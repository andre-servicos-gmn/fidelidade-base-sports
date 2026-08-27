"""Serviço de identidade: vínculo telefone <-> cliente.

Política de cadastro: o cliente pode se cadastrar pelo WhatsApp ANTES de
comprar (`register_customer_by_cpf`). Se o CPF ainda não existe na base, o
cadastro é criado com saldo zero e o cliente passa a acumular na próxima compra.
Se já existe (porque comprou no totem), o telefone é vinculado ao cadastro que
está lá — e o saldo dele aparece na hora.

`link_phone_to_cpf` mantém a política antiga (só vincula a CPF existente) e
segue disponível para quem precisar desse comportamento estrito.

Formato canônico do telefone
----------------------------
Apenas dígitos, com DDD, SEM código de país. Ex.: "11990000001". Entradas como
"+55 (11) 99000-0001" ou "5511990000001" são normalizadas para "11990000001"
(removemos não-dígitos e o prefixo de país 55, quando presente). Esse formato
canônico é usado tanto para gravar `Customer.phone` quanto como chave da sessão.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer, TermsAcceptance
from app.domain.phone import normalize_phone
from app.domain.security import hash_cpf
from app.services.customer_service import get_or_create_customer

# Reexportado de propósito: `normalize_phone` era definida aqui e vários módulos
# (inclusive os testes e o adapter da Evolution) a importam deste caminho.
__all__ = [
    "CustomerNotRegisteredError",
    "IdentityError",
    "PhoneAlreadyLinkedError",
    "find_customer_by_phone",
    "link_phone_to_cpf",
    "normalize_phone",
    "register_customer_by_cpf",
]


class IdentityError(Exception):
    """Base dos erros de identidade."""


class CustomerNotRegisteredError(IdentityError):
    """CPF não está na base (cliente ainda não comprou)."""


class PhoneAlreadyLinkedError(IdentityError):
    """O telefone já está vinculado a OUTRO cliente.

    Política: rejeitamos e orientamos o cliente a falar com o suporte. Não
    "roubamos" o telefone de um cadastro para outro automaticamente — isso
    poderia mascarar fraude ou erro de digitação de CPF.
    """


async def find_customer_by_phone(
    session: AsyncSession, phone: str
) -> Customer | None:
    """Busca o cliente já vinculado àquele telefone (ou None)."""
    phone_n = normalize_phone(phone)
    if not phone_n:
        return None
    result = await session.execute(
        select(Customer).where(Customer.phone == phone_n)
    )
    return result.scalar_one_or_none()


async def register_customer_by_cpf(
    session: AsyncSession,
    phone: str,
    cpf: str,
    terms_version: str,
) -> tuple[Customer, bool]:
    """Cadastra (ou encontra) o cliente do CPF e vincula o telefone.

    Retorna `(customer, criado_agora)`. `criado_agora=True` significa que o CPF
    não existia — o cliente entra com saldo zero e só acumula na próxima compra.

    Grava também o aceite do regulamento em `terms_acceptances`, na MESMA
    transação do cadastro: ou as duas coisas acontecem, ou nenhuma. Um cadastro
    sem prova de aceite não deveria existir.

    Erros:
      - `PhoneAlreadyLinkedError`: o telefone já pertence a outro cadastro.

    ATENÇÃO — limite conhecido: nada aqui prova que o CPF é de quem está
    digitando. Se o CPF já existir e tiver histórico, o telefone passa a ver
    aquele saldo. Os dígitos verificadores (validados na camada de conversa)
    barram o erro de digitação, não a má-fé. Uma verificação adicional para CPF
    com histórico é o próximo passo natural de segurança.
    """
    phone_n = normalize_phone(phone)
    if not phone_n:
        raise IdentityError("Telefone vazio.")

    cpf_hash_value = hash_cpf(cpf)
    existing = (
        await session.execute(
            select(Customer).where(Customer.cpf_hash == cpf_hash_value)
        )
    ).scalar_one_or_none()

    # Telefone já usado por OUTRO cadastro? Recusa antes de mexer em nada.
    other = (
        await session.execute(
            select(Customer.id).where(Customer.phone == phone_n)
        )
    ).scalar_one_or_none()
    if other is not None and (existing is None or other != existing.id):
        raise PhoneAlreadyLinkedError(phone_n)

    created = existing is None
    if existing is None:
        customer = await get_or_create_customer(session, cpf, phone=phone_n)
    else:
        customer = existing
        customer.phone = phone_n

    session.add(
        TermsAcceptance(
            id=uuid.uuid4(),
            customer_id=customer.id,
            phone=phone_n,
            terms_version=terms_version,
        )
    )

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise PhoneAlreadyLinkedError(phone_n) from exc

    return customer, created


async def link_phone_to_cpf(
    session: AsyncSession, phone: str, cpf: str
) -> Customer:
    """Vincula `phone` ao cliente do `cpf`. Commita em caso de sucesso.

    Erros:
      - `CustomerNotRegisteredError`: não existe cliente com aquele CPF.
      - `PhoneAlreadyLinkedError`: o telefone já pertence a outro cliente.
    """
    phone_n = normalize_phone(phone)
    cpf_hash_value = hash_cpf(cpf)

    customer = (
        await session.execute(
            select(Customer).where(Customer.cpf_hash == cpf_hash_value)
        )
    ).scalar_one_or_none()
    if customer is None:
        raise CustomerNotRegisteredError(cpf_hash_value)

    # Já vinculado a este mesmo cliente: idempotente.
    if customer.phone == phone_n:
        return customer

    # Telefone já usado por outro cadastro?
    other = (
        await session.execute(
            select(Customer.id).where(Customer.phone == phone_n)
        )
    ).scalar_one_or_none()
    if other is not None and other != customer.id:
        raise PhoneAlreadyLinkedError(phone_n)

    customer.phone = phone_n
    try:
        await session.commit()
    except IntegrityError as exc:
        # Corrida: o índice único parcial pegou um vínculo concorrente.
        await session.rollback()
        raise PhoneAlreadyLinkedError(phone_n) from exc

    return customer

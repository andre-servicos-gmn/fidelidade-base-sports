"""Serviço de clientes: buscar/criar por CPF, sem nunca guardar o CPF cru."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer
from app.domain.phone import normalize_phone
from app.domain.security import hash_cpf, mask_cpf


async def get_or_create_customer(
    session: AsyncSession,
    cpf: str,
    phone: str | None = None,
) -> Customer:
    """Retorna o cliente do CPF, criando-o se não existir.

    Busca por `cpf_hash` (HMAC determinístico). Ao criar, grava apenas o hash e
    a versão mascarada — o CPF cru NUNCA é persistido.

    TELEFONE: gravado SEMPRE no formato canônico (`normalize_phone`: só dígitos,
    com DDD, sem o 55). Isto é obrigatório, não cosmético — `find_customer_by_phone`
    (usado pelo webhook do WhatsApp) compara com o valor canônico. Gravar o que a
    TouchPay mandou (ex.: "+5511987654321") criaria um cliente que ganha pontos
    normalmente mas que o bot NUNCA reconhece: ele responderia como se fosse um
    desconhecido e pediria o CPF, sem erro nenhum no log.

    Concorrência: dois processos podem tentar criar o mesmo CPF ao mesmo tempo.
    A criação é feita dentro de um SAVEPOINT (`begin_nested`); se a constraint
    única de `cpf_hash` disparar, capturamos e relê-mos o registro já existente.
    """
    cpf_hash_value = hash_cpf(cpf)
    # String vazia vira NULL: o índice único parcial de `phone` só considera
    # valores não-nulos, e "" não é um telefone.
    phone_canonical = normalize_phone(phone) or None if phone else None

    existing = await session.execute(
        select(Customer).where(Customer.cpf_hash == cpf_hash_value)
    )
    customer = existing.scalar_one_or_none()
    if customer is not None:
        return customer

    customer = Customer(
        id=uuid.uuid4(),
        cpf_hash=cpf_hash_value,
        cpf_masked=mask_cpf(cpf),
        phone=phone_canonical,
    )
    try:
        async with session.begin_nested():
            session.add(customer)
            await session.flush()
    except IntegrityError:
        # Corrida: outro processo criou o mesmo CPF entre o SELECT e o INSERT.
        result = await session.execute(
            select(Customer).where(Customer.cpf_hash == cpf_hash_value)
        )
        customer = result.scalar_one()

    return customer

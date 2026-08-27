"""Serviço de afiliados: CRUD do cadastro de parceiros (professores/influencers).

Nesta fase é só gestão. A normalização do `code` (sempre MAIÚSCULAS) vive aqui
e na camada de schema, de modo que a unicidade do código seja case-insensitive
na prática. O INSERT/UPDATE roda dentro de um SAVEPOINT (`begin_nested`) para
capturar a violação da constraint única de `code` sem abortar a transação — o
mesmo padrão de `customer_service.get_or_create_customer`.

Convenção transacional: estas funções NÃO fazem commit. Quem chama (a rota)
commita. Em código duplicado, levantam `DuplicateAffiliateCodeError`, que a rota
traduz para HTTP 409.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Affiliate, AffiliateAttribution, AffiliateType


@dataclass(frozen=True)
class AffiliateStats:
    """Métricas agregadas de um afiliado.

    - `points`: pontos do CLIENTE gerados (métrica).
    - `affiliate_points`: pontos que o AFILIADO ganhou.
    """

    customers: int = 0
    purchases: int = 0
    points: int = 0
    affiliate_points: int = 0


def compute_affiliate_points(amount: Decimal, rate_percent: Decimal) -> int:
    """Pontos do afiliado = floor(valor_em_reais × taxa%/100).

    Base 1 real = 1 ponto; a taxa do professor é um percentual sobre isso.
    Floor segue a política de arredondamento do motor de scoring (o afiliado
    nunca recebe ponto fracionado).
    """
    raw = Decimal(amount) * Decimal(rate_percent) / Decimal(100)
    return math.floor(raw)


class DuplicateAffiliateCodeError(Exception):
    """Já existe um afiliado com este código."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Já existe um afiliado com o código: {code}")


def normalize_code(code: str) -> str:
    """Normaliza o código para a forma canônica (sem espaços, MAIÚSCULAS)."""
    return code.strip().upper()


async def list_affiliates(session: AsyncSession) -> list[Affiliate]:
    """Lista todos os afiliados, ordenados por nome."""
    rows = (
        await session.execute(select(Affiliate).order_by(Affiliate.name))
    ).scalars().all()
    return list(rows)


async def get_affiliate(
    session: AsyncSession, affiliate_id: uuid.UUID
) -> Affiliate | None:
    """Retorna o afiliado pelo id, ou None."""
    return await session.get(Affiliate, affiliate_id)


async def get_active_affiliate_by_code(
    session: AsyncSession, code: str
) -> Affiliate | None:
    """Retorna o afiliado ATIVO com aquele código (normalizado), ou None.

    Usado na captura via WhatsApp: só um afiliado ativo pode receber atribuição.
    """
    normalized = normalize_code(code)
    return (
        await session.execute(
            select(Affiliate).where(
                Affiliate.code == normalized,
                Affiliate.active.is_(True),
            )
        )
    ).scalar_one_or_none()


async def create_affiliate(
    session: AsyncSession,
    *,
    name: str,
    affiliate_type: AffiliateType,
    code: str,
    points_rate: Decimal = Decimal(0),
    contact: str | None = None,
    notes: str | None = None,
    active: bool = True,
) -> Affiliate:
    """Cria um afiliado. Levanta DuplicateAffiliateCodeError se o código colidir."""
    affiliate = Affiliate(
        id=uuid.uuid4(),
        name=name.strip(),
        affiliate_type=affiliate_type,
        code=normalize_code(code),
        points_rate=points_rate,
        contact=contact,
        notes=notes,
        active=active,
    )
    try:
        async with session.begin_nested():
            session.add(affiliate)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateAffiliateCodeError(affiliate.code) from exc
    return affiliate


async def update_affiliate(
    session: AsyncSession,
    affiliate: Affiliate,
    *,
    name: str,
    affiliate_type: AffiliateType,
    code: str,
    points_rate: Decimal,
    contact: str | None,
    notes: str | None,
    active: bool,
) -> Affiliate:
    """Atualiza (substituição completa). DuplicateAffiliateCodeError em colisão."""
    affiliate.name = name.strip()
    affiliate.affiliate_type = affiliate_type
    affiliate.code = normalize_code(code)
    affiliate.points_rate = points_rate
    affiliate.contact = contact
    affiliate.notes = notes
    affiliate.active = active
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateAffiliateCodeError(affiliate.code) from exc
    return affiliate


async def set_active(
    session: AsyncSession, affiliate: Affiliate, active: bool
) -> Affiliate:
    """Ativa/desativa o afiliado."""
    affiliate.active = active
    return affiliate


async def delete_affiliate(session: AsyncSession, affiliate: Affiliate) -> None:
    """Remove o afiliado (hard delete)."""
    await session.delete(affiliate)


# --------------------------------------------------------------------------- #
# Atribuição por compra                                                        #
# --------------------------------------------------------------------------- #
async def record_attribution(
    session: AsyncSession,
    *,
    affiliate_id: uuid.UUID,
    customer_id: uuid.UUID,
    source_reference: str,
    points: int,
    amount: Decimal = Decimal(0),
    affiliate_points: int = 0,
) -> AffiliateAttribution | None:
    """Atribui UMA compra a um afiliado. Retorna None se já atribuída.

    `points` = pontos do cliente (métrica); `amount` = valor da compra (R$) e
    `affiliate_points` = pontos do afiliado, gravados como snapshot.

    A unicidade de `source_reference` garante que a mesma compra não seja
    atribuída duas vezes. O INSERT roda num SAVEPOINT (`begin_nested`); se a
    constraint disparar, revertemos só o savepoint e retornamos None — idêntico
    ao padrão de `ledger_service.add_entry`. NÃO commita (quem chama commita).
    """
    attribution = AffiliateAttribution(
        id=uuid.uuid4(),
        affiliate_id=affiliate_id,
        customer_id=customer_id,
        source_reference=source_reference,
        points=points,
        amount=amount,
        affiliate_points=affiliate_points,
    )
    try:
        async with session.begin_nested():
            session.add(attribution)
            await session.flush()
    except IntegrityError:
        return None
    return attribution


async def get_affiliate_stats(
    session: AsyncSession,
) -> dict[uuid.UUID, AffiliateStats]:
    """Agrega por afiliado: clientes distintos, compras, pontos do cliente e do afiliado."""
    rows = (
        await session.execute(
            select(
                AffiliateAttribution.affiliate_id,
                func.count(func.distinct(AffiliateAttribution.customer_id)),
                func.count(),
                func.coalesce(func.sum(AffiliateAttribution.points), 0),
                func.coalesce(
                    func.sum(AffiliateAttribution.affiliate_points), 0
                ),
            ).group_by(AffiliateAttribution.affiliate_id)
        )
    ).all()
    return {
        affiliate_id: AffiliateStats(
            customers=int(customers),
            purchases=int(purchases),
            points=int(points),
            affiliate_points=int(affiliate_points),
        )
        for affiliate_id, customers, purchases, points, affiliate_points in rows
    }

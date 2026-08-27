"""Motor de regras de pontuação — módulo de domínio PURO.

Sem banco, sem TouchPay, sem I/O. Só cálculo determinístico e testável. Esta é
a fonte ÚNICA de verdade de "quantos pontos esta compra gera".

As regras são declarativas e serializáveis (representáveis como dict/JSON), pois
no futuro serão criadas por usuários de negócio num painel — nunca como código.
Adicionar um novo `RuleType` é só: (1) acrescentar o valor no enum, (2) tratar a
fase correspondente em `calculate_points`. Nada fora deste módulo precisa mudar.

--------------------------------------------------------------------------- #
PARÂMETROS POR TIPO DE REGRA (campo `Rule.params`)
--------------------------------------------------------------------------- #
- BASE                    {"points_per_real": float}
      Pontos base por real gasto. Regra global: aplica a TODOS os itens.
- CATEGORY_MULTIPLIER     {"category": str, "multiplier": float}
      Multiplica os pontos dos itens cuja `category` casa exatamente.
- PRODUCT_MULTIPLIER      {"product_id": int, "multiplier": float}
      Multiplica os pontos dos itens cujo `product_id` casa.
- CATEGORY_BONUS_PERCENT  {"category": str, "percent": float}
      Bônus de pontos (cashback) = percent% dos pontos ATUAIS do item.

--------------------------------------------------------------------------- #
ORDEM DE APLICAÇÃO (documentada e testável)
--------------------------------------------------------------------------- #
1. FILTRO: mantém só regras `active=True` cuja janela [valid_from, valid_until]
   contém `transaction_date` (limites inclusivos; bound nulo = sem limite;
   regra sem janela vale sempre).
2. ORDENAÇÃO: por `priority` ascendente (menor = mais prioritário). Empate de
   priority é desempatado por `id` (ordenação estável e determinística).
3. FASE BASE: estabelece os pontos base de cada item
   (pontos_item = item.total_price × points_per_real). Se houver MAIS DE UMA
   regra BASE ativa, vence a de MAIOR prioridade = MENOR `priority`
   (desempate por `id`). As demais BASE são ignoradas.
   Sem nenhuma BASE ativa → zero pontos (com aviso no breakdown).
4. FASE MULTIPLICADORES (CATEGORY_MULTIPLIER, PRODUCT_MULTIPLIER), em ordem de
   prioridade: cada multiplicador incide sobre os pontos ATUAIS do item. Quando
   vários multiplicadores atingem o mesmo item, eles SE MULTIPLICAM entre si
   (ex.: 2× e depois 3× ⇒ 6×). A parcela registrada no breakdown é o delta
   marginal: pontos_atuais × (multiplier − 1).
5. FASE BÔNUS (CATEGORY_BONUS_PERCENT), em ordem de prioridade: adiciona
   percent% dos pontos ATUAIS (já multiplicados) do item, como pontos extras.

Obs.: a FASE tem precedência sobre o número de `priority` entre tipos
diferentes — um bônus sempre é aplicado depois de qualquer multiplicador,
mesmo que tenha priority menor. Dentro de cada fase, `priority` manda.

--------------------------------------------------------------------------- #
ARREDONDAMENTO
--------------------------------------------------------------------------- #
Política: FLOOR no total final (o cliente nunca recebe pontos fracionados).
Antes do floor, o total bruto é normalizado com `round(…, 9)` para descartar
ruído de ponto flutuante (evita que 99.9999999 vire 99 indevidamente).
O breakdown inclui uma linha final de "ajuste de arredondamento" igual a
`total_points − total_bruto`, de modo que a soma das parcelas do breakdown
reconcilia exatamente com `total_points`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# Casas decimais usadas para limpar ruído de ponto flutuante antes do floor.
_FLOAT_PRECISION = 9


# --------------------------------------------------------------------------- #
# Estruturas de dados                                                          #
# --------------------------------------------------------------------------- #
class RuleType(str, Enum):
    """Tipos de regra suportados pelo motor."""

    BASE = "BASE"
    CATEGORY_MULTIPLIER = "CATEGORY_MULTIPLIER"
    PRODUCT_MULTIPLIER = "PRODUCT_MULTIPLIER"
    CATEGORY_BONUS_PERCENT = "CATEGORY_BONUS_PERCENT"


@dataclass(frozen=True)
class ScoringItem:
    """Item da transação já normalizado para o motor."""

    product_id: int
    category: str | None
    quantity: int
    unit_price: float
    total_price: float


@dataclass(frozen=True)
class ScoringContext:
    """Input completo do cálculo de pontos."""

    items: list[ScoringItem]
    total_amount: float
    transaction_date: datetime


@dataclass(frozen=True)
class Rule:
    """Regra declarativa de pontuação (serializável como dict/JSON)."""

    id: str
    name: str
    rule_type: RuleType
    priority: int = 0
    active: bool = True
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        """Constrói uma Rule a partir de um dict (ex.: vindo do painel/JSON).

        Datas podem vir como `datetime` ou string ISO-8601.
        """

        def _dt(value: Any) -> datetime | None:
            if value is None or isinstance(value, datetime):
                return value
            return datetime.fromisoformat(value)

        return cls(
            id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            rule_type=RuleType(data["rule_type"]),
            priority=int(data.get("priority", 0)),
            active=bool(data.get("active", True)),
            valid_from=_dt(data.get("valid_from")),
            valid_until=_dt(data.get("valid_until")),
            params=dict(data.get("params", {})),
        )


@dataclass
class ScoringResult:
    """Resultado do cálculo.

    - `total_points`: pontos finais (inteiro, floor do total bruto).
    - `breakdown`: parcelas que explicam a origem dos pontos (auditoria e
      explicação ao cliente). Cada item é um dict com chaves:
      `rule_id`, `rule_name`, `rule_type`, `points`, `detail`
      (+ `product_id`/`category` quando fizer sentido).
    - `raw_total`: total bruto (float) antes do floor — exposto para auditoria.
    """

    total_points: int
    breakdown: list[dict[str, Any]]
    raw_total: float


# --------------------------------------------------------------------------- #
# Helpers internos                                                             #
# --------------------------------------------------------------------------- #
def _is_within_window(rule: Rule, when: datetime) -> bool:
    """True se `when` está na janela de validade da regra (bounds inclusivos)."""
    if rule.valid_from is not None and when < rule.valid_from:
        return False
    if rule.valid_until is not None and when > rule.valid_until:
        return False
    return True


def _active_rules(rules: list[Rule], when: datetime) -> list[Rule]:
    """Filtra regras ativas e dentro da janela, já ordenadas por (priority, id)."""
    eligible = [
        r for r in rules if r.active and _is_within_window(r, when)
    ]
    return sorted(eligible, key=lambda r: (r.priority, r.id))


def _category_matches(item: ScoringItem, category: Any) -> bool:
    """Casamento de categoria. Item com categoria None nunca casa."""
    return item.category is not None and item.category == category


# --------------------------------------------------------------------------- #
# Motor                                                                        #
# --------------------------------------------------------------------------- #
def calculate_points(
    context: ScoringContext, rules: list[Rule]
) -> ScoringResult:
    """Calcula os pontos de uma transação dadas as regras ativas.

    Veja o docstring do módulo para a semântica completa de cada fase.
    """
    items = context.items
    when = context.transaction_date
    eligible = _active_rules(rules, when)

    # Pontos correntes por índice de item (float; floor só no total final).
    points: list[float] = [0.0] * len(items)
    breakdown: list[dict[str, Any]] = []

    # --- Fase 1: BASE ----------------------------------------------------- #
    base_rules = [r for r in eligible if r.rule_type is RuleType.BASE]
    if not base_rules:
        breakdown.append(
            {
                "rule_id": None,
                "rule_name": "Sem regra BASE",
                "rule_type": "WARNING",
                "points": 0.0,
                "detail": (
                    "Nenhuma regra BASE ativa na data da transação: "
                    "nenhum ponto base foi gerado."
                ),
            }
        )
        return ScoringResult(total_points=0, breakdown=breakdown, raw_total=0.0)

    # `eligible` já está ordenado por (priority, id); a primeira BASE é a de
    # maior prioridade (menor priority), com desempate determinístico por id.
    base_rule = base_rules[0]
    points_per_real = float(base_rule.params.get("points_per_real", 0.0))
    for i, item in enumerate(items):
        base_points = item.total_price * points_per_real
        points[i] = base_points
        breakdown.append(
            {
                "rule_id": base_rule.id,
                "rule_name": base_rule.name,
                "rule_type": RuleType.BASE.value,
                "product_id": item.product_id,
                "category": item.category,
                "points": base_points,
                "detail": (
                    f"R$ {item.total_price:.2f} × {points_per_real} "
                    f"pt/R$ = {base_points:.4f} pts"
                ),
            }
        )

    # --- Fase 2: multiplicadores ----------------------------------------- #
    multiplier_types = (
        RuleType.CATEGORY_MULTIPLIER,
        RuleType.PRODUCT_MULTIPLIER,
    )
    for rule in (r for r in eligible if r.rule_type in multiplier_types):
        multiplier = float(rule.params.get("multiplier", 1.0))
        for i, item in enumerate(items):
            if rule.rule_type is RuleType.CATEGORY_MULTIPLIER:
                matched = _category_matches(item, rule.params.get("category"))
            else:  # PRODUCT_MULTIPLIER
                matched = item.product_id == rule.params.get("product_id")
            if not matched:
                continue

            delta = points[i] * (multiplier - 1.0)
            points[i] += delta  # equivale a points[i] *= multiplier
            breakdown.append(
                {
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "rule_type": rule.rule_type.value,
                    "product_id": item.product_id,
                    "category": item.category,
                    "points": delta,
                    "detail": (
                        f"×{multiplier} no item (delta {delta:+.4f} pts)"
                    ),
                }
            )

    # --- Fase 3: bônus percentuais --------------------------------------- #
    for rule in (
        r for r in eligible if r.rule_type is RuleType.CATEGORY_BONUS_PERCENT
    ):
        percent = float(rule.params.get("percent", 0.0))
        for i, item in enumerate(items):
            if not _category_matches(item, rule.params.get("category")):
                continue
            delta = points[i] * (percent / 100.0)
            points[i] += delta
            breakdown.append(
                {
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "rule_type": rule.rule_type.value,
                    "product_id": item.product_id,
                    "category": item.category,
                    "points": delta,
                    "detail": (
                        f"+{percent}% de bônus no item (+{delta:.4f} pts)"
                    ),
                }
            )

    # --- Total + arredondamento ------------------------------------------ #
    raw_total = sum(entry["points"] for entry in breakdown)
    # Normaliza ruído de float antes do floor (ver docstring do módulo).
    total_points = math.floor(round(raw_total, _FLOAT_PRECISION))

    # Linha de reconciliação: faz a soma do breakdown bater com total_points.
    adjustment = total_points - raw_total
    breakdown.append(
        {
            "rule_id": None,
            "rule_name": "Arredondamento (floor)",
            "rule_type": "ROUNDING",
            "points": adjustment,
            "detail": (
                f"floor({raw_total:.4f}) = {total_points} "
                f"(ajuste {adjustment:+.4f})"
            ),
        }
    )

    return ScoringResult(
        total_points=total_points,
        breakdown=breakdown,
        raw_total=raw_total,
    )

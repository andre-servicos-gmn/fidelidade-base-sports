/**
 * Tradução de termos técnicos para linguagem de negócio.
 * O painel NUNCA expõe rule_type/params/points_cost cru.
 */
import type {
  AffiliateType,
  CouponStatus,
  DiscountType,
  EntryType,
  Rule,
  RuleType,
} from "./api";

export const AFFILIATE_TYPE_LABELS: Record<AffiliateType, string> = {
  PROFESSOR: "Professor",
  INFLUENCER: "Influencer",
};

export function humanizeAffiliateType(type: AffiliateType): string {
  return AFFILIATE_TYPE_LABELS[type] ?? type;
}

/** Formata um percentual, sem casas desnecessárias (ex.: 50, 12,5). */
export function formatPercent(value: number | string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${trimNum(n)}%`;
}

export const RULE_TYPE_LABELS: Record<RuleType, string> = {
  BASE: "Pontos por real",
  CATEGORY_MULTIPLIER: "Multiplicador por categoria",
  PRODUCT_MULTIPLIER: "Multiplicador por produto",
  CATEGORY_BONUS_PERCENT: "Bônus percentual por categoria",
};

export function humanizeRuleType(type: RuleType): string {
  return RULE_TYPE_LABELS[type] ?? type;
}

/** Resumo legível dos parâmetros de uma regra (ex.: "Categoria Raquetes ganha 2x pontos"). */
export function summarizeRule(rule: Rule): string {
  const p = rule.params as Record<string, unknown>;
  switch (rule.rule_type) {
    case "BASE":
      return `${num(p.points_per_real)} ponto(s) por R$ 1,00`;
    case "CATEGORY_MULTIPLIER":
      return `Categoria "${p.category}" ganha ${num(p.multiplier)}x pontos`;
    case "PRODUCT_MULTIPLIER":
      return `Produto #${p.product_id} ganha ${num(p.multiplier)}x pontos`;
    case "CATEGORY_BONUS_PERCENT":
      return `Categoria "${p.category}" ganha +${num(p.percent)}% de pontos`;
    default:
      return "";
  }
}

const ENTRY_LABELS: Record<EntryType, string> = {
  EARN: "Ganhou pontos",
  REDEEM: "Resgatou cupom",
  EXPIRE: "Pontos expiraram",
  ADJUST: "Ajuste manual",
};
export function humanizeEntryType(type: EntryType): string {
  return ENTRY_LABELS[type] ?? type;
}

const STATUS_LABELS: Record<CouponStatus, string> = {
  AVAILABLE: "Disponível",
  ALLOCATED: "Resgatado",
  USED: "Utilizado",
  EXPIRED: "Expirado",
};
export function humanizeStatus(status: CouponStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function formatDiscount(
  type: DiscountType | string,
  value: number | string
): string {
  const n = Number(value);
  if (type === "PERCENTAGE") return `${trimNum(n)}% de desconto`;
  return `R$ ${n.toFixed(2).replace(".", ",")}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("pt-BR");
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatPoints(n: number): string {
  return new Intl.NumberFormat("pt-BR").format(n);
}

export function formatBRL(value: number | string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `R$ ${n.toFixed(2).replace(".", ",")}`;
}

function num(v: unknown): string {
  return trimNum(Number(v ?? 0));
}
function trimNum(n: number): string {
  if (Number.isNaN(n)) return "0";
  return Number.isInteger(n) ? String(n) : String(n).replace(".", ",");
}

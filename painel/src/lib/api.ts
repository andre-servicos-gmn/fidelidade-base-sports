/**
 * Cliente HTTP centralizado.
 * - A URL da API vem de VITE_API_URL (NUNCA hardcoded).
 * - Injeta Authorization: Bearer <token> automaticamente.
 * - Em 401 (token expirado), limpa o token e dispara 'auth:unauthorized'
 *   (o AuthProvider escuta e redireciona pro login).
 */

const API_URL = (
  import.meta.env.VITE_API_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

const TOKEN_KEY = "bs_admin_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function extractError(body: unknown): string {
  if (!body) return "Erro inesperado. Tente novamente.";
  if (typeof body === "string") return body;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => (e && typeof e === "object" ? (e as any).msg : String(e)))
      .filter(Boolean)
      .join("; ");
  }
  return "Erro inesperado. Tente novamente.";
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(API_URL + path, { ...options, headers });

  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("auth:unauthorized"));
    throw new ApiError("Sessão expirada. Faça login novamente.", 401);
  }
  if (res.status === 204) return null as T;

  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) throw new ApiError(extractError(body), res.status);
  return body as T;
}

/* ----- Tipos da API ------------------------------------------------------ */
export type RuleType =
  | "BASE"
  | "CATEGORY_MULTIPLIER"
  | "PRODUCT_MULTIPLIER"
  | "CATEGORY_BONUS_PERCENT";

export interface Rule {
  id: string;
  name: string;
  rule_type: RuleType;
  priority: number;
  active: boolean;
  valid_from: string | null;
  valid_until: string | null;
  params: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface RulePayload {
  name: string;
  rule_type: RuleType;
  priority: number;
  active: boolean;
  valid_from: string | null;
  valid_until: string | null;
  params: Record<string, unknown>;
}

export type CouponStatus = "AVAILABLE" | "ALLOCATED" | "USED" | "EXPIRED";
export type DiscountType = "FIXED" | "PERCENTAGE";

export interface Coupon {
  id: string;
  code: string;
  discount_type: DiscountType;
  discount_value: string;
  points_cost: number;
  min_order_value: string | null;
  status: CouponStatus;
  allocated_to_customer_id: string | null;
  allocated_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface RewardSummaryRow {
  discount_type: string;
  discount_value: number;
  points_cost: number;
  min_order_value: number | null;
  available: number;
  allocated: number;
  used: number;
  expired: number;
}

export interface CouponList {
  items: Coupon[];
  summary: RewardSummaryRow[];
}

export interface CouponBatchResult {
  created: string[];
  skipped: string[];
  warning: string;
}

export interface Customer {
  id: string;
  cpf_masked: string;
  phone_masked: string | null;
  balance: number;
  created_at: string;
}

export interface CustomerList {
  items: Customer[];
  total: number;
  page: number;
  page_size: number;
}

export type EntryType = "EARN" | "REDEEM" | "EXPIRE" | "ADJUST";

export interface LedgerEntry {
  sequence: number;
  created_at: string;
  entry_type: EntryType;
  points: number;
  balance_after: number;
  description: string | null;
}

export interface CustomerCoupon {
  code: string;
  discount_type: DiscountType;
  discount_value: number;
  points_cost: number;
  status: CouponStatus;
  allocated_at: string | null;
  expires_at: string | null;
}

export type AffiliateType = "PROFESSOR" | "INFLUENCER";

export interface Affiliate {
  id: string;
  name: string;
  affiliate_type: AffiliateType;
  code: string;
  points_rate: string;
  contact: string | null;
  notes: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AffiliatePayload {
  name: string;
  affiliate_type: AffiliateType;
  code: string;
  points_rate: number;
  contact: string | null;
  notes: string | null;
  active: boolean;
}

export interface AffiliateStats {
  affiliate_id: string;
  customers: number;
  purchases: number;
  points: number;
  affiliate_points: number;
}

/* ----- Endpoints --------------------------------------------------------- */
export async function login(
  username: string,
  password: string
): Promise<{ access_token: string; token_type: string }> {
  const res = await fetch(API_URL + "/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (res.status === 401) {
    throw new ApiError("Usuário ou senha inválidos.", 401);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(extractError(body), res.status);
  }
  return res.json();
}

export const api = {
  // Regras
  listRules: () => request<Rule[]>("/admin/rules"),
  createRule: (payload: RulePayload) =>
    request<Rule>("/admin/rules", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateRule: (id: string, payload: RulePayload) =>
    request<Rule>(`/admin/rules/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  toggleRule: (id: string) =>
    request<Rule>(`/admin/rules/${id}/toggle`, { method: "PATCH" }),
  deleteRule: (id: string) =>
    request<null>(`/admin/rules/${id}`, { method: "DELETE" }),

  // Cupons
  listCoupons: (query = "") => request<CouponList>(`/admin/coupons${query}`),
  createCoupons: (payload: {
    codes: string[];
    discount_type: DiscountType;
    discount_value: string;
    points_cost: number;
    min_order_value: string | null;
    expires_at: string | null;
  }) =>
    request<CouponBatchResult>("/admin/coupons", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteCoupon: (id: string) =>
    request<null>(`/admin/coupons/${id}`, { method: "DELETE" }),

  // Clientes
  listCustomers: (page = 1, pageSize = 25) =>
    request<CustomerList>(
      `/admin/customers/list?page=${page}&page_size=${pageSize}`
    ),
  findCustomer: (cpf: string) =>
    request<Customer>(`/admin/customers?cpf=${encodeURIComponent(cpf)}`),
  customerLedger: (id: string) =>
    request<LedgerEntry[]>(`/admin/customers/${id}/ledger`),
  customerCoupons: (id: string) =>
    request<CustomerCoupon[]>(`/admin/customers/${id}/coupons`),

  // Afiliados
  listAffiliates: () => request<Affiliate[]>("/admin/affiliates"),
  affiliateStats: () =>
    request<AffiliateStats[]>("/admin/affiliates/stats"),
  createAffiliate: (payload: AffiliatePayload) =>
    request<Affiliate>("/admin/affiliates", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateAffiliate: (id: string, payload: AffiliatePayload) =>
    request<Affiliate>(`/admin/affiliates/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  toggleAffiliate: (id: string) =>
    request<Affiliate>(`/admin/affiliates/${id}/toggle`, { method: "PATCH" }),
  deleteAffiliate: (id: string) =>
    request<null>(`/admin/affiliates/${id}`, { method: "DELETE" }),
};

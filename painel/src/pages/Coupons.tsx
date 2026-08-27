import { useEffect, useState } from "react";
import {
  ApiError,
  api,
  type CouponList,
  type CouponStatus,
  type DiscountType,
} from "../lib/api";
import {
  formatBRL,
  formatDiscount,
  formatPoints,
  humanizeStatus,
} from "../lib/format";
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  Field,
  Loading,
  Modal,
  Select,
  TextInput,
  Textarea,
} from "../components/ui";

const LOW_STOCK = 5; // abaixo disso, alerta em rosa

const STATUS_TONE: Record<CouponStatus, "green" | "pink" | "gray" | "amber"> = {
  AVAILABLE: "green",
  ALLOCATED: "pink",
  USED: "gray",
  EXPIRED: "amber",
};

export function CouponsPage() {
  const [data, setData] = useState<CouponList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [createOpen, setCreateOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const query = statusFilter ? `?status=${statusFilter}` : "";
      setData(await api.listCoupons(query));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao carregar.");
    }
  }
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  async function remove(id: string, code: string) {
    if (!window.confirm(`Remover o cupom ${code} do pool?`)) return;
    setBusyId(id);
    try {
      await api.deleteCoupon(id);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Não foi possível remover o cupom."
      );
    } finally {
      setBusyId(null);
    }
  }

  const availableTiers = (data?.summary ?? []).filter(
    (r) => r.discount_type === "FIXED"
  );

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Cupons</h1>
          <p>Pool de cupons disponíveis para resgate pelos clientes.</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>+ Cadastrar cupons</Button>
      </div>

      {error && (
        <div style={{ marginBottom: 16 }}>
          <Banner tone="warn" icon="!">
            {error}
          </Banner>
        </div>
      )}

      {/* Resumo: quantos cupons disponíveis por faixa */}
      {data === null ? (
        <div className="card" style={{ marginBottom: 24 }}>
          <Loading label="Carregando resumo…" />
        </div>
      ) : (
        <div className="summary-grid">
          {availableTiers.length === 0 ? (
            <div className="summary-card">
              <div className="summary-label">Cupons disponíveis</div>
              <div className="summary-value">0</div>
              <div className="summary-meta">Nenhuma faixa cadastrada</div>
            </div>
          ) : (
            availableTiers.map((row) => {
              const low = row.available < LOW_STOCK;
              return (
                <div
                  key={`${row.discount_value}-${row.points_cost}`}
                  className={`summary-card ${low ? "low" : ""}`}
                >
                  <div className="summary-label">
                    {formatDiscount("FIXED", row.discount_value)}
                  </div>
                  <div className="summary-value">{row.available}</div>
                  <div className="summary-meta">
                    disponíveis · custa {formatPoints(row.points_cost)} pts
                    {row.min_order_value != null &&
                      ` · mín. ${formatBRL(row.min_order_value)}`}
                    {low && " · acabando!"}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* Filtro */}
      <div className="toolbar">
        <span className="subtle">Filtrar por status:</span>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          style={{ width: 200 }}
        >
          <option value="">Todos</option>
          <option value="AVAILABLE">Disponível</option>
          <option value="ALLOCATED">Resgatado</option>
          <option value="USED">Utilizado</option>
          <option value="EXPIRED">Expirado</option>
        </Select>
      </div>

      {/* Lista */}
      {data === null ? (
        <div className="card">
          <Loading label="Carregando cupons…" />
        </div>
      ) : data.items.length === 0 ? (
        <div className="card">
          <EmptyState
            title="Nenhum cupom encontrado"
            message="Cadastre cupons (em lote) para liberá-los ao resgate."
            action={
              <Button onClick={() => setCreateOpen(true)}>
                + Cadastrar cupons
              </Button>
            }
          />
        </div>
      ) : (
        <div className="card table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Código</th>
                <th>Recompensa</th>
                <th className="right">Custo</th>
                <th>Status</th>
                <th className="right">Ações</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.id}>
                  <td className="strong mono">{c.code}</td>
                  <td>
                    {formatDiscount(c.discount_type, c.discount_value)}
                    {c.min_order_value != null && (
                      <div className="subtle">
                        válido acima de {formatBRL(c.min_order_value)}
                      </div>
                    )}
                  </td>
                  <td className="right num">{formatPoints(c.points_cost)} pts</td>
                  <td>
                    <Badge tone={STATUS_TONE[c.status]}>
                      {humanizeStatus(c.status)}
                    </Badge>
                  </td>
                  <td className="right">
                    {c.status === "AVAILABLE" ? (
                      <button
                        className="btn-danger-link"
                        onClick={() => remove(c.id, c.code)}
                        disabled={busyId === c.id}
                      >
                        remover
                      </button>
                    ) : (
                      <span className="subtle">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {createOpen && (
        <CreateCouponsModal
          onClose={() => setCreateOpen(false)}
          onSaved={() => {
            setCreateOpen(false);
            load();
          }}
        />
      )}
    </>
  );
}

/* -------------------------------------------------------------------------- */
function CreateCouponsModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [codesText, setCodesText] = useState("");
  const [discountType, setDiscountType] = useState<DiscountType>("FIXED");
  const [discountValue, setDiscountValue] = useState("10");
  const [pointsCost, setPointsCost] = useState("500");
  const [minOrder, setMinOrder] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [result, setResult] = useState<{ created: number; skipped: number } | null>(
    null
  );
  const [saving, setSaving] = useState(false);

  async function submit() {
    setApiError(null);
    const codes = codesText
      .split("\n")
      .map((c) => c.trim())
      .filter(Boolean);

    const e: Record<string, string> = {};
    if (codes.length === 0) e.codes = "Cole pelo menos um código (um por linha).";
    const value = Number(discountValue.replace(",", "."));
    if (!Number.isFinite(value) || value <= 0)
      e.discountValue = "Informe um valor maior que zero.";
    const cost = parseInt(pointsCost, 10);
    if (!Number.isInteger(cost) || cost <= 0)
      e.pointsCost = "Informe o custo em pontos.";

    let minValue: string | null = null;
    const minTrim = minOrder.trim();
    if (minTrim) {
      const m = Number(minTrim.replace(",", "."));
      if (!Number.isFinite(m) || m <= 0)
        e.minOrder = "Se informar, o mínimo deve ser maior que zero.";
      else minValue = m.toFixed(2);
    }
    setErrors(e);
    if (Object.keys(e).length > 0) return;

    setSaving(true);
    try {
      const res = await api.createCoupons({
        codes,
        discount_type: discountType,
        discount_value: value.toFixed(2),
        points_cost: cost,
        min_order_value: minValue,
      });
      setResult({ created: res.created.length, skipped: res.skipped.length });
    } catch (err) {
      setApiError(
        err instanceof ApiError ? err.message : "Não foi possível cadastrar."
      );
    } finally {
      setSaving(false);
    }
  }

  if (result) {
    return (
      <Modal
        title="Cupons cadastrados"
        onClose={onSaved}
        footer={<Button onClick={onSaved}>Concluir</Button>}
      >
        <Banner tone="info" icon="✓">
          {result.created} cupom(ns) cadastrado(s).
          {result.skipped > 0 &&
            ` ${result.skipped} já existia(m) e foram ignorados.`}
        </Banner>
      </Modal>
    );
  }

  return (
    <Modal
      title="Cadastrar cupons em lote"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancelar
          </Button>
          <Button onClick={submit} loading={saving}>
            Cadastrar
          </Button>
        </>
      }
    >
      <Banner tone="warn" icon="!">
        <strong>Importante:</strong> estes códigos precisam ter sido criados
        ANTES no painel da TouchPay/AMLabs. O sistema só registra o código aqui —
        ele <strong>não cria</strong> o cupom no totem. Um código que não exista
        no TouchPay não funcionará para o cliente.
      </Banner>

      <div style={{ height: 16 }} />

      <Field
        label="Códigos dos cupons"
        hint="Um código por linha. Cole o lote gerado no TouchPay."
        error={errors.codes}
      >
        <Textarea
          value={codesText}
          onChange={(e) => setCodesText(e.target.value)}
          placeholder={"BASE-R10-0001\nBASE-R10-0002\nBASE-R10-0003"}
        />
      </Field>

      <div className="form-row">
        <Field label="Tipo de desconto">
          <Select
            value={discountType}
            onChange={(e) => setDiscountType(e.target.value as DiscountType)}
          >
            <option value="FIXED">Valor fixo (R$)</option>
            <option value="PERCENTAGE">Percentual (%)</option>
          </Select>
        </Field>
        <Field
          label={discountType === "FIXED" ? "Valor (R$)" : "Percentual (%)"}
          error={errors.discountValue}
        >
          <TextInput
            inputMode="decimal"
            value={discountValue}
            onChange={(e) => setDiscountValue(e.target.value)}
            invalid={!!errors.discountValue}
          />
        </Field>
      </div>

      <div className="form-row">
        <Field
          label="Custo em pontos"
          hint="Pontos que o cliente gasta para resgatar."
          error={errors.pointsCost}
        >
          <TextInput
            inputMode="numeric"
            value={pointsCost}
            onChange={(e) => setPointsCost(e.target.value)}
            invalid={!!errors.pointsCost}
          />
        </Field>
        <Field
          label="Pedido mínimo (R$) — opcional"
          hint="A compra mínima exigida no totem. Deixe vazio se não houver."
          error={errors.minOrder}
        >
          <TextInput
            inputMode="decimal"
            value={minOrder}
            onChange={(e) => setMinOrder(e.target.value)}
            placeholder="Ex.: 50"
            invalid={!!errors.minOrder}
          />
        </Field>
      </div>

      {apiError && (
        <Banner tone="warn" icon="!">
          {apiError}
        </Banner>
      )}
    </Modal>
  );
}

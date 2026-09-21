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
  formatDate,
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
import { IconPlus } from "../components/icons";

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
        <Button icon={<IconPlus />} onClick={() => setCreateOpen(true)}>
          Cadastrar cupons
        </Button>
      </div>

      {error && (
        <div className="mb-4">
          <Banner tone="warn">
            {error}
          </Banner>
        </div>
      )}

      {/* Resumo: quantos cupons disponíveis por faixa */}
      {data === null ? (
        <div className="card mb-5">
          <Loading label="Carregando resumo…" />
        </div>
      ) : (
        <div className="summary-grid">
          {availableTiers.length === 0 ? (
            <div className="summary-card">
              <div className="summary-top">
                <span className="summary-label">Cupons disponíveis</span>
              </div>
              <div className="summary-value">0</div>
              <div className="summary-meta">Nenhuma faixa cadastrada</div>
            </div>
          ) : (
            availableTiers.map((row) => {
              const low = row.available < LOW_STOCK;
              const total =
                row.available + row.allocated + row.used + row.expired;
              const pct = total > 0 ? (row.available / total) * 100 : 0;
              return (
                <div
                  key={`${row.discount_value}-${row.points_cost}`}
                  className={`summary-card ${low ? "low" : ""}`}
                >
                  <div className="summary-top">
                    <span className="summary-label">
                      {formatDiscount("FIXED", row.discount_value)}
                    </span>
                    {low && <span className="summary-flag">acabando!</span>}
                  </div>
                  <div className="summary-value">
                    {row.available}
                    <span className="summary-unit">disponíveis</span>
                  </div>
                  <div
                    className="summary-bar"
                    role="img"
                    aria-label={`${row.available} de ${total} ainda disponíveis`}
                  >
                    <span style={{ width: `${pct}%` }} />
                  </div>
                  <div className="summary-meta">
                    custa {formatPoints(row.points_cost)} pts
                    {row.min_order_value != null &&
                      ` · mín. ${formatBRL(row.min_order_value)}`}
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
          className="select-inline"
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
              <Button icon={<IconPlus />} onClick={() => setCreateOpen(true)}>
                Cadastrar cupons
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
                  <td>
                    <span className="code-chip">{c.code}</span>
                  </td>
                  <td>
                    {formatDiscount(c.discount_type, c.discount_value)}
                    {c.min_order_value != null && (
                      <div className="subtle">
                        válido acima de {formatBRL(c.min_order_value)}
                      </div>
                    )}
                    {c.expires_at != null && (
                      <div className="subtle">
                        vence em {formatDate(c.expires_at)}
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
/** Hoje no fuso de quem está usando o painel, como "AAAA-MM-DD" (o valor do input date). */
function todayISODate(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

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
  const [expiresOn, setExpiresOn] = useState("");
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

    // Validade do cupom REAL na TouchPay. Vale até o fim do dia escolhido, no
    // horário de Brasília (sem horário de verão desde 2019, então -03:00 fixo).
    // Vazio = sem validade cadastrada: no resgate o cliente recebe o prazo
    // padrão de 30 dias, que pode não bater com o cupom real.
    let expiresAt: string | null = null;
    if (expiresOn) {
      if (expiresOn < todayISODate())
        e.expiresOn = "A validade não pode estar no passado.";
      else expiresAt = `${expiresOn}T23:59:59-03:00`;
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
        expires_at: expiresAt,
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
        <Banner tone="info" icon="check">
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
      <Banner tone="warn">
        <strong>Importante:</strong> estes códigos precisam ter sido criados
        ANTES no painel da TouchPay/AMLabs. O sistema só registra o código aqui —
        ele <strong>não cria</strong> o cupom no totem. Um código que não exista
        no TouchPay não funcionará para o cliente.
      </Banner>

      <div className="gap-4" />

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

      <Field
        label="Validade — recomendado"
        hint="A mesma data de vencimento do cupom na TouchPay. É a que o cliente recebe no resgate. Sem ela, ele recebe 30 dias, que pode não bater com o cupom real."
        error={errors.expiresOn}
      >
        <TextInput
          type="date"
          min={todayISODate()}
          value={expiresOn}
          onChange={(e) => setExpiresOn(e.target.value)}
          invalid={!!errors.expiresOn}
        />
      </Field>

      {apiError && (
        <Banner tone="warn">
          {apiError}
        </Banner>
      )}
    </Modal>
  );
}

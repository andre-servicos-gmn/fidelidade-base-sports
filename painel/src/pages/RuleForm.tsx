import { useState } from "react";
import {
  ApiError,
  api,
  type Rule,
  type RulePayload,
  type RuleType,
} from "../lib/api";
import { RULE_TYPE_LABELS } from "../lib/format";
import {
  Banner,
  Button,
  Field,
  Modal,
  Select,
  TextInput,
  Toggle,
} from "../components/ui";

const TYPE_OPTIONS: { value: RuleType; label: string; help: string }[] = [
  {
    value: "BASE",
    label: RULE_TYPE_LABELS.BASE,
    help: "Quantos pontos o cliente ganha por cada R$ 1,00 gasto.",
  },
  {
    value: "CATEGORY_MULTIPLIER",
    label: RULE_TYPE_LABELS.CATEGORY_MULTIPLIER,
    help: "Multiplica os pontos dos itens de uma categoria (ex.: Raquetes em dobro).",
  },
  {
    value: "PRODUCT_MULTIPLIER",
    label: RULE_TYPE_LABELS.PRODUCT_MULTIPLIER,
    help: "Multiplica os pontos de um produto específico.",
  },
  {
    value: "CATEGORY_BONUS_PERCENT",
    label: RULE_TYPE_LABELS.CATEGORY_BONUS_PERCENT,
    help: "Dá um bônus percentual de pontos para uma categoria.",
  },
];

interface Props {
  rule?: Rule | null;
  onClose: () => void;
  onSaved: () => void;
}

export function RuleForm({ rule, onClose, onSaved }: Props) {
  const editing = !!rule;
  const p = (rule?.params ?? {}) as Record<string, unknown>;

  const [name, setName] = useState(rule?.name ?? "");
  const [ruleType, setRuleType] = useState<RuleType>(
    rule?.rule_type ?? "BASE"
  );
  const [pointsPerReal, setPointsPerReal] = useState(
    rule ? String(p.points_per_real ?? "") : "1"
  );
  const [category, setCategory] = useState(String(p.category ?? ""));
  const [multiplier, setMultiplier] = useState(String(p.multiplier ?? "2"));
  const [productId, setProductId] = useState(String(p.product_id ?? ""));
  const [percent, setPercent] = useState(String(p.percent ?? "10"));
  const [priority, setPriority] = useState(String(rule?.priority ?? 100));

  const [hasValidity, setHasValidity] = useState(
    !!(rule?.valid_from || rule?.valid_until)
  );
  const [validFrom, setValidFrom] = useState(
    rule?.valid_from ? rule.valid_from.slice(0, 10) : ""
  );
  const [validUntil, setValidUntil] = useState(
    rule?.valid_until ? rule.valid_until.slice(0, 10) : ""
  );

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function validate(): { ok: boolean; params: Record<string, unknown> } {
    const e: Record<string, string> = {};
    if (!name.trim()) e.name = "Dê um nome para a regra.";

    const params: Record<string, unknown> = {};
    const positive = (v: string) => {
      const n = Number(v.replace(",", "."));
      return Number.isFinite(n) && n > 0 ? n : null;
    };

    if (ruleType === "BASE") {
      const n = positive(pointsPerReal);
      if (n === null) e.pointsPerReal = "Informe um valor maior que zero.";
      else params.points_per_real = n;
    } else if (ruleType === "CATEGORY_MULTIPLIER") {
      if (!category.trim()) e.category = "Informe a categoria.";
      const n = positive(multiplier);
      if (n === null) e.multiplier = "O multiplicador deve ser maior que zero.";
      params.category = category.trim();
      if (n !== null) params.multiplier = n;
    } else if (ruleType === "PRODUCT_MULTIPLIER") {
      const id = parseInt(productId, 10);
      if (!Number.isInteger(id) || id <= 0)
        e.productId = "Informe o código (número) do produto.";
      else params.product_id = id;
      const n = positive(multiplier);
      if (n === null) e.multiplier = "O multiplicador deve ser maior que zero.";
      else params.multiplier = n;
    } else if (ruleType === "CATEGORY_BONUS_PERCENT") {
      if (!category.trim()) e.category = "Informe a categoria.";
      else params.category = category.trim();
      const n = positive(percent);
      if (n === null) e.percent = "O percentual deve ser maior que zero.";
      else params.percent = n;
    }

    if (hasValidity && validFrom && validUntil && validFrom > validUntil) {
      e.validUntil = "A data final não pode ser antes da inicial.";
    }

    setErrors(e);
    return { ok: Object.keys(e).length === 0, params };
  }

  async function onSubmit() {
    setApiError(null);
    const { ok, params } = validate();
    if (!ok) return;

    const payload: RulePayload = {
      name: name.trim(),
      rule_type: ruleType,
      priority: parseInt(priority, 10) || 0,
      active: rule?.active ?? true,
      valid_from:
        hasValidity && validFrom ? `${validFrom}T00:00:00` : null,
      valid_until:
        hasValidity && validUntil ? `${validUntil}T23:59:59` : null,
      params,
    };

    setSaving(true);
    try {
      if (editing && rule) await api.updateRule(rule.id, payload);
      else await api.createRule(payload);
      onSaved();
    } catch (err) {
      setApiError(
        err instanceof ApiError ? err.message : "Não foi possível salvar."
      );
    } finally {
      setSaving(false);
    }
  }

  const selectedHelp = TYPE_OPTIONS.find((o) => o.value === ruleType)?.help;

  return (
    <Modal
      title={editing ? "Editar regra" : "Nova regra"}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancelar
          </Button>
          <Button onClick={onSubmit} loading={saving}>
            {editing ? "Salvar alterações" : "Criar regra"}
          </Button>
        </>
      }
    >
      <Field label="Nome da regra" error={errors.name}>
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Ex.: Raquetes em dobro"
          invalid={!!errors.name}
        />
      </Field>

      <Field label="Tipo de regra" hint={selectedHelp}>
        <Select
          value={ruleType}
          onChange={(e) => setRuleType(e.target.value as RuleType)}
          disabled={editing}
        >
          {TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      </Field>

      {/* Campos específicos por tipo */}
      {ruleType === "BASE" && (
        <Field
          label="Pontos por R$ 1,00"
          hint="Ex.: 1 = um ponto a cada real gasto."
          error={errors.pointsPerReal}
        >
          <TextInput
            inputMode="decimal"
            value={pointsPerReal}
            onChange={(e) => setPointsPerReal(e.target.value)}
            invalid={!!errors.pointsPerReal}
          />
        </Field>
      )}

      {(ruleType === "CATEGORY_MULTIPLIER" ||
        ruleType === "CATEGORY_BONUS_PERCENT") && (
        <Field label="Categoria" error={errors.category}>
          <TextInput
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="Ex.: Raquetes"
            invalid={!!errors.category}
          />
        </Field>
      )}

      {ruleType === "PRODUCT_MULTIPLIER" && (
        <Field
          label="Código do produto"
          hint="O número do produto no TouchPay."
          error={errors.productId}
        >
          <TextInput
            inputMode="numeric"
            value={productId}
            onChange={(e) => setProductId(e.target.value)}
            placeholder="Ex.: 42"
            invalid={!!errors.productId}
          />
        </Field>
      )}

      {(ruleType === "CATEGORY_MULTIPLIER" ||
        ruleType === "PRODUCT_MULTIPLIER") && (
        <Field
          label="Multiplicador"
          hint="2 = pontos em dobro, 3 = em triplo."
          error={errors.multiplier}
        >
          <TextInput
            inputMode="decimal"
            value={multiplier}
            onChange={(e) => setMultiplier(e.target.value)}
            invalid={!!errors.multiplier}
          />
        </Field>
      )}

      {ruleType === "CATEGORY_BONUS_PERCENT" && (
        <Field
          label="Percentual de bônus"
          hint="Ex.: 10 = +10% de pontos para essa categoria."
          error={errors.percent}
        >
          <TextInput
            inputMode="decimal"
            value={percent}
            onChange={(e) => setPercent(e.target.value)}
            invalid={!!errors.percent}
          />
        </Field>
      )}

      <Field
        label="Prioridade"
        hint="Quando há várias regras, a de menor número é aplicada primeiro."
      >
        <TextInput
          inputMode="numeric"
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
        />
      </Field>

      <div className="field">
        <Toggle
          checked={hasValidity}
          onChange={setHasValidity}
          label="Essa regra tem data para começar/acabar? (promoção sazonal)"
        />
      </div>

      {hasValidity && (
        <div className="form-row">
          <Field label="Começa em">
            <TextInput
              type="date"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </Field>
          <Field label="Termina em" error={errors.validUntil}>
            <TextInput
              type="date"
              value={validUntil}
              onChange={(e) => setValidUntil(e.target.value)}
              invalid={!!errors.validUntil}
            />
          </Field>
        </div>
      )}

      {ruleType !== "BASE" && (
        <Banner tone="warn">
          Atenção: esta regra afeta os pontos de todas as compras processadas a
          partir de agora.
        </Banner>
      )}

      {apiError && (
        <div className="mt-3">
          <Banner tone="warn">
            {apiError}
          </Banner>
        </div>
      )}
    </Modal>
  );
}

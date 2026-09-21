import { useState } from "react";
import {
  ApiError,
  api,
  type Affiliate,
  type AffiliatePayload,
  type AffiliateType,
} from "../lib/api";
import { AFFILIATE_TYPE_LABELS } from "../lib/format";
import {
  Banner,
  Button,
  Field,
  Modal,
  Select,
  Textarea,
  TextInput,
} from "../components/ui";

const TYPE_OPTIONS: { value: AffiliateType; label: string }[] = [
  { value: "PROFESSOR", label: AFFILIATE_TYPE_LABELS.PROFESSOR },
  { value: "INFLUENCER", label: AFFILIATE_TYPE_LABELS.INFLUENCER },
];

const CODE_RE = /^[A-Z0-9-]{3,32}$/;

interface Props {
  affiliate?: Affiliate | null;
  onClose: () => void;
  onSaved: () => void;
}

export function AffiliateForm({ affiliate, onClose, onSaved }: Props) {
  const editing = !!affiliate;

  const [name, setName] = useState(affiliate?.name ?? "");
  const [affiliateType, setAffiliateType] = useState<AffiliateType>(
    affiliate?.affiliate_type ?? "PROFESSOR"
  );
  const [code, setCode] = useState(affiliate?.code ?? "");
  // Taxa de pontos do afiliado (%). Novo afiliado vem com 100 como sugestão.
  const [pointsRate, setPointsRate] = useState(
    affiliate ? String(Number(affiliate.points_rate)) : "100"
  );
  const [contact, setContact] = useState(affiliate?.contact ?? "");
  const [notes, setNotes] = useState(affiliate?.notes ?? "");

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function validate(): {
    ok: boolean;
    normalizedCode: string;
    rate: number;
  } {
    const e: Record<string, string> = {};
    if (!name.trim()) e.name = "Informe o nome do afiliado.";

    const normalizedCode = code.trim().toUpperCase();
    if (!CODE_RE.test(normalizedCode)) {
      e.code =
        "Use de 3 a 32 caracteres: letras, números ou hífen (sem espaços).";
    }

    const rate = Number(pointsRate.replace(",", "."));
    if (!Number.isFinite(rate) || rate < 0) {
      e.pointsRate = "Informe um percentual igual ou maior que zero.";
    }

    setErrors(e);
    return { ok: Object.keys(e).length === 0, normalizedCode, rate };
  }

  async function onSubmit() {
    setApiError(null);
    const { ok, normalizedCode, rate } = validate();
    if (!ok) return;

    const payload: AffiliatePayload = {
      name: name.trim(),
      affiliate_type: affiliateType,
      code: normalizedCode,
      points_rate: rate,
      contact: contact.trim() || null,
      notes: notes.trim() || null,
      active: affiliate?.active ?? true,
    };

    setSaving(true);
    try {
      if (editing && affiliate)
        await api.updateAffiliate(affiliate.id, payload);
      else await api.createAffiliate(payload);
      onSaved();
    } catch (err) {
      setApiError(
        err instanceof ApiError ? err.message : "Não foi possível salvar."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={editing ? "Editar afiliado" : "Novo afiliado"}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancelar
          </Button>
          <Button onClick={onSubmit} loading={saving}>
            {editing ? "Salvar alterações" : "Criar afiliado"}
          </Button>
        </>
      }
    >
      <Field label="Nome" error={errors.name}>
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Ex.: Prof. João Silva"
          invalid={!!errors.name}
        />
      </Field>

      <Field label="Tipo">
        <Select
          value={affiliateType}
          onChange={(e) => setAffiliateType(e.target.value as AffiliateType)}
        >
          {TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      </Field>

      <Field
        label="Código de divulgação"
        hint="O que o cliente informa no WhatsApp. Vira maiúsculas automaticamente."
        error={errors.code}
      >
        <TextInput
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="Ex.: JOAO10"
          invalid={!!errors.code}
        />
      </Field>

      <Field
        label="Pontos por compra (%)"
        hint="Quanto o afiliado ganha por compra atribuída. Base: 1 real = 1 ponto. Ex.: 50 = metade do valor da compra em pontos; 100 = o valor cheio."
        error={errors.pointsRate}
      >
        <TextInput
          inputMode="decimal"
          value={pointsRate}
          onChange={(e) => setPointsRate(e.target.value)}
          invalid={!!errors.pointsRate}
        />
      </Field>

      <Field label="Contato" hint="Telefone, e-mail ou @ (opcional).">
        <TextInput
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          placeholder="Ex.: 11 99999-0001"
        />
      </Field>

      <Field label="Observações" hint="Anotações internas (opcional).">
        <Textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
        />
      </Field>

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

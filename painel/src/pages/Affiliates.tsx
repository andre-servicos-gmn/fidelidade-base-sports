import { useEffect, useState } from "react";
import { ApiError, api, type Affiliate, type AffiliateStats } from "../lib/api";
import { humanizeAffiliateType, formatPoints, formatPercent } from "../lib/format";
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  Loading,
  Toggle,
} from "../components/ui";
import { IconEdit, IconPlus } from "../components/icons";
import { AffiliateForm } from "./AffiliateForm";

const EMPTY_STATS = { customers: 0, purchases: 0, points: 0, affiliate_points: 0 };

export function AffiliatesPage() {
  const [items, setItems] = useState<Affiliate[] | null>(null);
  const [stats, setStats] = useState<Record<string, AffiliateStats>>({});
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Affiliate | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const [list, statsList] = await Promise.all([
        api.listAffiliates(),
        api.affiliateStats(),
      ]);
      const byId: Record<string, AffiliateStats> = {};
      for (const s of statsList) byId[s.affiliate_id] = s;
      setItems(list);
      setStats(byId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao carregar.");
      setItems([]);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function toggle(aff: Affiliate) {
    setBusyId(aff.id);
    try {
      const updated = await api.toggleAffiliate(aff.id);
      setItems((as) => as?.map((a) => (a.id === aff.id ? updated : a)) ?? as);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao alterar.");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(aff: Affiliate) {
    if (
      !window.confirm(
        `Excluir o afiliado "${aff.name}"? Em geral é melhor DESATIVAR. ` +
          `Esta ação não pode ser desfeita.`
      )
    )
      return;
    setBusyId(aff.id);
    try {
      await api.deleteAffiliate(aff.id);
      setItems((as) => as?.filter((a) => a.id !== aff.id) ?? as);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao excluir.");
    } finally {
      setBusyId(null);
    }
  }

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }
  function openEdit(aff: Affiliate) {
    setEditing(aff);
    setFormOpen(true);
  }
  function onSaved() {
    setFormOpen(false);
    load();
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Afiliados</h1>
          <p>
            Parceiros (professores e influencers) e seus códigos de divulgação.
            O cliente informa o código no WhatsApp para ser atribuído ao
            afiliado.
          </p>
        </div>
        <Button icon={<IconPlus />} onClick={openCreate}>
          Novo afiliado
        </Button>
      </div>

      {error && (
        <div className="mb-4">
          <Banner tone="warn">
            {error}
          </Banner>
        </div>
      )}

      {items === null ? (
        <div className="card">
          <Loading label="Carregando afiliados…" />
        </div>
      ) : items.length === 0 ? (
        <div className="card">
          <EmptyState
            title="Nenhum afiliado cadastrado ainda"
            message="Cadastre um professor ou influencer e gere o código dele."
            action={<Button icon={<IconPlus />} onClick={openCreate}>
          Novo afiliado
        </Button>}
          />
        </div>
      ) : (
        <div className="card table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Nome</th>
                <th>Tipo</th>
                <th>Código</th>
                <th className="right">Taxa</th>
                <th className="right">Clientes</th>
                <th className="right">Compras</th>
                <th className="right">Pontos do afiliado</th>
                <th>Status</th>
                <th className="right">Ações</th>
              </tr>
            </thead>
            <tbody>
              {items.map((aff) => {
                const s = stats[aff.id] ?? EMPTY_STATS;
                return (
                <tr key={aff.id}>
                  <td className="strong">{aff.name}</td>
                  <td>{humanizeAffiliateType(aff.affiliate_type)}</td>
                  <td>
                    <span className="code-chip">{aff.code}</span>
                  </td>
                  <td className="right num">{formatPercent(aff.points_rate)}</td>
                  <td className="right num">{s.customers}</td>
                  <td className="right num">{s.purchases}</td>
                  <td className="right num">{formatPoints(s.affiliate_points)}</td>
                  <td>
                    <div className="row">
                      <Toggle
                        checked={aff.active}
                        onChange={() => toggle(aff)}
                        disabled={busyId === aff.id}
                      />
                      <Badge tone={aff.active ? "green" : "gray"}>
                        {aff.active ? "Ativo" : "Inativo"}
                      </Badge>
                    </div>
                  </td>
                  <td className="right">
                    <div className="row row-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        icon={<IconEdit size={15} />}
                        onClick={() => openEdit(aff)}
                      >
                        Editar
                      </Button>
                      <button
                        className="btn-danger-link"
                        onClick={() => remove(aff)}
                        disabled={busyId === aff.id}
                        title="Preferir desativar a excluir"
                      >
                        excluir
                      </button>
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {formOpen && (
        <AffiliateForm
          affiliate={editing}
          onClose={() => setFormOpen(false)}
          onSaved={onSaved}
        />
      )}
    </>
  );
}

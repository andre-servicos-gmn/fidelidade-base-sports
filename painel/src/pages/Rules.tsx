import { useEffect, useState } from "react";
import { ApiError, api, type Rule } from "../lib/api";
import { humanizeRuleType, summarizeRule } from "../lib/format";
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  Loading,
  Toggle,
} from "../components/ui";
import { RuleForm } from "./RuleForm";

export function RulesPage() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Rule | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      setRules(await api.listRules());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao carregar.");
      setRules([]);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function toggle(rule: Rule) {
    setBusyId(rule.id);
    try {
      const updated = await api.toggleRule(rule.id);
      setRules((rs) => rs?.map((r) => (r.id === rule.id ? updated : r)) ?? rs);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Erro ao alterar.");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(rule: Rule) {
    if (
      !window.confirm(
        `Excluir a regra "${rule.name}"? Em geral é melhor DESATIVAR ` +
          `(o histórico fica preservado). Esta ação não pode ser desfeita.`
      )
    )
      return;
    setBusyId(rule.id);
    try {
      await api.deleteRule(rule.id);
      setRules((rs) => rs?.filter((r) => r.id !== rule.id) ?? rs);
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
  function openEdit(rule: Rule) {
    setEditing(rule);
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
          <h1>Regras de pontuação</h1>
          <p>
            Definem quantos pontos cada compra gera. Alterações valem para as
            próximas compras processadas.
          </p>
        </div>
        <Button onClick={openCreate}>+ Nova regra</Button>
      </div>

      {error && (
        <div style={{ marginBottom: 16 }}>
          <Banner tone="warn" icon="!">
            {error}
          </Banner>
        </div>
      )}

      {rules === null ? (
        <div className="card">
          <Loading label="Carregando regras…" />
        </div>
      ) : rules.length === 0 ? (
        <div className="card">
          <EmptyState
            title="Nenhuma regra cadastrada ainda"
            message="Comece criando a regra base (quantos pontos por real)."
            action={<Button onClick={openCreate}>+ Nova regra</Button>}
          />
        </div>
      ) : (
        <div className="card table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Nome</th>
                <th>Tipo</th>
                <th>Como funciona</th>
                <th className="right">Prioridade</th>
                <th>Status</th>
                <th className="right">Ações</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id}>
                  <td className="strong">{rule.name}</td>
                  <td>{humanizeRuleType(rule.rule_type)}</td>
                  <td className="muted">{summarizeRule(rule)}</td>
                  <td className="right num">{rule.priority}</td>
                  <td>
                    <div className="row">
                      <Toggle
                        checked={rule.active}
                        onChange={() => toggle(rule)}
                        disabled={busyId === rule.id}
                      />
                      <Badge tone={rule.active ? "green" : "gray"}>
                        {rule.active ? "Ativa" : "Inativa"}
                      </Badge>
                    </div>
                  </td>
                  <td className="right">
                    <div
                      className="row"
                      style={{ justifyContent: "flex-end", gap: 8 }}
                    >
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(rule)}
                      >
                        Editar
                      </Button>
                      <button
                        className="btn-danger-link"
                        onClick={() => remove(rule)}
                        disabled={busyId === rule.id}
                        title="Preferir desativar a excluir"
                      >
                        excluir
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {formOpen && (
        <RuleForm
          rule={editing}
          onClose={() => setFormOpen(false)}
          onSaved={onSaved}
        />
      )}
    </>
  );
}

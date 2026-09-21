import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  api,
  type Customer,
  type CustomerCoupon,
  type CustomerList,
  type LedgerEntry,
} from "../lib/api";
import {
  formatDate,
  formatDateTime,
  formatDiscount,
  formatPoints,
  humanizeEntryType,
  humanizeStatus,
} from "../lib/format";
import {
  Badge,
  Banner,
  Button,
  EmptyState,
  Loading,
  TextInput,
} from "../components/ui";
import { IconArrowLeft, IconArrowRight, IconChevronRight, IconSearch } from "../components/icons";

interface Loaded {
  customer: Customer;
  ledger: LedgerEntry[];
  coupons: CustomerCoupon[];
}

const PAGE_SIZE = 25;

export function CustomersPage() {
  // Listagem (visão padrão).
  const [list, setList] = useState<CustomerList | null>(null);
  const [page, setPage] = useState(1);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  // Detalhe de um cliente (lista e busca levam para cá).
  const [data, setData] = useState<Loaded | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Busca por CPF exato.
  const [cpf, setCpf] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async (targetPage: number) => {
    setListLoading(true);
    setListError(null);
    try {
      setList(await api.listCustomers(targetPage, PAGE_SIZE));
    } catch (err) {
      setListError(
        err instanceof ApiError ? err.message : "Erro ao carregar os clientes."
      );
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadList(page);
  }, [loadList, page]);

  /** Abre o detalhe. A linha da lista já traz saldo/CPF; faltam ledger e cupons. */
  async function openCustomer(customer: Customer) {
    setDetailLoading(true);
    setError(null);
    try {
      const [ledger, coupons] = await Promise.all([
        api.customerLedger(customer.id),
        api.customerCoupons(customer.id),
      ]);
      setData({ customer, ledger, coupons });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Erro ao abrir o cliente."
      );
    } finally {
      setDetailLoading(false);
    }
  }

  async function search(e: FormEvent) {
    e.preventDefault();
    const clean = cpf.replace(/\D/g, "");
    if (clean.length === 0) return;
    // A busca é por CPF EXATO: o banco guarda só o hash, que não permite
    // procurar por pedaço. Avisamos ANTES de disparar um request condenado —
    // um 404 aqui diria "cliente não existe", o que seria falso.
    if (clean.length !== 11) {
      setError(
        "A busca por CPF precisa dos 11 dígitos completos (o CPF é " +
          "armazenado criptografado, então não dá para procurar por parte " +
          "dele). Se você não tem o número inteiro, use a lista abaixo."
      );
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const customer = await api.findCustomer(clean);
      await openCustomer(customer);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError("Nenhum cliente cadastrado com esse CPF.");
      } else {
        setError(err instanceof ApiError ? err.message : "Erro na busca.");
      }
    } finally {
      setSearching(false);
    }
  }

  function backToList() {
    setData(null);
    setError(null);
    setCpf("");
    void loadList(page);
  }

  // ----- Detalhe ---------------------------------------------------------- //
  if (data) {
    return (
      <>
        <div className="page-header">
          <div>
            <h1>Cliente</h1>
            <p>
              Consulta de suporte. Por privacidade, CPF e telefone aparecem
              mascarados.
            </p>
          </div>
          <Button
            variant="ghost"
            icon={<IconArrowLeft size={16} />}
            onClick={backToList}
          >
            Voltar para a lista
          </Button>
        </div>
        <CustomerDetail data={data} />
      </>
    );
  }

  // ----- Lista + busca ---------------------------------------------------- //
  const total = list?.total ?? 0;
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);
  const hasPrev = page > 1;
  const hasNext = last < total;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Clientes</h1>
          <p>
            Consulta de suporte. Por privacidade, CPF e telefone aparecem
            mascarados.
          </p>
        </div>
      </div>

      <div className="card card-pad mb-5">
        <form onSubmit={search} className="search-form">
          <label className="search-field">
            <IconSearch className="search-icon" />
            <TextInput
              value={cpf}
              onChange={(e) => setCpf(e.target.value)}
              placeholder="CPF completo (11 dígitos)"
              inputMode="numeric"
              aria-label="CPF completo"
            />
          </label>
          <Button type="submit" loading={searching || detailLoading}>
            Buscar
          </Button>
        </form>
      </div>

      {error && (
        <div className="mb-4">
          <Banner tone="warn">
            {error}
          </Banner>
        </div>
      )}

      <div className="card">
        <div className="card-row">
          <h2>Todos os clientes</h2>
          <span className="subtle">
            {listLoading
              ? "carregando…"
              : total === 0
              ? "nenhum cliente"
              : `${first}–${last} de ${total}`}
          </span>
        </div>

        {listError && (
          <div className="pad-4">
            <Banner tone="warn">
              {listError}
            </Banner>
          </div>
        )}

        {listLoading && <Loading label="Carregando clientes…" />}

        {!listLoading && !listError && total === 0 && (
          <EmptyState
            title="Nenhum cliente ainda"
            message="Os clientes aparecem aqui automaticamente na primeira compra creditada."
          />
        )}

        {!listLoading && !listError && total > 0 && (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>CPF</th>
                    <th>Telefone</th>
                    <th className="right">Saldo</th>
                    <th>Cadastro</th>
                    <th aria-label="Abrir" />
                  </tr>
                </thead>
                <tbody>
                  {list?.items.map((c) => (
                    <tr
                      key={c.id}
                      className="row-link"
                      onClick={() => void openCustomer(c)}
                    >
                      <td className="strong mono">{c.cpf_masked}</td>
                      <td className="muted mono">{c.phone_masked ?? "—"}</td>
                      <td className="right num strong">
                        {formatPoints(c.balance)}
                      </td>
                      <td className="muted">{formatDate(c.created_at)}</td>
                      <td className="right muted">
                        <IconChevronRight size={16} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {(hasPrev || hasNext) && (
<div className="row-between pager">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!hasPrev}
                  icon={<IconArrowLeft size={14} />}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  Anteriores
                </Button>
                <span className="subtle">página {page}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!hasNext}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Próximos
                  <IconArrowRight size={14} />
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}

function CustomerDetail({ data }: { data: Loaded }) {
  const { customer, ledger, coupons } = data;
  return (
    <div className="stack">
      {/* Cabeçalho do cliente */}
      <div className="card card-pad customer-hero">
        <div className="customer-hero-row">
          <div>
            <div className="eyebrow">CPF</div>
            <div className="customer-cpf mono">{customer.cpf_masked}</div>
            <div className="muted mt-2">
              Telefone: {customer.phone_masked ?? "—"} · Cadastro:{" "}
              {formatDate(customer.created_at)}
            </div>
          </div>
          <div className="balance-box">
            <div className="eyebrow">Saldo atual</div>
            <div className="balance-hero">
              <span className="value">{formatPoints(customer.balance)}</span>
              <span className="muted">pontos</span>
            </div>
          </div>
        </div>
      </div>

      {/* Histórico do ledger */}
      <div className="card">
        <div className="card-row">
          <h2>Histórico de pontos</h2>
          <span className="subtle">{ledger.length} lançamento(s)</span>
        </div>
        {ledger.length === 0 ? (
          <EmptyState
            title="Sem movimentações"
            message="Este cliente ainda não ganhou nem gastou pontos."
          />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Data</th>
                  <th>O que aconteceu</th>
                  <th>Detalhe</th>
                  <th className="right">Pontos</th>
                  <th className="right">Saldo após</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((e) => (
                  <tr key={e.sequence}>
                    <td className="muted">{formatDateTime(e.created_at)}</td>
                    <td className="strong">{humanizeEntryType(e.entry_type)}</td>
                    <td className="muted">{e.description ?? "—"}</td>
                    <td
                      className={`right num ${
                        e.points >= 0 ? "text-pos" : "text-neg"
                      }`}
                    >
                      {e.points >= 0 ? "+" : ""}
                      {formatPoints(e.points)}
                    </td>
                    <td className="right num">{formatPoints(e.balance_after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Cupons do cliente */}
      <div className="card">
        <div className="card-row">
          <h2>Cupons resgatados</h2>
          <span className="subtle">{coupons.length} cupom(ns)</span>
        </div>
        {coupons.length === 0 ? (
          <EmptyState
            title="Nenhum cupom resgatado"
            message="Este cliente ainda não trocou pontos por cupom."
          />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Código</th>
                  <th>Recompensa</th>
                  <th>Status</th>
                  <th>Resgatado em</th>
                  <th>Válido até</th>
                </tr>
              </thead>
              <tbody>
                {coupons.map((c) => (
                  <tr key={c.code}>
                    <td className="strong mono">{c.code}</td>
                    <td>{formatDiscount(c.discount_type, c.discount_value)}</td>
                    <td>
                      <Badge tone={c.status === "USED" ? "gray" : "pink"}>
                        {humanizeStatus(c.status)}
                      </Badge>
                    </td>
                    <td className="muted">{formatDate(c.allocated_at)}</td>
                    <td className="muted">{formatDate(c.expires_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

# Fase 3 — Estado de conversa compartilhado (Redis)

> Status: **IMPLEMENTADO** (2026-07-20). Decisões aplicadas: falha do Redis =
> **fail-safe**; ambiente = **Redis gerenciado (Upstash/Redis Cloud)**.
> Entregue: `RedisSessionStore` (`app/whatsapp/session_store.py`), factory por
> config (`app/dependencies.py`), fechamento no lifespan (`app/main.py`), configs
> (`REDIS_URL`, `USE_REDIS_SESSION_STORE`, `SESSION_TTL_SECONDS`), testes
> (`tests/test_redis_session_store.py`), README. Falta só **provisionar** a
> instância Redis e ligar (`USE_REDIS_SESSION_STORE=true`) no deploy — Fase 4.

## 1. Problema

O estado da conversa (`SessionStore`) vive na RAM de um processo
(`app/dependencies.py::get_session_store` → `InMemorySessionStore`). Isso impõe
três limites incompatíveis com produção:

1. **Não escala além de 1 worker.** Com `uvicorn --workers N`, cada worker tem
   memória própria. Cliente manda "resgatar" (worker A grava
   `AWAITING_REWARD_CHOICE`), depois manda "2" (cai no worker B, que não sabe de
   nada) → a conversa esquece o passo.
2. **Perde tudo em cada deploy/restart.** Conversas em andamento evaporam.
3. **Quebra o worker separado (Fase 4).** Se o polling roda em outro processo, o
   `dispatch_affiliate_prompts` grava `AWAITING_AFFILIATE_CODE` num store que o
   webhook (outro processo) não enxerga → a resposta com o código de afiliado
   nunca casa.

Redis resolve os três: store único, fora do processo, com TTL nativo.

## 2. Por que é de baixo risco

- **Acoplamento único.** Todos os consumidores (webhook, `conversation`,
  `affiliate_prompt`, `worker`) dependem só da interface abstrata `SessionStore`
  (`app/whatsapp/session_store.py`), cujos métodos já são async. Nenhum muda.
- **A troca é 1 factory.** Só `get_session_store()` decide entre in-memory e Redis.
- **Payload já é JSON-safe.** `ConversationState.data` guarda apenas:
  - resgate: `{"rewards": [ {reward_id: str, discount_type: str, discount_value:
    float, points_cost: int, available_count: int, min_order_value: float|None} ]}`
  - afiliado: `{"source_reference": str, "points": int, "amount": str}`

  Tudo str/float/int/None → **serialização com `json.dumps` puro, sem encoder
  custom** para Decimal/datetime.

## 3. Escopo

### 3.1 Dependência e config
- `redis>=5` (usa `redis.asyncio`, oficial e async-nativo) no `pyproject.toml`.
- `config.py`: `redis_url: str = ""` e `use_redis_session_store: bool = False`
  (default seguro: mantém in-memory). Espelhar em `.env` / `.env.example`.
- Upstash/Redis Cloud usam TLS → a URL será `rediss://...` (dois "s"). O cliente
  `redis.asyncio.from_url` entende `rediss://` nativamente.

### 3.2 `RedisSessionStore` (nova subclasse em `session_store.py`)
- Implementa `get`, `set`, `delete`.
- **Serialização:** `set` grava `json.dumps({"step": state.step.value, "data":
  state.data})`; `get` reidrata e reconstrói `ConversationStep(step)`.
- **TTL nativo:** `SET key value EX ttl_seconds` — o Redis expira sozinho
  (substitui o clock manual do in-memory). Mesmo default de 600s.
- **Namespace:** prefixo `fidelidade:session:{phone}`.
- **Política fail-safe (decidida):**
  - `get()`: se o Redis lançar, **loga e retorna `None`** → a conversa cai no
    primeiro contato/menu em vez de estourar erro para o cliente.
  - `set()` / `delete()`: se lançar, **loga e engole** (não derruba o webhook).
  - Consequência aceita: numa indisponibilidade do Redis, o cliente pode ver o
    menu de novo em vez de continuar o passo. Preferível a um 500. Isso NÃO é
    perda de dados durável (o estado é efêmero por design).

### 3.3 Factory (`dependencies.py`)
- `get_session_store()` retorna `RedisSessionStore(redis_url, ...)` quando
  `use_redis_session_store=true`; senão `InMemorySessionStore()` (intacto).
- Conexão: pool `redis.asyncio` criado uma vez (o `@lru_cache` já dá singleton).
  Fechar no shutdown via lifespan (junto do worker que já vive lá).

### 3.4 Testes
- **Unit (sem Redis real):** round-trip de serialização — `ConversationState`
  com `data` de resgate (lista de rewards) e de afiliado (amount str) sobrevive a
  `dumps`→`loads` idêntico, e `step` volta como `ConversationStep`. Cobre o passo
  mais arriscado sem infra.
- **Unit fail-safe:** um cliente Redis fake que sempre lança prova que `get`
  retorna `None` e `set`/`delete` não propagam.
- **Integração (`@pytest.mark.integration`, exige Redis):** `set`→`get`
  round-trip; `delete` remove; TTL expira. Segue o padrão de skip do projeto
  (pula se `redis_url` vazia, igual ao `database_url`).
- Os 66 testes atuais continuam passando (a interface não muda).

### 3.5 Documentação
- Seção no `README.md`: quando ligar o Redis, criar uma instância no
  Upstash/Redis Cloud, pôr a `rediss://` no `.env`, e a relação com multi-worker.
- Atualizar comentários em `dependencies.py`/`worker.py` que dizem "troque por
  Redis (a interface já está pronta)" para apontar à implementação real.

## 4. Fora de escopo (fronteiras)
- Provisionar/hospedar o Redis (é Fase 4/deploy — aqui só o código consumidor).
- Migrar rate-limiting ou outro estado para Redis (escopo é só `SessionStore`).
- Persistência durável do estado (Redis+TTL é efêmero por design; conversas são
  curtas, então está correto).

## 5. Passo a passo de execução (quando for implementar)
1. `pyproject.toml`: adicionar `redis>=5`; reinstalar (`pip install -e ".[dev]"`).
2. `config.py`: `redis_url`, `use_redis_session_store`.
3. `session_store.py`: `RedisSessionStore` (serialização + TTL + fail-safe).
4. `dependencies.py`: `get_session_store()` decide por config; fechar pool no
   shutdown (lifespan em `main.py`).
5. Testes: unit de serialização + unit fail-safe + integração marcada.
6. `README.md` + comentários.
7. Rodar `pytest` (unit) e, com um Redis real, `pytest -m integration`.
8. Ligar em produção: `USE_REDIS_SESSION_STORE=true`, `REDIS_URL=rediss://...`,
   e então `RUN_WORKER_IN_APP=false` + `uvicorn --workers N` já é seguro.

## 6. Critério de pronto (Definition of Done)
- Com `use_redis_session_store=true`, uma conversa multi-passo (resgate:
  "resgatar" → "2") completa mesmo alternando entre processos/workers.
- Um restart da API no meio de um `AWAITING_AFFILIATE_CODE` não perde o estado.
- Redis derrubado → cliente vê o menu, webhook responde 200 (não 500).
- Suíte unit verde sem Redis; suíte integração verde com Redis.

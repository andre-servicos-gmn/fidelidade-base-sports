# Fidelidade Base Sports

Fundação de um sistema de fidelidade em Python. Por enquanto só existe a
**estrutura** e a **camada de integração externa isolada** (TouchPay) — nenhuma
lógica de negócio e nenhum banco de dados ainda.

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2 / pydantic-settings
- pytest

## Estrutura

```
fidelidade/
  app/
    main.py                 # FastAPI app, rota /health, factory do client TouchPay
    config.py               # Settings via pydantic-settings (.env)
    integrations/
      touchpay/
        client.py           # Interface abstrata TouchPayClient (ABC)
        mock_client.py      # MockTouchPayClient (em memória)
        schemas.py          # Modelos Pydantic do swagger TouchPay
  tests/
```

A regra de ouro: o domínio depende apenas de `TouchPayClient` (a interface) e
dos schemas. Trocar o mock por um cliente HTTP real é só adicionar outra
subclasse de `TouchPayClient` e ligá-la no factory em `app/main.py`.

## Setup

Com [uv](https://docs.astral.sh/uv/) (recomendado):

```bash
cd fidelidade
uv venv
uv pip install -e ".[dev]"
```

Sem uv (venv + pip):

```bash
cd fidelidade
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate
pip install -e ".[dev]"
```

Copie o exemplo de configuração:

```bash
cp .env.example .env
```

## Rodar a aplicação

```bash
uvicorn app.main:app --reload
```

Healthcheck: http://127.0.0.1:8000/health → `{"status": "ok"}`

Docs interativas (Swagger UI): http://127.0.0.1:8000/docs

## Banco de dados (Supabase / PostgreSQL)

A persistência usa **SQLAlchemy 2.0 async (asyncpg)** + **Alembic**. O banco é
o Supabase; a aplicação só conecta e roda migrations (a instância já existe).

### Connection string

Em **Supabase > Project Settings > Database**, copie a URI e troque o prefixo
`postgresql://` por `postgresql+asyncpg://`. Coloque no `.env` (NUNCA no
`alembic.ini` nem versionado). O `.env` está no `.gitignore`; o repositório traz
só o `.env.example` com placeholders.

> **⚠️ IPv6 vs. IPv4 (importante).** A *direct connection*
> `db.[REF].supabase.co:5432` hoje só tem registro **IPv6**. Se sua rede não
> tiver IPv6, a conexão falha com `getaddrinfo failed`. Duas saídas:
>
> 1. **Direct connection** (porta 5432) — use de uma rede com IPv6 ou com o
>    add-on IPv4 do Supabase:
>    ```
>    DATABASE_URL=postgresql+asyncpg://postgres:[SENHA]@db.[REF].supabase.co:5432/postgres
>    ```
> 2. **Session Pooler** (porta 5432, **IPv4**) — funciona para migrations e é o
>    que usamos neste projeto. Note o usuário `postgres.[REF]` e o host
>    `aws-1-[REGIAO].pooler.supabase.com` (este projeto está em `sa-east-1`):
>    ```
>    DATABASE_URL=postgresql+asyncpg://postgres.[REF]:[SENHA]@aws-1-sa-east-1.pooler.supabase.com:5432/postgres
>    ```
>    Use o **Session pooler** (porta 5432), não o Transaction pooler (6543) —
>    este último não suporta o DDL/prepared statements das migrations.

### Migrations

```bash
# pré-visualizar o SQL sem tocar no banco (modo offline)
alembic upgrade head --sql

# aplicar de verdade no Supabase
alembic upgrade head

# reverter a última migration
alembic downgrade -1
```

O Alembic lê a `DATABASE_URL` da config (`app/alembic/env.py`), nunca hardcoded.

## Estado de conversa (memória vs. Redis)

O passo da conversa (ex: "esperando o cliente escolher a recompensa") precisa
sobreviver ENTRE mensagens do WhatsApp (cada webhook é um POST separado). Onde
esse estado vive é escolhido por config (`app/whatsapp/session_store.py`):

- **Em memória** (default, `USE_REDIS_SESSION_STORE=false`): simples, mas só
  serve para **1 worker single-process**. Cada processo tem memória própria, e
  o estado se perde em restart. Bom para dev e testes.
- **Redis** (`USE_REDIS_SESSION_STORE=true` + `REDIS_URL`): estado compartilhado
  entre processos/workers, com TTL nativo. **Obrigatório** para rodar
  `uvicorn --workers N` OU o worker de polling em processo separado (senão a
  resposta do cliente cai num processo que não tem o estado).

O Redis é **fail-safe**: se ficar indisponível, a conversa é tratada como "sem
estado" (o cliente vê o menu) em vez de o webhook estourar 500.

```bash
# Redis gerenciado (Upstash/Redis Cloud usam TLS -> rediss://):
#   USE_REDIS_SESSION_STORE=true
#   REDIS_URL=rediss://default:[SENHA]@[HOST].upstash.io:6379
# Testar com um Redis local:
#   docker run -p 6379:6379 redis   (REDIS_URL=redis://localhost:6379)
#   pytest -m integration            (roda os testes contra o Redis real)
```

## Agendador de polling (worker de ingestão)

A API TouchPay não avisa quando há uma compra — só responde quando perguntamos.
O **worker** (`app/worker.py`) é quem pergunta, em intervalos regulares. A cada
ciclo: lê as compras da janela recente (`ingest_transactions`) → credita pontos
→ dispara a pergunta de afiliado no WhatsApp (`dispatch_affiliate_prompts`).

Sem o worker rodando, compras do totem **nunca viram pontos** e a pergunta de
afiliado nunca é enviada. É o "coração" que faz o sistema andar sozinho.

**Janela sobreposta + idempotência.** Cada ciclo lê `[agora - POLL_WINDOW_MINUTES,
agora]`. A janela é maior que o intervalo de propósito, para nenhuma compra
escapar entre ciclos; a constraint única em `source_reference` garante que a
sobreposição não credite a mesma compra duas vezes. Por isso não há checkpoint.

Dois jeitos de rodar:

```bash
# 1) No MESMO processo da API (simples; ideal para 1 worker / testes ponta a
#    ponta — webhook e dispatch compartilham o session_store em memória).
#    Ligue RUN_WORKER_IN_APP=true no .env e suba só a API:
uvicorn app.main:app

# 2) Como processo à parte (produção multi-worker; exige SessionStore
#    compartilhado/Redis para o estado casar com o webhook):
python -m app.worker            # loop infinito
python -m app.worker --once     # um único ciclo (útil para cron/depuração)
```

Config (`.env`): `POLL_INTERVAL_SECONDS` (default 300), `POLL_WINDOW_MINUTES`
(default 30), `RUN_WORKER_IN_APP` (default false).

## Seeds (configuração e dados de teste)

As regras de pontuação ficam no banco (tabela `scoring_rules`) e a ingestão as
carrega em tempo de execução — mudanças entram em vigor sem mexer em código. A
"tabela de recompensas" (X pontos = cupom de Y) é **implícita no pool**
(`coupon_pool`): cada cupom carrega `points_cost` e `discount_value`, e
`list_available_rewards` agrega. Não há modelo `Reward` separado no MVP.

```bash
# Regra BASE (1 ponto/real) + cupons de TESTE nas 3 faixas (R$10/500,
# R$25/1000, R$50/2000). Idempotente.
python -m scripts.seed_config

# Cliente de TESTE (CPF 00000000001, telefone 11900000000) com 1500 pontos,
# para testar resgate sem depender da ingestão. Idempotente.
python -m scripts.seed_test_customer
```

> **⚠️ AVISO FORTE — dados de teste.** Os cupons criados pelo `seed_config` têm
> prefixo `TESTE-` e **NÃO existem no TouchPay**: não funcionam no totem real.
> O cliente de teste e seu saldo também são fictícios. Antes do go-live:
> 1. Remova os cupons de teste: `DELETE FROM coupon_pool WHERE code LIKE 'TESTE-%';`
> 2. Remova o cliente de teste (CPF `00000000001`) e seus lançamentos.
> 3. Cadastre os cupons **reais** criados no painel da AMLabs.

## API administrativa (painel)

Endpoints REST sob `/admin/*`, consumidos pelo painel web (próxima fase). Todos
exigem **JWT** (exceto o login). Docs interativas em `/docs` — o botão
**Authorize** aceita o Bearer token retornado pelo login.

```bash
# Cria o admin inicial. Senha via INITIAL_ADMIN_PASSWORD; se vazia, gera e
# imprime uma senha aleatória forte (uma única vez). Idempotente.
python -m scripts.seed_admin
```

Fluxo: `POST /admin/login` `{username, password}` → `{access_token}`. Envie
`Authorization: Bearer <token>` nas demais rotas.

Grupos: `/admin/rules` (CRUD de regras de pontuação — uma regra criada aqui
entra em vigor na **próxima ingestão**, sem deploy), `/admin/coupons` (pool +
cadastro em lote + resumo agregado), `/admin/customers` (consulta de suporte;
**CPF e telefone sempre mascarados** na resposta — nunca o CPF completo nem o
hash).

Segurança: senha com **bcrypt**, JWT assinado (HS256) com expiração de
`JWT_EXPIRATION_HOURS`, mensagem de login genérica (não revela se errou usuário
ou senha), todos os endpoints fechados por padrão.

> Cadastrar um cupom em `/admin/coupons` apenas REGISTRA o código aqui — não
> cria o cupom no TouchPay. Os códigos devem corresponder a cupons reais do
> painel da TouchPay/AMLabs.

## Segurança do CPF

O CPF **nunca** é armazenado em texto plano:

- `app/domain/security.py` → `hash_cpf()` aplica HMAC-SHA256 com a `CPF_PEPPER`
  (determinístico, permite buscar por CPF sem guardá-lo) e `mask_cpf()` gera
  `123****89` para exibição.
- O ledger de pontos (`app/domain/ledger.py`) é **append-only com encadeamento
  de hash**: cada lançamento guarda o hash do anterior. `verify_chain()`
  detecta qualquer adulteração.

Gere uma pepper forte: `python -c "import secrets; print(secrets.token_hex(32))"`.

## Rodar os testes

```bash
pytest                  # testes unitários (integração é pulada por padrão)
```

### ⚠️ Testes de integração exigem um banco SEPARADO

Os testes de integração **não são somente-leitura**: eles semeiam e depois
**apagam** dados, e os filtros de limpeza usam as constantes do seed de produção
(`BASE_RULE_NAME` e `TEST_PREFIX`, importadas de `scripts/seed_config.py`).
Rodá-los contra o banco real apaga a **regra BASE** e **todo o pool de cupons
`TESTE-%`**, deixando o sistema sem nenhuma recompensa disponível.

Por isso eles só rodam contra um banco descartável, apontado por
`TEST_DATABASE_URL`. Sem essa variável, são **pulados** (ver `tests/conftest.py`):

```bash
# banco descartável, NUNCA o de produção:
TEST_DATABASE_URL=postgresql+asyncpg://user:senha@host:5432/fidelidade_test
pytest -m integration
```

Se o banco de teste estiver zerado, aplique as migrations nele antes
(`DATABASE_URL=$TEST_DATABASE_URL alembic upgrade head`).

## Configuração

| Variável            | Default                              | Descrição                                            |
| ------------------- | ------------------------------------ | ---------------------------------------------------- |
| `TOUCHPAY_BASE_URL` | `https://api.touchpay.example.com`   | URL base da API TouchPay (cliente real).             |
| `TOUCHPAY_TOKEN`    | `""`                                 | Token de autenticação da TouchPay.                   |
| `USE_MOCK_TOUCHPAY` | `true`                               | Usa o mock em memória quando `true`.                 |
| `DATABASE_URL`      | `""`                                 | Connection string do Supabase (formato asyncpg).     |
| `CPF_PEPPER`        | `dev-insecure-pepper-change-me`      | Segredo do HMAC do CPF. **Troque em produção.**      |

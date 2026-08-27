# Painel · Fidelidade Base Sports

Painel administrativo interno (React + Vite + TypeScript) que consome a API
administrativa do sistema de fidelidade. Ferramenta de trabalho da equipe
comercial — foco em clareza e densidade de informação, não em estética chamativa.

Áreas: **Regras de pontuação**, **Cupons** e **Clientes** (suporte), além do
**Login**.

## Rodar

Pré-requisito: Node 18+.

```bash
cd painel
npm install
cp .env.example .env     # ajuste VITE_API_URL se a API não estiver em :8000
npm run dev              # http://localhost:5173
```

Build de produção:

```bash
npm run build            # gera dist/
npm run preview          # serve o build localmente
```

## Apontar para a API

A URL da API vem da variável **`VITE_API_URL`** (em `.env`), nunca hardcoded.

- Dev: `VITE_API_URL=http://localhost:8000` (default).
- Produção: aponte para a URL real da API. Nada no código muda.

A API precisa estar rodando e com CORS liberado para a origem do painel
(o backend já tem `CORS_ORIGINS`, default `*` em dev). Suba a API com:

```bash
# na pasta fidelidade/
uvicorn app.main:app --reload
```

E crie um admin para conseguir logar:

```bash
python -m scripts.seed_admin
```

## Onde trocar as cores da marca

**`src/styles/tokens.css`** — é o ÚNICO arquivo a editar. Todas as cores são
variáveis CSS em `:root`. Troque `--brand-green-*` (verde primário) e
`--brand-pink-*` (rosa escuro / acento) pelos hex exatos da Base Sports; o resto
do painel se ajusta sozinho. A fonte (`--font-sans`) também está lá.

## Autenticação

- Login em `/login` (usuário + senha) → recebe um JWT, guardado no
  `localStorage`.
- O cliente HTTP (`src/lib/api.ts`) injeta `Authorization: Bearer <token>`
  automaticamente e, ao receber **401** (token expirado), limpa o token e
  redireciona para o login.
- Rotas autenticadas são protegidas por `ProtectedRoute` (sem token → login).

## Estrutura

```
src/
  lib/        api.ts (HTTP + tipos), auth.tsx (contexto), format.ts (tradução)
  components/ Layout, ProtectedRoute, ui.tsx (primitivos)
  pages/      Login, Rules, RuleForm, Coupons, Customers
  styles/     tokens.css (MARCA), global.css
```

## Notas de produto

- O formulário de regra é **guiado** (sem JSON): o usuário escolhe o tipo em
  linguagem de negócio e só vê os campos relevantes. Termos técnicos
  (`rule_type`, `params`) nunca aparecem.
- Cupons: o cadastro só **registra** o código aqui — ele precisa existir antes
  no painel da TouchPay/AMLabs (aviso destacado na tela).
- Clientes: tela de suporte. CPF e telefone já vêm mascarados da API; o painel
  não tenta desmascarar.

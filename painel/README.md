# Painel · Fidelidade Base Sports

Painel administrativo interno (React + Vite + TypeScript) que consome a API
administrativa do sistema de fidelidade. Ferramenta de trabalho da equipe
comercial — foco em clareza e densidade de informação, com a identidade visual
da Base Sports.

Áreas: **Regras de pontuação**, **Cupons**, **Clientes** (suporte) e
**Afiliados**, além do **Login**.

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

## Identidade visual

**`src/styles/tokens.css`** é o ÚNICO arquivo com hex; o resto do painel só usa
as variáveis. Os valores vêm do site basesports.com.br e do logo:

| Token | Hex | Uso |
|---|---|---|
| `--brand-green-neon` | `#11E763` | verde do logo: botão primário, barras, destaques |
| `--brand-green` | `#10AC61` | verde de botão do site: hover, toggle ligado |
| `--brand-green-ink` | `#0A7D46` | verde para **texto** (AA) |
| `--brand-pink` / `-ink` | `#EB4086` / `#B81E60` | rosa da bolinha do logo: atenção, estoque baixo, pontos negativos |
| `--brand-black` | `#0A0A0A` | cabeçalho do site: tela de login, detalhes |
| `--brand-yellow` | `#F1FF00` | **só sobre preto** (some em fundo claro) |
| `--color-text` | `#072708` | texto do site |

Regras: texto colorido usa sempre a versão `*-ink`; o texto sobre o verde neon é
escuro (`--color-on-primary`), nunca branco. Fontes: **Sora** (títulos e números
de destaque, `--font-display`) e **Inter** (corpo, `--font-sans`), via Google
Fonts em `index.html`.

Assets em `public/`:

- `logo-dark.png`: logo original (traço branco), para fundo escuro. Vem de
  `https://dcdn-us.mitiendanube.com/stores/004/461/654/themes/common/logo-511254320-1720197029-49ea50985fc78dc53009ed6ff9bb2bc11720197029.png`,
  recortado sem margem.
- `logo-light.png`: a mesma arte com os pixels brancos/cinza trocados por
  `#072708` (verde e rosa preservados), para fundo claro. Para regenerar, rode um
  script Pillow que faça essa troca nos pixels de baixa saturação.
- `favicon.png` / `apple-touch-icon.png`: raquete + bolinha do logo sobre um
  quadrado preto.

Ícones: SVG inline em `src/components/icons.tsx` (sem dependência externa).

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
  components/ Layout, ProtectedRoute, ui.tsx (primitivos), icons.tsx (SVG)
  pages/      Login, Rules, RuleForm, Coupons, Customers, Affiliates, AffiliateForm
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

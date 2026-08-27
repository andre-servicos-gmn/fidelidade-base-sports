# Painel administrativo (React + Vite) servido por nginx.
#
# ============================== ATENÇÃO ============================== #
# VITE_API_URL é lida em tempo de BUILD, não de execução.
#
# O Vite substitui `import.meta.env.VITE_API_URL` pelo valor LITERAL ao
# compilar. Definir a variável só no ambiente de execução do container NÃO
# tem efeito nenhum: o bundle já saiu com o valor antigo embutido.
#
# Por isso ela entra como ARG. No Easypanel, configure-a como argumento de
# build (não como variável de ambiente), apontando para o domínio da API:
#   VITE_API_URL=https://api.seudominio.com
# Trocar a URL depois exige REBUILD, não apenas restart.
# ===================================================================== #
FROM node:22-alpine AS build

WORKDIR /app

# Dependências primeiro: camada cacheada enquanto o lockfile não mudar.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

# `npm run build` roda `tsc --noEmit && vite build`: erro de tipo quebra o
# build de propósito, em vez de publicar um painel quebrado.
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

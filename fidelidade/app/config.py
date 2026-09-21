"""Configuração da aplicação, carregada de variáveis de ambiente / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações da aplicação.

    Lê de variáveis de ambiente e, se existir, de um arquivo `.env`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    touchpay_base_url: str = "https://api.touchpay.example.com"
    touchpay_token: str = ""
    use_mock_touchpay: bool = True

    # Connection string do Supabase no formato asyncpg:
    #   postgresql+asyncpg://postgres:[SENHA]@db.[ref].supabase.co:5432/postgres
    # Lida do .env, NUNCA hardcoded.
    database_url: str = ""

    # Segredo ("pepper") usado no HMAC-SHA256 do CPF. Deve ser trocado em
    # produção e mantido em segredo — quem tiver a pepper pode recomputar
    # hashes de CPF por força bruta.
    cpf_pepper: str = "dev-insecure-pepper-change-me"

    # --- WhatsApp / Evolution API ---
    evolution_base_url: str = ""
    evolution_api_key: str = ""       # autentica o ENVIO de mensagens via Evolution
    evolution_instance: str = ""      # nome da instância na Evolution
    webhook_token: str = ""           # segredo validado no RECEBIMENTO do webhook
    # Quando true, usa o MockMessageSender (não envia nada de verdade).
    use_mock_whatsapp: bool = True

    # --- WhatsApp Cloud API (Meta) ---
    # Convivem com a Evolution de propósito: dá para cadastrar e testar a Meta
    # com o canal atual no ar, e voltar atrás sem downtime.
    #
    # Token que NÓS inventamos. A Meta o devolve no GET de verificação da URL;
    # se não bater, recusamos e ela não cadastra o webhook.
    meta_verify_token: str = ""
    # App Secret (painel da Meta > Configurações > Básico). Assina o corpo de
    # cada evento em X-Hub-Signature-256. Sem ele, qualquer um forjaria eventos.
    meta_app_secret: str = ""
    # Token de acesso da Graph API (use um de System User; o da tela de setup
    # expira em 24h) e o id do número de onde as mensagens saem.
    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    # Segredo embutido no CAMINHO do webhook, para quando o App Secret ainda
    # não está disponível. A Meta só chama uma URL (não manda header
    # customizado), então o caminho é o único segredo que dá para exigir dela.
    # Vazio = só o HMAC protege. Os dois preenchidos = os dois valem.
    meta_webhook_path_secret: str = ""
    meta_graph_version: str = "v21.0"
    # Quando true, o ENVIO passa a sair pela Cloud API em vez da Evolution.
    # O recebimento é independente: os dois webhooks podem ficar ativos juntos.
    use_meta_whatsapp: bool = False
    # Nome do template aprovado da pergunta de afiliado pós-compra — a ÚNICA
    # mensagem que o sistema envia sem o cliente ter escrito antes. Todo o
    # resto é resposta dentro da janela de 24h e sai como texto livre.
    # Vazio = cai para texto livre, que a Meta RECUSA com o erro 131047 fora
    # da janela (ver o aviso em `affiliate_prompt._send_prompt`).
    meta_affiliate_template: str = ""
    # Código de idioma do template, exatamente como cadastrado na Meta.
    meta_template_language: str = "pt_BR"
    # Repasse ao sistema de BOAS-VINDAS (saudação por voz na Alexa). A loja usa
    # o MESMO número nos dois sistemas, e a Meta entrega os eventos a UM
    # callback só — este. O boas-vindas pede consentimento LGPD por template
    # com botões; sem repasse, o toque do cliente em "Aceito" nunca chega lá.
    #
    # URL COMPLETA do webhook do boas-vindas. Carrega o segredo de caminho
    # dele: só por variável de ambiente, nunca em código nem em log.
    # Vazio = não repassa.
    boasvindas_forward_url: str = ""
    # Curto de propósito: o repasse roda depois do 200 à Meta, mas um outro
    # sistema lento não pode ficar segurando conexão daqui.
    boasvindas_forward_timeout_seconds: float = 3.0
    # Payloads dos botões do template do boas-vindas, separados por vírgula
    # (sem diferenciar maiúsculas, espaços ignorados). Só eventos com um desses
    # toques são repassados — minimização: o boas-vindas não precisa ver as
    # conversas com o robô de fidelidade. E esses toques NUNCA entram na
    # conversa daqui, com o repasse ligado ou não. Vazio = nenhum botão é
    # reconhecido (nada repassado, nada filtrado).
    boasvindas_button_payloads: str = "ACEITO,NAO_ACEITO"

    # --- Estado conversacional (SessionStore) ---
    # Connection string do Redis. Upstash/Redis Cloud usam TLS -> `rediss://`
    # (dois "s"). Vazia = sem Redis (usa o store em memória). Ex:
    #   rediss://default:[SENHA]@[HOST].upstash.io:6379
    redis_url: str = ""
    # Quando true, o estado da conversa vai para o Redis (compartilhado entre
    # processos/workers). Default FALSE: mantém o InMemorySessionStore, que só
    # serve para 1 worker single-process. Ligue em produção multi-worker OU
    # quando o worker de polling roda em processo separado.
    use_redis_session_store: bool = False
    # TTL (segundos) de inatividade de uma conversa. Vale para os dois stores.
    # Só cobre a conversa normal (menu, cadastro, resgate). A pergunta de
    # indicação pós-compra NÃO depende disto: fica no banco
    # (`affiliate_questions`), porque o cliente responde ao template horas depois.
    session_ttl_seconds: int = 3600
    # Por quantos dias a resposta à pergunta de indicação ainda é aceita. Depois
    # disso, um "sim"/"não" do cliente volta a ser tratado como conversa normal.
    affiliate_answer_window_days: int = 7

    # --- Agendador de polling (worker de ingestão) ---
    # Intervalo, em segundos, entre ciclos de polling da TouchPay. Cada ciclo lê
    # as compras da janela recente, credita pontos e dispara a pergunta de
    # afiliado no WhatsApp. Ex: 300 = a cada 5 minutos.
    poll_interval_seconds: int = 300
    # Tamanho da janela lida a cada ciclo, em minutos. Deve ser MAIOR que o
    # intervalo (janelas sobrepostas), para nenhuma compra escapar entre ciclos.
    # A idempotência (constraint única em source_reference) garante que a
    # sobreposição não credite a mesma compra duas vezes.
    poll_window_minutes: int = 30
    # PDV (pointOfSaleId) da Base Sports. Reservado para filtrar a leitura por
    # estabelecimento. NOTA: `ingest_transactions` ainda não recebe esse filtro
    # (lê todos os PDVs do token); fiar aqui é um passo futuro se o token
    # enxergar mais de um estabelecimento. Vazio = todos.
    poll_point_of_sale_id: int | None = None
    # Quando true, a API sobe o worker de polling NO MESMO PROCESSO (lifespan).
    # Simples e correto para 1 worker / testes ponta a ponta (o webhook e o
    # dispatch compartilham o mesmo session_store em memória). Em produção
    # multi-processo, deixe false e rode o worker à parte (`python -m app.worker`)
    # com um SessionStore compartilhado (Redis).
    run_worker_in_app: bool = False

    # --- Log ---
    # Nível dos logs da aplicação (`fidelidade.*`). INFO mostra cada ciclo do
    # worker (compras lidas/creditadas, perguntas enviadas); WARNING esconde.
    log_level: str = "INFO"

    # --- API administrativa (JWT) ---
    # Segredo de assinatura do JWT. TROQUE em produção (segredo forte).
    jwt_secret: str = "dev-insecure-jwt-secret-change-me"
    jwt_expiration_hours: int = 8

    # Origens permitidas para CORS (painel admin). "*" libera todas (ok em dev,
    # pois a auth é via Bearer no header, sem cookies). Em produção, liste as
    # origens reais separadas por vírgula. Ex: "https://painel.basesports.com".
    cors_origins: str = "*"

    @field_validator("poll_point_of_sale_id", mode="before")
    @classmethod
    def _empty_str_is_none(cls, value: object) -> object:
        # No `.env`, `POLL_POINT_OF_SALE_ID=` (vazio) deve virar None, não erro
        # de parsing. Sem isso, a app nem sobe quando a var existe mas está vazia.
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    """Retorna as configurações (cacheadas) da aplicação."""
    return Settings()

"""Convivência com o sistema de BOAS-VINDAS no mesmo número de WhatsApp.

A loja usa um número só para dois sistemas: este (fidelidade) e o de
boas-vindas (saudação por voz na Alexa quando o cliente faz check-in). A Meta
aceita UM callback por app, e é este sistema que recebe tudo. O boas-vindas
envia, pelo mesmo número, um template de consentimento LGPD com dois botões
(payloads ACEITO e NAO_ACEITO) e precisa receber o toque do cliente.

Duas consequências, e as duas moram aqui:

1. **Repasse.** O evento com o toque é reenviado ao boas-vindas. SÓ ele —
   minimização: o outro sistema não tem por que ver as conversas do cliente
   com o robô de fidelidade.
2. **Filtro.** O toque NÃO é conversa daqui. Entregue a `handle_message`, o
   rótulo "Aceito" cai no vocabulário do onboarding ("aceito" é um "sim"): o
   robô de fidelidade responderia a um botão que não é dele e, se o cliente
   estivesse no passo do regulamento, o consentimento dado à SAUDAÇÃO seria
   contado como aceite do programa de fidelidade — finalidade diferente, o
   que a LGPD não permite.

Regras do repasse, espelhando as do repasse que já existe do lado de lá:

- **Bytes idênticos**, com a assinatura `X-Hub-Signature-256`. Reserializar o
  JSON mudaria o digest e o outro lado não conseguiria validar a origem.
- **Nunca levanta.** Roda depois do 200 à Meta; um erro aqui não tem a quem
  voltar, só vira rótulo no log.
- **Nunca cria laço.** Os dois sistemas conseguem, por configuração, apontar
  um para o outro. Evento que já chega marcado como repasse não é reenviado.
- **Nunca loga a URL** (ela carrega o segredo de caminho do outro sistema),
  nem telefone, nem conteúdo de mensagem.

Limitações assumidas:

- É best-effort. Se o boas-vindas estiver fora no instante do toque, aquele
  evento se perde para ele — a Meta já recebeu 200 e não reenvia. O cliente
  pode tocar de novo; se isso passar a importar, o próximo passo é fila com
  repetição.
- A minimização é por LOTE, não por mensagem. Se a Meta agrupar no mesmo POST
  um toque do boas-vindas e uma mensagem de outro cliente para este robô, o
  lote vai inteiro: recortar o JSON quebraria a assinatura. Com o volume de
  uma loja, lote misto é raro.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import httpx

from app.whatsapp.meta.webhook_schema import MetaMessage, MetaWebhook

logger = logging.getLogger("fidelidade.boasvindas")

# Cabeçalhos que o outro lado precisa para interpretar e validar o evento. O
# resto (host, content-length, cookies, IP de origem) é do NOSSO transporte e
# não deve ser propagado.
_REPASSAR = ("content-type", "x-hub-signature-256", "x-hub-signature")

# Marca de "isto já é um repasse". É o MESMO nome que o boas-vindas usa no
# repasse dele, de propósito: uma marca só vale para os dois lados, e um evento
# marcado não volta, seja qual for a configuração de cada um.
CABECALHO_REPASSE = "X-Boasvindas-Repassado"
MARCA_REPASSE = "nouva-fidelidade"


def normalize_payloads(raw: str | None) -> frozenset[str]:
    """Lista da config ("ACEITO, nao_aceito") -> {"ACEITO", "NAO_ACEITO"}.

    Maiúsculas e espaços normalizados dos dois lados: um payload digitado à mão
    no painel da Meta com outra caixa não pode fazer o toque escapar do filtro
    e cair na conversa.
    """
    return frozenset(
        parte.strip().upper() for parte in (raw or "").split(",") if parte.strip()
    )


def is_boasvindas_payload(valor: str | None, payloads: frozenset[str]) -> bool:
    """O identificador de botão pertence ao template do boas-vindas?"""
    return bool(valor) and valor.strip().upper() in payloads


def is_boasvindas_tap(message: MetaMessage, payloads: frozenset[str]) -> bool:
    """UMA mensagem é toque num botão do boas-vindas?

    Vale nos dois formatos da Meta: `button.payload` (botão de template) e
    `interactive.button_reply.id` (botão interativo). Texto digitado nunca
    conta — "aceito" escrito à mão é conversa daqui.
    """
    return is_boasvindas_payload(message.button_payload(), payloads)


def contains_boasvindas_tap(webhook: MetaWebhook, payloads: frozenset[str]) -> bool:
    """O lote tem ao menos um toque do boas-vindas? Decide se há repasse."""
    for entry in webhook.entry or []:
        for change in entry.changes or []:
            value = change.value
            if value is None:
                continue
            for message in value.messages or []:
                if is_boasvindas_tap(message, payloads):
                    return True
    return False


async def repassar(
    url: str | None,
    raw_body: bytes,
    headers: Mapping[str, str],
    timeout_seconds: float,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Reenvia o evento cru ao boas-vindas. Devolve um rótulo; nunca levanta.

    Rótulos: "off" (sem URL), "laco_evitado", "ok", "http_<status>", "erro".
    """
    try:
        if not url or not url.strip():
            return "off"

        # Chaves em minúsculas: o `Headers` do Starlette já é assim, mas um
        # dict comum (teste, outro chamador) pode não ser.
        recebidos = {str(nome).lower(): valor for nome, valor in headers.items()}

        # Trava de laço: nunca repassar o que já é um repasse.
        if CABECALHO_REPASSE.lower() in recebidos:
            logger.info("repasse ao boas-vindas: laco_evitado")
            return "laco_evitado"

        cabecalhos = {
            nome: recebidos[nome]
            for nome in _REPASSAR
            if recebidos.get(nome) is not None
        }
        cabecalhos.setdefault("content-type", "application/json")
        cabecalhos[CABECALHO_REPASSE] = MARCA_REPASSE

        proprio = client is None
        http = client or httpx.AsyncClient(timeout=timeout_seconds)
        try:
            resposta = await http.post(
                url.strip(),
                content=raw_body,
                headers=cabecalhos,
                timeout=timeout_seconds,
            )
        finally:
            if proprio:
                await http.aclose()

        # 3xx também é falha: não seguimos redirect (a URL tem segredo, e um
        # redirect a levaria para outro lugar), então o evento não foi entregue.
        if not resposta.is_success:
            rotulo = f"http_{resposta.status_code}"
            logger.warning("repasse ao boas-vindas: %s", rotulo)
            return rotulo
        return "ok"
    except Exception as exc:  # noqa: BLE001 — o outro sistema fora do ar não derruba nada aqui
        # Só o TIPO do erro: a mensagem de exceções do httpx pode conter a
        # URL, e a URL carrega o segredo do outro sistema.
        logger.warning("repasse ao boas-vindas: erro (%s)", type(exc).__name__)
        return "erro"

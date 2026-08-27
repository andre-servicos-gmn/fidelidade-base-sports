"""Agendador de polling: o "coração" que faz o sistema andar sozinho.

A API TouchPay NÃO avisa quando há uma compra — ela só responde quando
perguntamos. Este worker é quem pergunta, em intervalos regulares. A cada ciclo:

  1. lê as compras da janela recente na TouchPay (`ingest_transactions`),
  2. credita os pontos no ledger (idempotente: a mesma compra nunca conta 2x),
  3. dispara no WhatsApp a pergunta de afiliado das compras recém-creditadas
     (`dispatch_affiliate_prompts`).

Janela sobreposta
-----------------
Cada ciclo lê `[agora - poll_window_minutes, agora]`. A janela é maior que o
intervalo de propósito: ciclos consecutivos se sobrepõem, então nenhuma compra
escapa "no vão" entre dois ciclos. A idempotência (constraint única em
`LedgerEntry.source_reference`) garante que a sobreposição não credite pontos
repetidos — por isso NÃO precisamos persistir um checkpoint de "até onde li".

Estado compartilhado (importante)
---------------------------------
O worker usa EXATAMENTE o mesmo `session_store` e `message_sender` da API
(`app.dependencies`), que são singletons de processo. Rodar o worker no MESMO
processo da API (`RUN_WORKER_IN_APP=true`) faz o `dispatch` gravar o estado
`AWAITING_AFFILIATE_CODE` no mesmo store que o webhook lê ao receber a resposta
do cliente. Para rodar o worker em processo separado, ligue o Redis
(`USE_REDIS_SESSION_STORE=true`) — só assim o estado gravado aqui casa com o
webhook do outro processo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.dependencies import get_message_sender, get_session_factory, get_session_store
from app.integrations.touchpay.client import TouchPayClient
from app.services.ingestion_service import IngestionReport, ingest_transactions
from app.whatsapp.affiliate_prompt import dispatch_affiliate_prompts

logger = logging.getLogger("fidelidade.worker")


async def run_once(touchpay_client: TouchPayClient) -> IngestionReport:
    """Executa UM ciclo de polling. Retorna o relatório da ingestão.

    Isolado do loop para ser testável e chamável avulso (ex: um cron externo
    que invoca `python -m app.worker --once`).

    Cada ciclo abre a sua própria `AsyncSession` (via `session_factory`), no
    mesmo padrão da conversa — nunca reaproveita sessão entre ciclos.
    """
    settings = get_settings()
    session_factory = get_session_factory()

    now = datetime.now(timezone.utc)
    min_date = now - timedelta(minutes=settings.poll_window_minutes)

    async with session_factory() as session:
        report = await ingest_transactions(
            session,
            touchpay_client,
            min_date=min_date,
            max_date=now,
        )

    # Dispara as perguntas de afiliado FORA da sessão de ingestão: envolve I/O
    # de rede (WhatsApp) e grava no session_store (não no banco). Usa os mesmos
    # singletons da API para o estado casar com o webhook.
    sent = 0
    if report.affiliate_prompts:
        sender = get_message_sender()
        store = get_session_store()
        sent = await dispatch_affiliate_prompts(
            report.affiliate_prompts, sender, store
        )

    logger.info(
        "ciclo de polling: lidas=%d creditadas=%d ja_processadas=%d "
        "sem_cpf=%d erros=%d pontos=%d prompts_afiliado_enviados=%d",
        report.total_read,
        report.credited,
        report.already_processed,
        report.skipped_no_cpf,
        report.errors,
        report.total_points_credited,
        sent,
    )
    return report


def _build_touchpay_client() -> TouchPayClient:
    """Cria o cliente TouchPay pelo factory canônico (mock vs real por config)."""
    # Importado aqui (não no topo) para evitar ciclo: main.py importa routers
    # que, indiretamente, poderiam importar o worker no futuro.
    from app.main import get_touchpay_client

    return get_touchpay_client()


async def run_forever(stop_event: asyncio.Event | None = None) -> None:
    """Loop infinito de polling: roda `run_once` a cada `poll_interval_seconds`.

    `stop_event` permite parada graciosa (setar o evento encerra o loop após o
    ciclo atual / o sleep corrente). Uma falha num ciclo é logada e NÃO derruba
    o loop — o próximo ciclo tenta de novo.
    """
    settings = get_settings()
    interval = settings.poll_interval_seconds
    stop_event = stop_event or asyncio.Event()

    client = _build_touchpay_client()
    logger.info(
        "worker iniciado: intervalo=%ds janela=%dmin mock_touchpay=%s "
        "mock_whatsapp=%s",
        interval,
        settings.poll_window_minutes,
        settings.use_mock_touchpay,
        settings.use_mock_whatsapp,
    )
    try:
        while not stop_event.is_set():
            try:
                await run_once(client)
            except Exception:  # noqa: BLE001 — um ciclo ruim não mata o worker
                logger.exception("falha no ciclo de polling; segue no próximo")

            # Espera o intervalo OU um pedido de parada (o que vier primeiro).
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass  # tempo esgotado -> hora do próximo ciclo
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            await aclose()
        logger.info("worker encerrado")


def main() -> None:
    """Entrypoint standalone: `python -m app.worker` (ou `--once`).

    - Sem argumentos: loop infinito (`run_forever`).
    - `--once`: executa um único ciclo e sai (útil para cron externo/depuração).
    """
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if "--once" in sys.argv[1:]:
        client = _build_touchpay_client()

        async def _one() -> None:
            try:
                await run_once(client)
            finally:
                aclose = getattr(client, "aclose", None)
                if aclose is not None:
                    await aclose()

        asyncio.run(_one())
    else:
        asyncio.run(run_forever())


if __name__ == "__main__":
    main()

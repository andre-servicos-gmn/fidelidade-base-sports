"""Ponto de entrada da aplicação FastAPI."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.integrations.touchpay.client import TouchPayClient
from app.integrations.touchpay.mock_client import MockTouchPayClient
from app.routes.admin.affiliates import router as admin_affiliates_router
from app.routes.admin.auth import router as admin_auth_router
from app.routes.admin.coupons import router as admin_coupons_router
from app.routes.admin.customers import router as admin_customers_router
from app.routes.admin.rules import router as admin_rules_router
from app.routes.whatsapp_webhook import router as whatsapp_router


def get_touchpay_client(settings: Settings | None = None) -> TouchPayClient:
    """Factory do cliente TouchPay (mesmo padrão do sender da Evolution).

    Default SEGURO: `use_mock_touchpay=True` -> mock (não toca a produção).
    Só com `use_mock_touchpay=False` retorna o cliente HTTP real, que faz
    APENAS leitura (a API TouchPay não tem sandbox).

    O cliente HTTP mantém uma conexão httpx aberta; quem o cria é responsável
    por fechá-lo (`await client.aclose()` ou usar como context manager).
    """
    settings = settings or get_settings()

    if settings.use_mock_touchpay:
        return MockTouchPayClient()

    from app.integrations.touchpay.http_client import HttpTouchPayClient

    return HttpTouchPayClient(
        base_url=settings.touchpay_base_url,
        token=settings.touchpay_token,
    )


logger = logging.getLogger("fidelidade.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Sobe (opcionalmente) o worker de polling no mesmo processo da API.

    Ligado por `run_worker_in_app=true` (config). O worker roda como uma task
    de background e é encerrado graciosamente no shutdown. Ver `app/worker.py`.
    """
    settings = get_settings()
    stop_event: asyncio.Event | None = None
    worker_task: asyncio.Task | None = None

    if settings.run_worker_in_app:
        from app.worker import run_forever

        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(run_forever(stop_event))
        logger.info("worker de polling iniciado no processo da API (lifespan).")

    try:
        yield
    finally:
        if worker_task is not None and stop_event is not None:
            stop_event.set()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        # Fecha o pool do Redis (no-op se estiver rodando em memória).
        from app.dependencies import close_redis_client

        await close_redis_client()


app = FastAPI(title="Fidelidade Base Sports", lifespan=lifespan)

# CORS para o painel admin (frontend separado). Bearer no header -> sem cookies,
# então allow_credentials=False e "*" é aceitável em dev.
_cors = get_settings().cors_origins.strip()
_origins = ["*"] if _cors == "*" else [o.strip() for o in _cors.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# O estado conversacional (session_store) é um singleton de processo, exposto
# via `app.dependencies.get_session_store`. Ver docstring de dependencies.py.
app.include_router(whatsapp_router)
app.include_router(admin_auth_router)
app.include_router(admin_rules_router)
app.include_router(admin_coupons_router)
app.include_router(admin_customers_router)
app.include_router(admin_affiliates_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Healthcheck simples."""
    return {"status": "ok"}

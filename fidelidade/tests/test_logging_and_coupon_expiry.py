"""Logs da aplicação visíveis e validade do cupom no resgate. Sem banco.

Os dois defeitos eram silenciosos: o worker creditava compras sem deixar
nenhuma linha no log, e o cliente recebia uma validade de cupom inventada.
A parte do resgate que toca o banco (filtro de vencidos) está em
`test_redemption.py`, como teste de integração.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.redemption import RedemptionResult
from app.logging_config import configure_logging
from app.services.redemption_service import coupon_expiry
from app.whatsapp import messages

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Logs                                                                         #
# --------------------------------------------------------------------------- #
def _capture(logger_name: str, message: str) -> str:
    """Emite `message` em INFO e devolve o que o handler da aplicação escreveu."""
    app_logger = logging.getLogger("fidelidade")
    handler = app_logger.handlers[0]
    buffer = io.StringIO()
    original = handler.setStream(buffer)
    try:
        logging.getLogger(logger_name).info(message)
    finally:
        handler.setStream(original)
    return buffer.getvalue()


def test_worker_info_reaches_the_log():
    """O ciclo do worker é INFO: antes era descartado pelo logger raiz em WARNING."""
    configure_logging("INFO")

    saida = _capture("fidelidade.worker", "ciclo de polling: creditadas=1")

    assert "fidelidade.worker" in saida
    assert "ciclo de polling: creditadas=1" in saida


def test_configure_logging_is_idempotent():
    """Chamar de novo (API + worker, reload) não duplica as linhas."""
    configure_logging("INFO")
    configure_logging("INFO")

    assert len(logging.getLogger("fidelidade").handlers) == 1


def test_third_party_info_stays_quiet():
    """Só a árvore `fidelidade` é ligada: o httpx não passa a logar toda request."""
    configure_logging("INFO")

    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_log_level_is_configurable():
    configure_logging("WARNING")
    try:
        assert not logging.getLogger("fidelidade.worker").isEnabledFor(logging.INFO)
    finally:
        configure_logging("INFO")


# --------------------------------------------------------------------------- #
# Validade do cupom no resgate                                                 #
# --------------------------------------------------------------------------- #
def test_registered_expiry_is_kept():
    """A validade cadastrada espelha o cupom real da TouchPay e não é trocada."""
    real = NOW + timedelta(days=10)

    assert coupon_expiry(real, NOW, expiration_days=30) == real


def test_registered_expiry_later_than_default_is_kept():
    """Nem para encurtar: o totem aceita até a data real do cupom."""
    real = NOW + timedelta(days=365)

    assert coupon_expiry(real, NOW, expiration_days=30) == real


def test_default_applies_only_without_registered_expiry():
    assert coupon_expiry(None, NOW, expiration_days=30) == NOW + timedelta(days=30)


# --------------------------------------------------------------------------- #
# Data mostrada ao cliente                                                     #
# --------------------------------------------------------------------------- #
# O painel grava "válido até 31/12, 23:59 de Brasília", que no banco vira
# 02:59 de 01/01 em UTC. Formatar sem converter mostrava um dia a mais.
_FIM_DO_ANO_EM_UTC = datetime(2027, 1, 1, 2, 59, 59, tzinfo=timezone.utc)


def test_redemption_message_shows_brasilia_date():
    result = RedemptionResult(
        coupon_code="BASE-R25-0001",
        discount_type="FIXED",
        discount_value=Decimal("25.00"),
        points_spent=500,
        balance_after=100,
        expires_at=_FIM_DO_ANO_EM_UTC,
        min_order_value=None,
    )

    texto = messages.redemption_success(result)

    assert "31/12/2026" in texto
    assert "01/01/2027" not in texto


def test_coupons_list_shows_brasilia_date():
    texto = messages.coupons_list(
        [
            {
                "code": "BASE-R25-0001",
                "discount_type": "FIXED",
                "discount_value": 25.0,
                "status": "ALLOCATED",
                "expires_at": _FIM_DO_ANO_EM_UTC,
                "min_order_value": None,
            }
        ]
    )

    assert "vale até 31/12/2026" in texto

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

from app.logging_config import configure_logging
from app.services.redemption_service import coupon_expiry

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

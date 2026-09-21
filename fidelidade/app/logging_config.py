"""Configuração de log dos módulos da aplicação (`fidelidade.*`).

Por que existe: rodando dentro da API, ninguém configurava os loggers da
aplicação. O uvicorn configura só os DELE (`uvicorn`, `uvicorn.access`), e o
logger raiz do Python fica em WARNING — então todo `logger.info` era descartado
em silêncio. Na prática, o worker de polling rodava sem deixar rastro: nenhum
"ciclo de polling", nenhuma compra creditada aparecia no log, e não havia como
saber se ele estava funcionando.

Configuramos só a árvore `fidelidade`, não o logger raiz: assim não ligamos o
INFO de bibliotecas de terceiros (o httpx, por exemplo, loga toda requisição).
"""

from __future__ import annotations

import logging

_LOGGER_NAME = "fidelidade"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Liga os logs da aplicação no nível dado. Idempotente.

    Não chame `logging.basicConfig` junto: o logger raiz ganharia um handler e
    cada linha sairia duas vezes. A propagação fica ligada de propósito — é
    por ela que o `caplog` do pytest enxerga estes logs.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level.upper())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)

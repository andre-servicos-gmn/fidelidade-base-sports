"""Configuração compartilhada dos testes.

=========================== POR QUE ISTO EXISTE =============================
Os testes de integração não são somente-leitura: eles SEMEIAM e depois APAGAM
dados, e os filtros de limpeza usam as constantes do seed de produção
(`scripts.seed_config.BASE_RULE_NAME` e `TEST_PREFIX`). Rodá-los contra o banco
real, portanto, DESTRÓI configuração de verdade — a regra BASE e todo o pool de
cupons `TESTE-%` desaparecem, e o sistema fica sem recompensa nenhuma.

Isso não é hipotético: aconteceu. Por isso os testes de integração agora exigem
um banco SEPARADO, apontado por `TEST_DATABASE_URL`. Sem essa variável eles são
PULADOS — nunca caem por engano no banco de produção.

    # descartável, só para testes:
    TEST_DATABASE_URL=postgresql+asyncpg://user:senha@host:5432/fidelidade_test
    pytest -m integration

Quando `TEST_DATABASE_URL` existe, ela sobrepõe `DATABASE_URL` para o processo
inteiro de teste: as fixtures leem `get_settings().database_url`, então basta
plantar o valor no ambiente (que vence o `.env`) e invalidar o cache de
`get_settings`.

Sem ela, `DATABASE_URL` aponta para um endereço que não existe. Pular os testes
marcados não bastava: todo teste que sobe o app com `TestClient` dispara o
lifespan, e com `RUN_WORKER_IN_APP=true` no `.env` o worker de polling ligava e
se conectava ao banco de produção. Por isso o worker também fica sempre
desligado nos testes — quem testa o worker chama `run_once` direto.
=============================================================================
"""

from __future__ import annotations

import os

import pytest

from app.config import get_settings

TEST_DB_ENV = "TEST_DATABASE_URL"

_SKIP_REASON = (
    f"{TEST_DB_ENV} não definida. Os testes de integração apagam cupons e "
    "regras semeadas; para não destruir o banco de produção, eles só rodam "
    "contra um banco descartável apontado por essa variável."
)


# Porta 1 em localhost: conexão recusada na hora, nunca um banco de verdade.
_UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://nobody:x@127.0.0.1:1/none"


def _test_database_url() -> str:
    return os.getenv(TEST_DB_ENV, "").strip()


def pytest_configure(config: pytest.Config) -> None:
    """Isola a aplicação do banco de produção durante os testes."""
    # Variável de ambiente vence o `.env` no pydantic-settings; limpar o cache
    # garante que ninguém já tenha lido o valor antigo.
    os.environ["RUN_WORKER_IN_APP"] = "false"
    os.environ["DATABASE_URL"] = _test_database_url() or _UNREACHABLE_DATABASE_URL
    get_settings.cache_clear()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Sem banco de teste dedicado, pula tudo que for marcado `integration`."""
    if _test_database_url():
        return
    skip = pytest.mark.skip(reason=_SKIP_REASON)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)

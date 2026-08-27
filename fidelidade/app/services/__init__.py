"""Serviços de aplicação: orquestram domínio + persistência.

Diferente de `app/domain` (puro), estes serviços recebem uma `AsyncSession` e
falam com o banco. Continuam sem conhecer detalhes da TouchPay: a ingestão
depende apenas da interface `TouchPayClient`.
"""

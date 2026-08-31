"""Validação da assinatura X-Hub-Signature-256 da Meta.

A Meta assina o CORPO CRU de cada evento com HMAC-SHA256 usando o App Secret.
É a única prova de que o evento veio dela — sem isso, qualquer um que conheça a
URL pode forjar mensagens e fazer o bot creditar pontos ou entregar cupons.

Dois cuidados que parecem detalhe e não são:

1. Assinar o corpo CRU, byte a byte. Se você validar sobre o JSON já parseado e
   re-serializado, a menor diferença de formatação (espaço, ordem de chave,
   escape de unicode) muda o hash e a validação falha para sempre.
2. Comparar com `hmac.compare_digest`, não com `==`. Comparação comum sai no
   primeiro byte diferente, e esse tempo vaza informação suficiente para
   descobrir a assinatura correta byte a byte.
"""

from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """True se `header` for a assinatura válida de `raw_body`.

    FAIL-CLOSED: sem segredo configurado ou sem header, retorna False. Preferimos
    recusar tudo a aceitar qualquer coisa por descuido de configuração.
    """
    if not app_secret or not header:
        return False
    if not header.startswith(_PREFIX):
        return False

    esperado = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    recebido = header[len(_PREFIX):]

    # Comparar em BYTES, não em str. `compare_digest` com duas strings levanta
    # TypeError se qualquer uma tiver caractere não-ASCII — e o header vem do
    # atacante (cabeçalhos HTTP decodificam como latin-1). Com str, uma
    # assinatura forjada com um byte alto derruba a requisição em 500 em vez de
    # recusar com 401. Em bytes, a comparação é sempre segura.
    return hmac.compare_digest(
        esperado.encode("ascii"), recebido.encode("latin-1", "replace")
    )

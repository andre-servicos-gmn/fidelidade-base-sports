"""Tradução de telefone Evolution <-> formato canônico da Fase 5.

ESTE é o ponto onde os dois formatos se encontram. Errar aqui faz o cliente
vinculado "sumir" (o número não casa com o `Customer.phone` gravado).

- Evolution entrega: `5511987654321@s.whatsapp.net` (código de país 55 + DDD +
  número, às vezes com sufixo de JID e/ou device `:NN`).
- Fase 5 (canônico): `11987654321` — só dígitos, com DDD, SEM o 55.

Reaproveitamos `normalize_phone` do identity_service para garantir que a
definição de "canônico" seja EXATAMENTE a mesma usada ao gravar/buscar no banco.
"""

from __future__ import annotations

from app.services.identity_service import normalize_phone


def evolution_to_canonical(raw: str) -> str:
    """Converte o JID/numero da Evolution para o canônico da Fase 5.

    Remove o sufixo de JID (`@s.whatsapp.net`, `@c.us`, etc.) e o sufixo de
    device (`:12`), depois normaliza (que remove o 55 inicial). Idempotente:
    números que já vierem sem o 55 ou sem sufixo passam intactos.
    """
    if not raw:
        return ""
    # Remove o domínio do JID e o sufixo de device antes de normalizar.
    local = raw.split("@", 1)[0].split(":", 1)[0]
    return normalize_phone(local)


def canonical_to_evolution(phone: str) -> str:
    """Converte o canônico para o formato de ENVIO da Evolution.

    Formato exato: dígitos com código de país, SEM sufixo de JID —
    `5511987654321` (55 + DDD + número). O endpoint sendText da Evolution
    aceita esse `number`. Idempotente: aceita tanto `11987654321` quanto
    `5511987654321` e sempre devolve a forma com 55.
    """
    digits = normalize_phone(phone)  # canonicaliza (tira 55 se já vier com ele)
    if not digits:
        return ""
    return "55" + digits

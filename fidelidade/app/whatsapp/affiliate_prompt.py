"""Disparo do prompt de afiliado após uma compra.

A ingestão (`ingest_transactions`) NÃO envia mensagens — ela só coleta os
`AffiliatePrompt` das compras recém-creditadas (clientes com telefone). Quem
roda o ciclo (futuro agendador do polling) chama `dispatch_affiliate_prompts`
para, por compra: gravar o estado `AWAITING_AFFILIATE_CODE` (com a compra a
atribuir) e enviar a pergunta via `MessageSender`.

A chave da sessão e o número de envio passam ambos por `normalize_phone`
(mesma definição de "canônico" do webhook), garantindo que a resposta do
cliente case com o estado gravado aqui.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.services.identity_service import normalize_phone
from app.whatsapp import messages
from app.whatsapp.evolution.sender import MessageSender
from app.whatsapp.session_store import (
    ConversationState,
    ConversationStep,
    SessionStore,
)

logger = logging.getLogger("fidelidade.affiliate_prompt")


@dataclass(frozen=True)
class AffiliatePrompt:
    """Uma compra recém-creditada que deve gerar a pergunta de afiliado."""

    phone: str
    points: int
    source_reference: str
    # Valor da compra em reais — base do cálculo dos pontos do afiliado.
    amount: Decimal = Decimal(0)


async def dispatch_affiliate_prompts(
    prompts: list[AffiliatePrompt],
    sender: MessageSender,
    store: SessionStore,
) -> int:
    """Grava o estado e envia a pergunta de cada prompt. Retorna quantos enviou."""
    sent = 0
    for prompt in prompts:
        phone_n = normalize_phone(prompt.phone)
        if not phone_n:
            continue
        await store.set(
            phone_n,
            ConversationState(
                step=ConversationStep.AWAITING_AFFILIATE_CODE,
                data={
                    "source_reference": prompt.source_reference,
                    "points": prompt.points,
                    # String para ser serializável (futuro store Redis) sem
                    # perder precisão do Decimal.
                    "amount": str(prompt.amount),
                },
            ),
        )
        try:
            await sender.send_text(
                phone_n, messages.ask_affiliate_code(prompt.points)
            )
            sent += 1
        except Exception:  # noqa: BLE001 — ver comentário abaixo
            # Uma falha de envio NÃO pode abortar o lote. Sem este try, o
            # primeiro erro (número inexistente, instabilidade, ou — na Cloud
            # API — a recusa por mensagem fora da janela de 24h) interromperia
            # o for e os demais clientes nunca receberiam a pergunta. E, como a
            # compra já foi creditada e o ledger é idempotente, esse prompt
            # nunca mais seria gerado: perda silenciosa e definitiva.
            logger.exception(
                "falha ao enviar pergunta de afiliado para %s; seguindo com o "
                "restante do lote",
                phone_n,
            )
    return sent

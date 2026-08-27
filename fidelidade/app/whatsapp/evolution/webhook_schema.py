"""Schema TOLERANTE do webhook da Evolution (evento messages.upsert).

O payload da Evolution é aninhado e varia entre versões. Por isso modelamos com
campos opcionais, `extra="allow"` e extração defensiva. Lemos apenas o que
importa:

- `data.key.remoteJid`  -> número do remetente (JID).
- `data.key.fromMe`     -> se true, é o próprio bot: IGNORAR (evita loop).
- `data.message.conversation`              -> texto simples, OU
  `data.message.extendedTextMessage.text`  -> texto com formatação/reply.
- `data.pushName`       -> nome de exibição (informativo).

Qualquer evento que não seja uma mensagem de texto de um cliente (status, mídia,
fromMe) faz `extract_text_message()` retornar None — o webhook então ignora.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, field_validator

_TOLERANT = ConfigDict(extra="allow")


@dataclass
class IncomingTextMessage:
    """Mensagem de texto já extraída do payload."""

    remote_jid: str
    text: str
    push_name: str | None = None


class EvolutionKey(BaseModel):
    model_config = _TOLERANT
    remoteJid: str | None = None
    fromMe: bool | None = None
    id: str | None = None


class EvolutionMessage(BaseModel):
    model_config = _TOLERANT
    conversation: str | None = None
    extendedTextMessage: dict | None = None


class EvolutionData(BaseModel):
    model_config = _TOLERANT
    key: EvolutionKey | None = None
    message: EvolutionMessage | None = None
    messageType: str | None = None
    pushName: str | None = None


class EvolutionWebhook(BaseModel):
    """Envelope do webhook. `data` pode vir como objeto ou lista (batch)."""

    model_config = _TOLERANT
    event: str | None = None
    instance: str | None = None
    data: EvolutionData | None = None

    @field_validator("data", mode="before")
    @classmethod
    def _unwrap_list(cls, value: object) -> object:
        # Algumas versões mandam `data` como lista de mensagens.
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def extract_text_message(self) -> IncomingTextMessage | None:
        """Extrai a mensagem de texto do cliente, ou None se não for o caso."""
        data = self.data
        if data is None or data.key is None:
            return None
        if data.key.fromMe:  # mensagem do próprio bot -> ignorar
            return None
        jid = data.key.remoteJid
        if not jid:
            return None

        message = data.message
        if message is None:
            return None
        text = message.conversation
        if text is None and message.extendedTextMessage:
            text = message.extendedTextMessage.get("text")
        if not text or not text.strip():
            return None

        return IncomingTextMessage(
            remote_jid=jid, text=text, push_name=data.pushName
        )

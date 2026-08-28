"""Schema TOLERANTE do webhook da Cloud API (Meta).

Estrutura do payload (bem mais aninhada que a da Evolution):

    entry[].changes[].value.messages[]   -> mensagens recebidas
    entry[].changes[].value.statuses[]   -> entregue/lido: IGNORAR

Como na Evolution, modelamos com campos opcionais e `extra="allow"`: a Meta
acrescenta campos entre versões, e um schema rígido quebraria o webhook inteiro
por um campo novo que nem usamos.

Tipos de mensagem tratados:
- `text`        -> `text.body`
- `interactive` -> resposta de botão ou de lista. Aqui vem a graça da API
  oficial: botões funcionam de verdade. Usamos o TÍTULO do botão como texto,
  porque a máquina de conversa já entende "1"/"2"/"Consultar saldo".

Qualquer outra coisa (áudio, imagem, status, reação) devolve None e o webhook
ignora sem erro.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

_TOLERANT = ConfigDict(extra="allow")


@dataclass
class IncomingTextMessage:
    """Mensagem já extraída do payload."""

    from_number: str          # "5511987654321" (sem o +)
    text: str
    contact_name: str | None = None
    message_id: str | None = None


class MetaText(BaseModel):
    model_config = _TOLERANT
    body: str | None = None


class MetaReply(BaseModel):
    model_config = _TOLERANT
    id: str | None = None
    title: str | None = None


class MetaInteractive(BaseModel):
    model_config = _TOLERANT
    type: str | None = None
    button_reply: MetaReply | None = None
    list_reply: MetaReply | None = None

    def resolved_text(self) -> str | None:
        """Texto equivalente ao que o cliente 'digitou' ao tocar no botão."""
        for reply in (self.button_reply, self.list_reply):
            if reply is not None and reply.title:
                return reply.title
        # Sem título, o id serve: costuma ser "1"/"2"/"3", que o menu entende.
        for reply in (self.button_reply, self.list_reply):
            if reply is not None and reply.id:
                return reply.id
        return None


class MetaMessage(BaseModel):
    model_config = _TOLERANT
    id: str | None = None
    type: str | None = None
    text: MetaText | None = None
    interactive: MetaInteractive | None = None
    # Campo "from" é palavra reservada em Python; o alias resolve.
    from_: str | None = None

    def __init__(self, **data):
        if "from" in data:
            data["from_"] = data.pop("from")
        super().__init__(**data)

    def resolved_text(self) -> str | None:
        if self.text is not None and self.text.body:
            return self.text.body
        if self.interactive is not None:
            return self.interactive.resolved_text()
        return None


class MetaContact(BaseModel):
    model_config = _TOLERANT
    profile: dict | None = None

    def name(self) -> str | None:
        return (self.profile or {}).get("name")


class MetaValue(BaseModel):
    model_config = _TOLERANT
    messages: list[MetaMessage] | None = None
    contacts: list[MetaContact] | None = None
    statuses: list[dict] | None = None


class MetaChange(BaseModel):
    model_config = _TOLERANT
    field: str | None = None
    value: MetaValue | None = None


class MetaEntry(BaseModel):
    model_config = _TOLERANT
    id: str | None = None
    changes: list[MetaChange] | None = None


class MetaWebhook(BaseModel):
    """Envelope do webhook da Cloud API."""

    model_config = _TOLERANT
    object: str | None = None
    entry: list[MetaEntry] | None = None

    def extract_text_message(self) -> IncomingTextMessage | None:
        """Primeira mensagem de texto/botão do payload, ou None.

        Devolve None para eventos de status (entregue/lido) e para tipos de
        mídia que não tratamos — o webhook responde 200 e segue.
        """
        for entry in self.entry or []:
            for change in entry.changes or []:
                value = change.value
                if value is None or not value.messages:
                    continue

                nome = None
                if value.contacts:
                    nome = value.contacts[0].name()

                for message in value.messages:
                    texto = message.resolved_text()
                    if texto and message.from_:
                        return IncomingTextMessage(
                            from_number=message.from_,
                            text=texto,
                            contact_name=nome,
                            message_id=message.id,
                        )
        return None

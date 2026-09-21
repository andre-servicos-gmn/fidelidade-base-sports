"""Schema TOLERANTE do webhook da Cloud API (Meta).

Estrutura do payload (bem mais aninhada que a da Evolution):

    entry[].changes[].value.messages[]   -> mensagens recebidas
    entry[].changes[].value.statuses[]   -> entregue/lido: IGNORAR

Como na Evolution, modelamos com campos opcionais e `extra="allow"`: a Meta
acrescenta campos entre versões, e um schema rígido quebraria o webhook inteiro
por um campo novo que nem usamos.

Tipos de mensagem tratados:
- `text`        -> `text.body`
- `interactive` -> resposta de botão ou de lista de uma mensagem INTERATIVA
  (só dentro da janela de 24h). Usamos o TÍTULO do botão como texto, porque a
  máquina de conversa já entende "1"/"2"/"Consultar saldo".
- `button`      -> resposta de botão de TEMPLATE. Formato distinto do de cima
  (ver `MetaButton`); é o que chega quando o cliente responde à pergunta de
  afiliado pós-compra. Também é o formato do pedido de consentimento do
  sistema de boas-vindas, que usa o mesmo número: esses toques são separados
  no webhook pelo `button_payload` e não viram conversa daqui.

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
    # Identificador do botão tocado (payload de template ou id de botão
    # interativo), quando a mensagem é um toque. Diferente de `text`, que é o
    # RÓTULO: é por aqui que o webhook reconhece os botões do boas-vindas, que
    # não são conversa deste sistema (ver `app.whatsapp.meta.boasvindas`).
    button_payload: str | None = None


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


class MetaButton(BaseModel):
    """Clique em botão de TEMPLATE — formato DIFERENTE do botão interativo.

    Botão de mensagem interativa chega aninhado em `interactive.button_reply`.
    Botão de template chega num campo `button` de primeiro nível:

        {"type": "button", "button": {"text": "Sim", "payload": "SIM"}}

    Sem este ramo o clique cai no `resolved_text() -> None` e o webhook o
    descarta em silêncio: o cliente toca no botão e nada acontece, sem erro em
    lugar nenhum. É exatamente o caso da pergunta de afiliado pós-compra, que
    só pode sair como template (janela de 24h fechada).
    """

    model_config = _TOLERANT
    text: str | None = None
    payload: str | None = None

    def resolved_text(self) -> str | None:
        """Texto equivalente ao que o cliente 'digitou' ao tocar no botão."""
        # `text` é o RÓTULO VISÍVEL, que é o que a máquina de conversa
        # reconhece ("Sim, tenho o código" / "Não"). O `payload` (definido na
        # criação do template) é a reserva, caso a Meta omita o rótulo.
        return self.text or self.payload or None


class MetaMessage(BaseModel):
    model_config = _TOLERANT
    id: str | None = None
    type: str | None = None
    text: MetaText | None = None
    interactive: MetaInteractive | None = None
    button: MetaButton | None = None
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
        if self.button is not None:
            return self.button.resolved_text()
        return None

    def button_payload(self) -> str | None:
        """Identificador ESTÁVEL do botão tocado, ou None se não foi toque.

        O rótulo visível pode mudar com o texto do template; o payload (ou o
        id, no botão interativo) é o que quem enviou definiu para reconhecer a
        resposta. Os dois formatos da Meta, como em `resolved_text`.
        """
        if self.button is not None and self.button.payload:
            return self.button.payload
        reply = self.interactive.button_reply if self.interactive else None
        if reply is not None and reply.id:
            return reply.id
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

    def extract_text_messages(self) -> list[IncomingTextMessage]:
        """TODAS as mensagens de texto/botão do payload, na ordem.

        A Meta AGRUPA eventos: um único POST pode trazer várias mensagens, de
        clientes diferentes. Processar só a primeira e responder 200 descarta
        as demais silenciosamente — a Meta considera entregue e nunca reenvia.
        Cliente fica sem resposta e não há erro em lugar nenhum.

        Eventos de status (entregue/lido) e mídias que não tratamos não entram
        na lista; o webhook responde 200 e segue.
        """
        encontradas: list[IncomingTextMessage] = []
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
                        encontradas.append(
                            IncomingTextMessage(
                                from_number=message.from_,
                                text=texto,
                                contact_name=nome,
                                message_id=message.id,
                                button_payload=message.button_payload(),
                            )
                        )
        return encontradas

    def extract_text_message(self) -> IncomingTextMessage | None:
        """Primeira mensagem do payload, ou None. Conveniência para testes."""
        mensagens = self.extract_text_messages()
        return mensagens[0] if mensagens else None

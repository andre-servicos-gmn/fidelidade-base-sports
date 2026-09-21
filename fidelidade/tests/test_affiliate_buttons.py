"""Fluxo de botões da pergunta de afiliado (template da Cloud API).

Testes PUROS: não tocam banco nem rede. Cobrem as duas peças de fronteira que
o fluxo com botões introduz:

1. o clique num botão de TEMPLATE chega num formato diferente do botão
   interativo, e precisa ser reconhecido pelo schema do webhook;
2. o disparo pós-compra precisa sair como TEMPLATE quando o provedor é a Meta,
   porque texto livre é recusado fora da janela de 24h.

O segundo turno da conversa ("Sim" -> pede o código) está em
`test_affiliate_question_flow.py` (sem banco) e `test_conversation.py` (banco).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import get_settings
from app.whatsapp.affiliate_prompt import AffiliatePrompt, dispatch_affiliate_prompts
from app.whatsapp.conversation import _AFFILIATE_SKIP_CMDS, _AFFILIATE_YES_CMDS
from app.whatsapp.evolution.sender import MessageSender, MockMessageSender
from app.whatsapp.meta.webhook_schema import MetaWebhook

PHONE = "11987654321"
TEMPLATE = "compra_pontos_afiliado"


def _button_payload(text: str | None, payload: str = "SIM") -> dict:
    """Payload de clique em botão de TEMPLATE (não é o formato interativo)."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"profile": {"name": "Andre"}}],
                            "messages": [
                                {
                                    "id": "wamid.TESTE",
                                    "from": "5511987654321",
                                    "type": "button",
                                    "button": {"text": text, "payload": payload},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


# --------------------------------------------------------------------------- #
# 1. Schema: clique em botão de template                                       #
# --------------------------------------------------------------------------- #


def test_template_button_click_is_extracted():
    """O rótulo do botão vira o "texto digitado" pelo cliente.

    Sem o ramo `button` no schema, `resolved_text()` devolveria None e o
    webhook descartaria o clique em silêncio — o cliente toca no botão e nada
    acontece, sem erro em lugar nenhum.
    """
    webhook = MetaWebhook.model_validate(_button_payload("Sim, tenho o código"))
    mensagens = webhook.extract_text_messages()

    assert len(mensagens) == 1
    assert mensagens[0].text == "Sim, tenho o código"
    assert mensagens[0].from_number == "5511987654321"


def test_template_button_falls_back_to_payload():
    """Sem o rótulo visível, o payload (definido no template) serve."""
    mensagens = MetaWebhook.model_validate(
        _button_payload(None, payload="NAO")
    ).extract_text_messages()

    assert len(mensagens) == 1
    assert mensagens[0].text == "NAO"


def test_button_labels_match_conversation_vocabulary():
    """Os rótulos dos botões precisam cair nos comandos, sem tradução extra.

    Amarra o texto aprovado na Meta ao vocabulário da conversa: se alguém
    mudar um dos dois lados sem o outro, este teste quebra em vez de o cliente
    clicar no botão certo e ficar preso.
    """
    nao = MetaWebhook.model_validate(
        _button_payload("Não", payload="NAO")
    ).extract_text_messages()
    assert nao[0].text.lower() in _AFFILIATE_SKIP_CMDS
    assert nao[0].text.lower() not in _AFFILIATE_YES_CMDS

    # Rótulo aprovado no template: exatamente "Sim".
    sim = MetaWebhook.model_validate(
        _button_payload("Sim", payload="SIM")
    ).extract_text_messages()
    assert sim[0].text.lower() in _AFFILIATE_YES_CMDS
    assert sim[0].text.lower() not in _AFFILIATE_SKIP_CMDS


# --------------------------------------------------------------------------- #
# 2. Disparo pós-compra: template na Meta, texto livre na Evolution            #
# --------------------------------------------------------------------------- #


class _FakeTemplateSender(MessageSender):
    """Provedor que sabe mandar template (como a Cloud API). Só registra."""

    supports_templates = True

    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.templates: list[dict] = []

    async def send_text(self, phone_canonical: str, text: str) -> None:
        self.texts.append((phone_canonical, text))

    async def send_template(
        self,
        phone_canonical: str,
        template_name: str,
        language: str = "pt_BR",
        body_params: list[str] | None = None,
    ) -> None:
        self.templates.append(
            {
                "phone": phone_canonical,
                "name": template_name,
                "language": language,
                "params": body_params,
            }
        )


@pytest.fixture
def prompts() -> list[AffiliatePrompt]:
    return [
        AffiliatePrompt(
            phone=PHONE,
            points=1000,
            source_reference="TX-1",
            amount=Decimal("200.00"),
        )
    ]


@pytest.fixture
def template_configurado(monkeypatch):
    monkeypatch.setenv("META_AFFILIATE_TEMPLATE", TEMPLATE)
    monkeypatch.setenv("META_TEMPLATE_LANGUAGE", "pt_BR")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_dispatch_uses_template_on_meta(prompts, template_configurado):
    """Na Cloud API a pergunta sai como template, com os pontos no {{1}}."""
    sender = _FakeTemplateSender()

    sent = await dispatch_affiliate_prompts(prompts, sender)

    assert sent == 1
    assert sender.texts == []  # nada de texto livre: seria recusado (131047)
    assert len(sender.templates) == 1
    enviado = sender.templates[0]
    assert enviado["name"] == TEMPLATE
    assert enviado["language"] == "pt_BR"
    assert enviado["params"] == ["1000"]


async def test_dispatch_uses_text_on_evolution(prompts, template_configurado):
    """Provedor sem template (Evolution/mock) segue no texto livre de sempre."""
    sender = MockMessageSender()

    sent = await dispatch_affiliate_prompts(prompts, sender)

    assert sent == 1
    assert len(sender.sent) == 1
    assert "1000" in sender.sent[0][1]


async def test_dispatch_warns_when_template_missing(prompts, monkeypatch, caplog):
    """Meta ligada e template vazio: cai no texto, mas com aviso explícito.

    Sem o aviso, a recusa da Meta apareceria no log só como "falha ao enviar",
    sem indicar que a causa foi configuração faltando.
    """
    monkeypatch.setenv("META_AFFILIATE_TEMPLATE", "")
    get_settings.cache_clear()

    sender = _FakeTemplateSender()

    with caplog.at_level("WARNING", logger="fidelidade.affiliate_prompt"):
        sent = await dispatch_affiliate_prompts(prompts, sender)

    assert sent == 1
    assert sender.templates == []
    assert len(sender.texts) == 1
    assert "META_AFFILIATE_TEMPLATE" in caplog.text
    get_settings.cache_clear()

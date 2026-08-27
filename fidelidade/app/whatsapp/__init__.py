"""Cérebro da conversa do WhatsApp — lógica PURA, sem provedor de mensagem.

A integração com a Evolution API (ou outro provedor) será um adapter fino que
chama `conversation.handle_message` e envia as respostas de volta. Nada aqui
conhece HTTP/webhook.
"""

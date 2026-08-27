"""Adapter da Evolution API: traduz formatos e move mensagens.

Adapter FINO. Toda a inteligência da conversa fica na Fase 5
(`app.whatsapp.conversation`). Aqui só: normalização de telefone na fronteira,
schema tolerante do webhook, e o envio atrás de uma interface (`MessageSender`).
"""

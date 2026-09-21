"""Textos das respostas do WhatsApp (pt-BR, centralizados).

Tom: direto, claro, sem emojis em excesso, sem linguagem de guru. Mensagens
curtas — é WhatsApp.

FORMATAÇÃO (o WhatsApp NÃO renderiza markdown)
----------------------------------------------
- Negrito é asterisco SIMPLES: *texto*. Nada de `**`, `#` ou listas markdown.
- Crase simples NÃO formata nada: o cliente enxerga a crase na tela. Para
  destacar um exemplo, use negrito.
- Monoespaçado seria ```três crases```, mas não usamos: pesa visualmente e
  quebra em aparelhos antigos.

ESTRUTURA (por que as mensagens têm linhas em branco)
-----------------------------------------------------
Tela de celular é estreita e o cliente LÊ EM DIAGONAL. Então:
- uma ideia por bloco, separada por linha em branco;
- o dado que importa (código do cupom, saldo) fica ISOLADO na própria linha,
  para ser achado num relance e copiado com um toque;
- listas com um item por linha, nunca separadas por vírgula;
- a ação esperada vem por último ("Responda com o número...").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.models import CouponDiscountType, CouponStatus
from app.domain.redemption import RedemptionResult

# Horário de Brasília. Fixo em -03:00: o Brasil não tem horário de verão desde
# 2019 (e assim não dependemos do pacote de fusos no container).
_BRT = timezone(timedelta(hours=-3))


def _date_br(value: datetime) -> str:
    """Data no calendário de Brasília, "dd/mm/aaaa".

    O banco devolve as datas em UTC. Um cupom que vence às 23:59 de 31/12 em
    Brasília é 02:59 de 01/01 em UTC; formatar direto mostrava ao cliente um
    dia a mais do que o cupom real vale.
    """
    if value.tzinfo is not None:
        value = value.astimezone(_BRT)
    return value.strftime("%d/%m/%Y")


_BRAND = "Base Sports"
_CLUB = "Base Club"
_CURRENCY = "Base Points"

# ============================== ⚠️ ATENÇÃO ================================= #
# O TEXTO DO REGULAMENTO ABAIXO É UM RASCUNHO DE EXEMPLO, escrito para o fluxo
# poder ser construído e testado. NÃO foi revisado por ninguém responsável pelo
# programa e NÃO tem validade. Prazos, percentuais e condições são inventados.
#
# Antes do go-live: substitua os dois textos pelo regulamento oficial e suba a
# `TERMS_VERSION`. A versão é gravada junto de cada aceite (`terms_acceptances`),
# então trocar o texto sem trocar a versão corrompe a prova do consentimento.
# =========================================================================== #
TERMS_VERSION = "0.1-rascunho"

_TERMS_SUMMARY = f"""*{_CLUB} — resumo do regulamento*

*1.* Participa quem tem CPF válido e mais de 18 anos.

*2.* Você acumula *1 {_CURRENCY} por R$ 1,00* em compras identificadas com o seu CPF, na loja física ou no site.

*3.* Os pontos valem por *12 meses* a contar de cada compra.

*4.* O resgate é por faixas: uma quantidade de pontos vira um cupom de desconto. As faixas aparecem em "Resgatar pontos".

*5.* Cada cupom tem prazo de validade, uso único e pode exigir valor mínimo de compra.

*6.* Pontos não são dinheiro: não podem ser transferidos nem trocados por espécie.

*7.* Seu CPF é usado apenas para identificar as suas compras no programa, conforme a LGPD.

*8.* A {_BRAND} pode alterar ou encerrar o programa, avisando antes."""

_TERMS_FULL = f"""*REGULAMENTO DO {_CLUB.upper()}*
versão {TERMS_VERSION}

*1. O PROGRAMA*
O {_CLUB} é o programa de fidelidade da {_BRAND}. A participação é gratuita e voluntária.

*2. QUEM PODE PARTICIPAR*
Pessoa física, com CPF válido e regular, maior de 18 anos. Cada CPF corresponde a um único cadastro, e cada cadastro a um único número de telefone.

*3. COMO ACUMULAR*
Cada R$ 1,00 em compras equivale a 1 {_CURRENCY}. Para que a compra gere pontos, é obrigatório informar o CPF no momento do pagamento — na loja física ou no site.
Valores pagos com cupons de desconto do próprio programa não geram novos pontos.
Fretes, taxas e serviços não geram pontos.

*4. PONTOS NÃO SÃO RETROATIVOS*
Só geram pontos as compras identificadas com CPF a partir da adesão. Compras anteriores, ou feitas sem informar o CPF, não são creditadas depois.

*5. PRAZO DE VALIDADE*
Cada ponto vale 12 meses contados da data da compra que o gerou. Pontos vencidos são retirados do saldo e não são restituídos.

*6. COMO RESGATAR*
O resgate é feito por faixas: uma quantidade fixa de pontos é trocada por um cupom de desconto de valor determinado. As faixas vigentes podem ser consultadas a qualquer momento e podem mudar sem aviso.
O resgate é imediato e irreversível: os pontos são debitados no ato e não retornam ao saldo.

*7. OS CUPONS*
Cada cupom tem código próprio, uso único e prazo de validade. Pode haver exigência de valor mínimo de compra, informada no momento do resgate.
Cupons não são cumulativos entre si nem com outras promoções, salvo indicação em contrário. Cupom vencido ou já utilizado não é substituído, e os pontos gastos nele não voltam.

*8. NATUREZA DOS PONTOS*
Os pontos não têm valor monetário, não são transferíveis entre CPFs, não podem ser vendidos, doados nem convertidos em dinheiro.

*9. INDICAÇÃO*
Compras podem ser atribuídas a um parceiro indicador mediante informe do código dele. A atribuição vale para uma compra específica e não altera os pontos do cliente.

*10. DADOS PESSOAIS*
O CPF é usado exclusivamente para identificar as compras do participante no programa. Ele é armazenado de forma protegida e nunca é exibido por completo.
O tratamento segue a Lei nº 13.709/2018 (LGPD). O participante pode solicitar acesso, correção ou exclusão dos seus dados, o que implica o encerramento da participação e a perda do saldo.

*11. ENCERRAMENTO*
O participante pode sair a qualquer momento, perdendo o saldo acumulado.
A {_BRAND} pode suspender ou cancelar um cadastro em caso de fraude, uso indevido ou informação falsa.

*12. ALTERAÇÕES*
Este regulamento pode ser alterado. Mudanças relevantes serão comunicadas e poderá ser pedido um novo aceite. Seguir usando o programa após o aviso significa concordar com a versão vigente."""


# --- Onboarding ------------------------------------------------------------ #
def greeting() -> str:
    """Primeiro contato: apresenta o clube e pergunta se quer participar."""
    return (
        f"Olá, seja bem-vindo ao *{_CLUB}* — o programa de fidelidade da "
        f"{_BRAND}!\n\n"
        f"Aqui você junta {_CURRENCY} e troca por produtos na loja.\n\n"
        "Quer participar?\n\n"
        "*1* - Sim\n"
        "*2* - Não"
    )


def terms_summary() -> str:
    """Resumo do regulamento + as três opções de resposta."""
    return f"{_TERMS_SUMMARY}\n\n{_ask_consent(with_full_option=True)}"


def terms_full() -> str:
    """Regulamento completo + o pedido de aceite (sem a opção de ler de novo)."""
    return f"{_TERMS_FULL}\n\n{_ask_consent(with_full_option=False)}"


def _ask_consent(with_full_option: bool) -> str:
    linhas = ["Você concorda com o regulamento?", "", "*1* - De acordo", "*2* - Não concordo"]
    if with_full_option:
        linhas.append("*3* - Ler o regulamento completo")
    return "\n".join(linhas)


def terms_declined() -> str:
    """Cliente não concordou: encerra sem cadastrar nada."""
    return (
        "Entendido — sem o aceite do regulamento não podemos fazer o "
        "cadastro.\n\n"
        f"A {_BRAND} agradece seu contato. Até a próxima!"
    )


def goodbye() -> str:
    """Encerramento a pedido do cliente ('sair') ou recusa em participar."""
    return f"A {_BRAND} agradece seu contato. Até a próxima!"


def invalid_option() -> str:
    """Resposta fora das opções oferecidas, em qualquer passo de escolha."""
    return "Não entendi. Responda com o *número* de uma das opções acima."


def ask_cpf() -> str:
    return (
        "Digite agora o seu *CPF*.\n\n"
        "Sem pontos, traços ou espaços."
    )


def cpf_invalid() -> str:
    return (
        "Esse CPF não parece válido.\n\n"
        "Envie os *11 números*, sem pontos nem traços."
    )


def cpf_invalid_again() -> str:
    """2ª tentativa inválida: repete o pedido com um exemplo concreto.

    Evita repetir a MESMA frase (que soa robótico e não ajuda quem errou). O
    exemplo vai em negrito e isolado — antes ia entre crases, que o WhatsApp
    mostra literalmente.
    """
    return (
        "Ainda não consegui ler o CPF. Ele tem 11 números, assim:\n\n"
        "*12345678900*\n\n"
        "Se preferir, envie *sair* para encerrar por aqui."
    )


def cpf_give_up() -> str:
    """3ª+ tentativa inválida: para de insistir e oferece uma saída humana."""
    return (
        "Não consegui identificar seu CPF, então vou encerrar por aqui para "
        "não ficar repetitivo.\n\n"
        "Quando quiser tentar de novo, é só mandar uma mensagem.\n\n"
        f"Se precisar de ajuda, fale com um atendente em uma loja *{_BRAND}*."
    )


def phone_already_linked() -> str:
    return (
        "Este telefone já está vinculado a outro cadastro.\n\n"
        "Se você acha que é um engano, fale com o nosso *suporte*."
    )


def welcome_to_program(balance: int = 0, name: str | None = None) -> str:
    """Boas-vindas após o cadastro.

    O trecho do saldo muda conforme o CPF já tinha compras ou não: quem chega
    novo precisa saber que os pontos NÃO são retroativos, senão espera um saldo
    que nunca vai aparecer.
    """
    quem = f", *{name}*" if name else ""
    if balance > 0:
        saldo = f"Você já tem *{balance} {_CURRENCY}* acumulados."
    else:
        saldo = (
            "Você começa a acumular na sua *próxima compra* — os pontos não "
            "são retroativos."
        )
    return (
        f"Parabéns pela escolha em fazer parte da nossa rede *{_CLUB}*{quem}!\n\n"
        "Acumular é simples: em toda compra na loja física ou no site, informe "
        "sempre o seu CPF.\n\n"
        f"{saldo}\n\n"
        f"Toda compra acumula {_CURRENCY}!"
    )


# --- Menu ------------------------------------------------------------------ #
def menu() -> str:
    return (
        "O que você quer fazer?\n\n"
        "*1* - Consultar saldo\n"
        "*2* - Resgatar pontos\n"
        "*3* - Meus cupons\n\n"
        "Responda com o número, ou escreva: saldo, resgatar, cupons.\n\n"
        "Para encerrar a conversa, digite *sair*."
    )


# --- Saldo ----------------------------------------------------------------- #
def balance(points: int) -> str:
    return (
        f"Seu saldo é de *{points} pontos*.\n\n"
        "Envie *2* para trocar por desconto."
    )


# --- Recompensas ----------------------------------------------------------- #
def _format_discount(discount_type: str, discount_value: float | Decimal) -> str:
    value = Decimal(str(discount_value))
    if discount_type == CouponDiscountType.PERCENTAGE.value:
        return f"{value:.0f}% de desconto"
    return f"R$ {value:.2f}".replace(".", ",")


def _format_min_value(value: float | Decimal) -> str:
    v = Decimal(str(value))
    if v == v.to_integral_value():
        return f"R${v:.0f}"
    return f"R${v:.2f}".replace(".", ",")


def _min_condition(min_order_value: float | Decimal | None) -> str:
    """Frase da condição de pedido mínimo, ou "" quando não há mínimo."""
    if min_order_value is None:
        return ""
    return f"válido em compras acima de {_format_min_value(min_order_value)}"


def rewards_list(rewards: list[dict[str, Any]]) -> str:
    """Uma recompensa por bloco: título em negrito, condições na linha de baixo."""
    blocos = []
    for i, reward in enumerate(rewards, start=1):
        desc = _format_discount(
            reward["discount_type"], reward["discount_value"]
        )
        titulo = f"*{i}* - {desc} por {reward['points_cost']} pontos"

        detalhes = []
        condicao = _min_condition(reward.get("min_order_value"))
        if condicao:
            detalhes.append(condicao)
        detalhes.append(f"{reward['available_count']} disponíveis")

        blocos.append(f"{titulo}\n{' · '.join(detalhes)}")

    corpo = "\n\n".join(blocos)
    return (
        "Recompensas disponíveis:\n\n"
        f"{corpo}\n\n"
        "Responda com o *número* da recompensa que você quer."
    )


def no_rewards() -> str:
    return (
        "No momento não temos recompensas disponíveis.\n\n"
        "Volte em breve!"
    )


def invalid_choice() -> str:
    return (
        "Não entendi a opção.\n\n"
        "Responda com o *número* da recompensa, ou envie *menu* para voltar."
    )


# --- Resgate --------------------------------------------------------------- #
def redemption_success(result: RedemptionResult) -> str:
    """O código fica ISOLADO: é o que o cliente precisa achar e copiar no caixa."""
    valor = _format_discount(result.discount_type, result.discount_value)
    validade = _date_br(result.expires_at)
    condicao = _min_condition(result.min_order_value)

    linhas = [f"Desconto: {valor}", f"Validade: {validade}"]
    if condicao:
        linhas.append(f"Condição: {condicao}")

    return (
        "Resgate confirmado! Seu cupom é:\n\n"
        f"*{result.coupon_code}*\n\n"
        + "\n".join(linhas)
        + "\n\nUse no totem na hora de pagar.\n\n"
        f"Saldo restante: *{result.balance_after} pontos*."
    )


def insufficient_points(balance_points: int, cost: int) -> str:
    return (
        "Saldo insuficiente para essa recompensa.\n\n"
        f"Ela custa *{cost} pontos* e você tem *{balance_points}*.\n\n"
        "Continue comprando para acumular mais!"
    )


def reward_unavailable() -> str:
    return (
        "Essa recompensa esgotou agora há pouco.\n\n"
        "Envie *2* para ver as opções disponíveis."
    )


# --- Cupons ---------------------------------------------------------------- #
_STATUS_LABEL = {
    CouponStatus.ALLOCATED.value: "disponível para uso",
    CouponStatus.USED.value: "já utilizado",
    CouponStatus.EXPIRED.value: "expirado",
}


def coupons_list(coupons: list[dict[str, Any]]) -> str:
    """Um cupom por bloco: código em destaque, detalhes embaixo."""
    blocos = []
    for c in coupons:
        valor = _format_discount(c["discount_type"], c["discount_value"])
        status = _STATUS_LABEL.get(c["status"], c["status"])

        detalhes = [valor]
        if c.get("expires_at") is not None:
            expires: datetime = c["expires_at"]
            detalhes.append(f"vale até {_date_br(expires)}")

        linhas = [f"*{c['code']}* - {status}", " · ".join(detalhes)]
        condicao = _min_condition(c.get("min_order_value"))
        if condicao:
            linhas.append(condicao)

        blocos.append("\n".join(linhas))

    corpo = "\n\n".join(blocos)
    return f"Seus cupons:\n\n{corpo}"


def no_coupons() -> str:
    return (
        "Você ainda não resgatou nenhum cupom.\n\n"
        "Envie *2* para ver as recompensas disponíveis."
    )


# --- Afiliados (atribuição por compra) ------------------------------------- #
def ask_affiliate_code(points: int) -> str:
    return (
        f"Sua compra rendeu *{points} pontos*!\n\n"
        "Você veio por indicação de algum professor ou influencer?\n\n"
        "Se sim, envie o *código* dele. Se não, responda *não*."
    )


def ask_affiliate_code_after_yes() -> str:
    """Segundo turno: o cliente clicou "Sim" e agora informa o código.

    Só existe por causa dos botões do template: no fluxo por texto o cliente
    manda o código de uma vez (ver `ask_affiliate_code`); tocando no botão ele
    apenas disse que TEM um código, e ainda precisa dizer qual.
    """
    return (
        "Ótimo! Qual é o *código* do professor ou influencer?\n\n"
        "Se preferir não informar, responda *não*."
    )


def affiliate_attributed(affiliate_name: str | None = None) -> str:
    de_quem = f" de *{affiliate_name}*" if affiliate_name else ""
    return (
        f"Anotado! Esta compra foi creditada à indicação{de_quem}.\n\n"
        "Obrigado!"
    )


def affiliate_code_not_found() -> str:
    return (
        "Não encontrei esse código de indicação.\n\n"
        "Confira com quem te indicou e envie de novo, ou responda *não* "
        "para pular."
    )


def affiliate_skipped() -> str:
    return (
        "Tudo certo, seguimos sem indicação.\n\n"
        "Bons pontos!"
    )

"""As ferramentas do Jarvis: funções com os parâmetros descritos para o modelo, e de qual recurso dependem.

Usado pelo servidor MCP (servidor.py) e pela linha de comando (cli.py). Os nomes são o contrato com o Hermes.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from dataclasses import dataclass
from typing import Annotated, Callable

from mcp_types import ToolAnnotations
from pydantic import Field

from app import agenda as agenda_google, busca, clima as previsao, config, emails as gmail, hora as relogio, moodle

log = logging.getLogger("jarvis-tools")
TEMPO_MAXIMO = 50  # segundos por chamada (o cliente MCP do Hermes espera até 60)

# Só consultam dados externos, não mudam nada
CONSULTA = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Cria algo novo (evento), sem apagar nem alterar o que existe
CRIACAO = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

QUANDO_FUTURO = ("hoje, amanhã, um dia da semana, uma data (26/09), fim de semana, semana, "
                 "próximas semanas (30 dias) ou mês.")
CONTA = "Rótulo ou e-mail da conta Google. Vazio = todas."


@dataclass(frozen=True)
class Ferramenta:
    nome: str
    funcao: Callable
    descricao: str
    anotacoes: ToolAnnotations
    recurso: str = ""  # "" = sempre; "moodle", "google" ou "busca"


# ---------------------------------------------------------------- funções

async def hora() -> str:
    return relogio.texto_hora()


async def clima(
    cidade: Annotated[str, Field(description="Cidade, com estado ou país se precisar (ex.: Londrina, PR). "
                                             "Vazio = cidade do usuário.")] = "",
    quando: Annotated[str, Field(description="hoje, agora, amanhã, depois de amanhã, um dia da semana, "
                                             "fim de semana ou semana.")] = "hoje",
) -> str:
    return await previsao.previsao(cidade, quando)


async def buscar(
    consulta: Annotated[str, Field(description="O que pesquisar, em poucas palavras.")],
) -> str:
    return await busca.buscar(consulta)


async def ler_pagina(
    url: Annotated[str, Field(description="Endereço completo da página (um Link dos resultados de buscar).")],
) -> str:
    return await busca.ler_pagina(url)


DISCIPLINA = "Nome da disciplina como o usuário disser, mesmo abreviado (ex.: TCC, redes). Vazio = todas."


async def moodle_prazos(
    quando: Annotated[str, Field(description="semana, hoje, amanhã, um dia da semana, uma data (26/09), "
                                             "próximas semanas (30 dias), mês ou atrasadas.")] = "semana",
    disciplina: Annotated[str, Field(description=DISCIPLINA)] = "",
) -> str:
    return await moodle.prazos(quando, disciplina)


async def moodle_provas(
    disciplina: Annotated[str, Field(description=DISCIPLINA)] = "",
) -> str:
    return await moodle.provas(disciplina)


async def moodle_disciplinas() -> str:
    return await moodle.disciplinas()


async def moodle_conteudo(
    disciplina: Annotated[str, Field(description="Nome da disciplina como o usuário disser, mesmo abreviado "
                                                 "(ex.: TCC, sistemas distribuídos).")],
    assunto: Annotated[str, Field(description="O que procurar dentro dela (ex.: data da defesa, cronograma). "
                                              "Vazio = visão geral.")] = "",
) -> str:
    return await moodle.conteudo(disciplina, assunto)


async def moodle_atividade(
    atividade: Annotated[str, Field(description="Nome da atividade (ex.: lista 2, relatório parcial).")],
    disciplina: Annotated[str, Field(description=DISCIPLINA)] = "",
) -> str:
    return await moodle.atividade(atividade, disciplina)


async def agenda(
    quando: Annotated[str, Field(description=QUANDO_FUTURO)] = "hoje",
    conta: Annotated[str, Field(description=CONTA)] = "",
) -> str:
    return await agenda_google.agenda(quando, conta)


async def agenda_criar(
    titulo: Annotated[str, Field(description="Título curto do evento.")],
    data: Annotated[str, Field(description="Um dia: hoje, amanhã, um dia da semana ou uma data (26/09).")],
    hora: Annotated[str, Field(description="Início: 15h, 15h30 ou 15:30. Vazio = dia todo.")] = "",
    duracao_minutos: Annotated[int, Field(description="Duração em minutos.")] = 60,
    conta: Annotated[str, Field(description="Rótulo ou e-mail da conta Google. Vazio = a padrão.")] = "",
) -> str:
    return await agenda_google.criar(titulo, data, hora, duracao_minutos, conta)


async def emails(
    quando: Annotated[str, Field(description="recentes (os mais novos), hoje, ontem, 3 dias ou semana.")] = "recentes",
    conta: Annotated[str, Field(description=CONTA)] = "",
    filtro: Annotated[str, Field(description="Remetente, assunto ou palavra para procurar (ex.: copel, utfpr). "
                                             "Vazio = todos.")] = "",
    incluir_promocoes: Annotated[bool, Field(description="true = incluir promoções e redes sociais.")] = False,
) -> str:
    return await gmail.emails(quando, conta, filtro, incluir_promocoes)


async def ler_email(
    email: Annotated[str, Field(description="O id que veio entre colchetes na lista da ferramenta emails, copiado "
                                            "de lá (nunca invente), ou o que procurar (ex.: fatura da copel).")],
    conta: Annotated[str, Field(description=CONTA)] = "",
) -> str:
    return await gmail.ler_email(email, conta)


# ---------------------------------------------------------------- catálogo

FERRAMENTAS = [
    Ferramenta("hora", hora, "Data e hora atuais no fuso de Brasília. Use sempre que perguntarem as horas ou a data.",
               CONSULTA),
    Ferramenta("clima", clima, "Previsão do tempo: temperatura, chuva e vento.", CONSULTA),
    Ferramenta("buscar", buscar, "Pesquisa na web e já lê as melhores páginas: devolve trechos com a fonte. Use "
               "para notícias, fatos recentes ou o que você não sabe.", CONSULTA, "busca"),
    Ferramenta("ler_pagina", ler_pagina, "Lê mais texto de uma página que apareceu em buscar.", CONSULTA, "busca"),
    Ferramenta("moodle_prazos", moodle_prazos, "Atividades pendentes do Moodle da faculdade, com disciplina e "
               "prazo.", CONSULTA, "moodle"),
    Ferramenta("moodle_provas", moodle_provas, "Provas e questionários marcados no Moodle nos próximos 30 dias.",
               CONSULTA, "moodle"),
    Ferramenta("moodle_disciplinas", moodle_disciplinas, "Lista as disciplinas em andamento no Moodle.", CONSULTA,
               "moodle"),
    Ferramenta("moodle_conteudo", moodle_conteudo, "Abre uma disciplina do Moodle: professores, avisos, prazos e "
               "materiais. Com assunto, procura dentro dela.", CONSULTA, "moodle"),
    Ferramenta("moodle_atividade", moodle_atividade, "Detalhes de uma atividade do Moodle: descrição, prazo, se "
               "já foi entregue e nota.", CONSULTA, "moodle"),
    Ferramenta("agenda", agenda, "Compromissos do Google Agenda.", CONSULTA, "google"),
    Ferramenta("agenda_criar", agenda_criar, "Cria um evento no Google Agenda, em dois passos: a 1ª chamada não "
               "cria nada e devolve o dia e a hora para você confirmar com o usuário; depois do sim dele, chame de novo "
               "com os mesmos dados.", CRIACAO, "google"),
    Ferramenta("emails", emails, "Lista os e-mails do Gmail, dos mais novos para os mais antigos: remetente, "
               "assunto, trecho e id.", CONSULTA, "google"),
    Ferramenta("ler_email", ler_email, "Lê um e-mail inteiro do Gmail, pelo id da lista de emails ou por uma busca.",
               CONSULTA, "google"),
]
POR_NOME = {f.nome: f for f in FERRAMENTAS}


def ativa(ferramenta: Ferramenta, recursos: dict) -> bool:
    return not ferramenta.recurso or bool(recursos.get(ferramenta.recurso))


def descricao(ferramenta: Ferramenta, recursos: dict) -> str:
    """Descrição final; com mais de uma conta Google, diz quais são (o modelo pode escolher pelo rótulo)."""
    contas = recursos.get("google") or []
    if ferramenta.recurso == "google" and len(contas) > 1:
        return "%s Contas: %s." % (ferramenta.descricao, ", ".join(contas))
    return ferramenta.descricao


# ---------------------------------------------------------------- registro de chamadas

def _valor_no_log(valor) -> str:
    texto = repr(valor)
    return texto if len(texto) <= 120 else texto[:117] + "...'"


def com_registro(ferramenta: Ferramenta) -> Callable:
    """Envolve a função: uma linha de log por chamada (nome e argumentos) e nenhum erro cru para o modelo."""
    assinatura = inspect.signature(ferramenta.funcao)

    @functools.wraps(ferramenta.funcao)
    async def chamada(*args, **kwargs):
        try:
            ligados = assinatura.bind(*args, **kwargs)
            ligados.apply_defaults()
            argumentos = " ".join("%s=%s" % (k, _valor_no_log(v)) for k, v in ligados.arguments.items())
        except TypeError:
            argumentos = "(argumentos inválidos)"
        log.info("%s", ("%s %s" % (ferramenta.nome, argumentos)).strip())
        try:
            # O Hermes desiste em 60 s; antes disso, uma resposta clara em vez de silêncio
            return await asyncio.wait_for(ferramenta.funcao(*args, **kwargs), TEMPO_MAXIMO)
        except asyncio.TimeoutError:
            log.error("%s demorou mais de %d s", ferramenta.nome, TEMPO_MAXIMO)
            return ("A ferramenta %s demorou demais para responder (o serviço está lento). Tente de novo daqui a "
                    "pouco." % ferramenta.nome)
        except Exception as erro:  # só o tipo no log: mensagens de bibliotecas podem trazer endereços com token
            log.error("%s falhou: %s", ferramenta.nome, type(erro).__name__)
            return "Tive um problema inesperado na ferramenta %s. Tente de novo daqui a pouco." % ferramenta.nome

    return chamada


def recursos() -> dict:
    return config.recursos()

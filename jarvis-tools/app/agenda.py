"""Google Agenda: compromissos de uma ou de todas as contas, e criação de eventos."""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from time import monotonic

from app import google_auth
from app.google_auth import ErroGoogle
from app.periodos import PeriodoInvalido, inicio_do_dia, interpretar, rotulo_dia
from app.recorrencia import RepeticaoInvalida, interpretar_repeticao
from app.textos import (agora as agora_local, as_hora, contagem, das_hora, data_falada, dia_curto, faixa_horas,
                        como_responder, fuso, juntar_com_e, maiuscula, normalizar)

URL_CALENDARIO = "https://www.googleapis.com/calendar/v3"
MAX_EVENTOS = 12
MAX_POR_AGENDA = 50
DURACAO_MINIMA, DURACAO_MAXIMA = 5, 24 * 60

# Criar evento em dois passos: a 1ª chamada só guarda o pedido; o evento sai quando a mesma chamada volta depois
# que o usuário confirmou. Encadear as duas no mesmo turno leva 1 a 2 s; uma confirmação de verdade leva mais.
CONFIRMACAO_MINIMA = 5.0    # segundos
CONFIRMACAO_MAXIMA = 600.0  # depois disso, pergunta de novo
_pendentes: dict[tuple, float] = {}


def relogio() -> float:
    return monotonic()


AJUDA_HORA = "Use 15h, 15h30 ou 15:30; deixe vazio para um evento de dia todo."


@dataclass(frozen=True)
class Evento:
    inicio: datetime
    fim: datetime
    dia_todo: bool
    titulo: str
    conta: str
    id: str


# ---------------------------------------------------------------- leitura

def _instante(texto: str) -> datetime:
    return datetime.fromisoformat(texto).astimezone(fuso())


def converter(item: dict, rotulo: str) -> Evento | None:
    """Evento da API -> Evento; None para cancelados, recusados ou sem data."""
    if item.get("status") == "cancelled":
        return None
    for participante in item.get("attendees") or []:
        if participante.get("self") and participante.get("responseStatus") == "declined":
            return None
    inicio, fim = item.get("start") or {}, item.get("end") or {}
    titulo = " ".join(str(item.get("summary") or "").split()) or "Evento sem título"
    try:
        if inicio.get("dateTime"):
            comeco = _instante(inicio["dateTime"])
            termino = _instante(fim["dateTime"]) if fim.get("dateTime") else comeco
            return Evento(comeco, termino, False, titulo, rotulo, str(item.get("id") or ""))
        if inicio.get("date"):
            dia = date.fromisoformat(inicio["date"])
            ultimo = date.fromisoformat(fim["date"]) if fim.get("date") else dia + timedelta(days=1)
            return Evento(inicio_do_dia(dia), inicio_do_dia(ultimo), True, titulo, rotulo, str(item.get("id") or ""))
    except (ValueError, TypeError):
        return None
    return None


def _rfc3339(momento: datetime) -> str:
    return momento.replace(microsecond=0).isoformat()


async def eventos_da_agenda(conta: dict, agenda_id: str, inicio: datetime, fim: datetime) -> list[dict]:
    url = "%s/calendars/%s/events" % (URL_CALENDARIO, urllib.parse.quote(agenda_id, safe=""))
    dados = await google_auth.chamar(conta, "GET", url, {
        "timeMin": _rfc3339(inicio), "timeMax": _rfc3339(fim), "singleEvents": "true", "orderBy": "startTime",
        "maxResults": MAX_POR_AGENDA})
    return dados.get("items") or [] if isinstance(dados, dict) else []


async def eventos_da_conta(conta: dict, inicio: datetime, fim: datetime) -> list[Evento]:
    try:
        lista = await google_auth.chamar(conta, "GET", URL_CALENDARIO + "/users/me/calendarList",
                                         {"minAccessRole": "reader", "maxResults": 250})
    except ErroGoogle as erro:
        if "expirou ou foi revogada" in str(erro):
            raise
        lista = {}  # sem a permissão da lista de agendas: fica só com a principal
    agendas = [a for a in (lista.get("items") or []) if a.get("primary") or a.get("selected")]
    if not agendas:
        agendas = [{"id": "primary"}]
    resultados = await asyncio.gather(*(eventos_da_agenda(conta, a["id"], inicio, fim) for a in agendas),
                                      return_exceptions=True)
    eventos = []
    for resultado in resultados:
        if isinstance(resultado, ErroGoogle) and len(agendas) == 1:
            raise resultado
        if isinstance(resultado, BaseException):
            continue  # uma agenda assinada com problema não derruba as outras
        eventos += [e for e in (converter(item, conta["rotulo"]) for item in resultado) if e]
    return eventos


def sem_duplicatas(eventos: list[Evento]) -> list[Evento]:
    vistos, unicos = set(), []
    for evento in sorted(eventos, key=lambda e: (e.inicio, not e.dia_todo, normalizar(e.titulo))):
        chaves = {("titulo", normalizar(evento.titulo), evento.inicio)}
        if evento.id:
            chaves.add(("id", evento.id))
        if chaves & vistos:
            continue
        vistos |= chaves
        unicos.append(evento)
    return unicos


# ---------------------------------------------------------------- texto

def _nome(evento: Evento, com_conta: bool) -> str:
    return "%s (%s)" % (evento.titulo, evento.conta) if com_conta else evento.titulo


def _horario(evento: Evento, hoje: date, dia: date) -> str:
    """'das 9h às 10h'; se termina noutro dia (fora a madrugada seguinte), 'das 9h até sábado às 18h';
    se começou antes do dia descrito, 'desde sábado às 22h até segunda às 18h'."""
    if evento.inicio.date() < dia:
        return "desde %s %s até %s %s" % (dia_curto(evento.inicio.date(), hoje), as_hora(evento.inicio),
                                          dia_curto(evento.fim.date(), hoje), as_hora(evento.fim))
    dias = (evento.fim.date() - evento.inicio.date()).days
    if dias >= 2 or (dias == 1 and evento.fim.time() > time(6, 0)):
        return "%s até %s %s" % (das_hora(evento.inicio), dia_curto(evento.fim.date(), hoje), as_hora(evento.fim))
    return faixa_horas(evento.inicio, evento.fim)


def _dia_todo(evento: Evento, hoje: date) -> str:
    ultimo = evento.fim.date() - timedelta(days=1)
    if ultimo > evento.inicio.date():
        return "%s (até %s)" % (evento.titulo, dia_curto(ultimo, hoje))
    return evento.titulo


def texto_do_dia(eventos: list[Evento], hoje: date, dia: date, com_conta: bool) -> str:
    com_hora = ["%s, %s" % (_horario(e, hoje, dia), _nome(e, com_conta)) for e in eventos if not e.dia_todo]
    dia_todo = [_dia_todo(e, hoje) + (" (%s)" % e.conta if com_conta else "") for e in eventos if e.dia_todo]
    if com_hora and dia_todo:
        return "%s. Dia todo: %s." % ("; ".join(com_hora), juntar_com_e(dia_todo))
    if com_hora:
        return "; ".join(com_hora) + "."
    return "dia todo, %s." % juntar_com_e(dia_todo)


def montar_texto(eventos: list[Evento], periodo, agora: datetime, com_conta: bool) -> str:
    hoje = agora.date()
    if not eventos:
        return "Nada na agenda %s." % periodo.alcance
    mostrados, sobra = eventos[:MAX_EVENTOS], len(eventos) - MAX_EVENTOS
    if periodo.um_dia:
        texto = "%s: %s" % (maiuscula(periodo.rotulo), texto_do_dia(mostrados, hoje, periodo.dia, com_conta))
    else:
        por_dia: dict[date, list[Evento]] = {}
        for evento in mostrados:
            dia = max(evento.inicio.date(), periodo.inicio.date())
            por_dia.setdefault(dia, []).append(evento)
        partes = ["Agenda %s, com %s." % (periodo.rotulo, contagem(len(eventos), "evento", "eventos"))]
        for dia in sorted(por_dia):
            partes.append("%s: %s" % (maiuscula(rotulo_dia(dia, hoje)),
                                      texto_do_dia(por_dia[dia], hoje, dia, com_conta)))
        texto = " ".join(partes)
    if sobra > 0:
        texto += " E mais %s." % contagem(sobra, "evento", "eventos")
    return texto + ("\n" + como_responder(len(eventos)) if len(eventos) > 3 else "")


async def agenda(quando: str = "hoje", conta: str = "", agora: datetime | None = None) -> str:
    agora = agora or agora_local()
    try:
        periodo = interpretar(quando or "hoje", agora)
        contas = google_auth.escolher_contas(conta)
    except PeriodoInvalido as erro:
        return str(erro)
    except ErroGoogle as erro:
        return maiuscula(str(erro)) + "."
    resultados = await asyncio.gather(*(eventos_da_conta(c, periodo.inicio, periodo.fim) for c in contas),
                                      return_exceptions=True)
    eventos, falhas = [], []
    for c, resultado in zip(contas, resultados):
        if isinstance(resultado, ErroGoogle):
            falhas.append(maiuscula(str(resultado)) + ".")
        elif isinstance(resultado, BaseException):
            raise resultado
        else:
            eventos += resultado
    if falhas and len(falhas) == len(contas):
        return "Não consegui ler a agenda. " + " ".join(falhas)
    texto = montar_texto(sem_duplicatas(eventos), periodo, agora, com_conta=len(contas) > 1)
    return texto + ("" if not falhas else " Atenção: " + " ".join(falhas))


# ---------------------------------------------------------------- criação

def interpretar_hora(texto: str) -> tuple[int, int] | None:
    """'15h', '15h30', '15:30', '15', '3 da tarde', 'meio-dia' -> (hora, minuto). None se não entendi."""
    q = normalizar(texto)
    q = re.sub(r"^(as|a|ao|pelas?)\s+", "", q)
    if q in ("meio dia", "meiodia"):
        return 12, 0
    if q in ("meia noite", "meianoite"):
        return 0, 0
    achado = re.fullmatch(r"(\d{1,2})\s*(?:h|hs|:|\.|horas?)?\s*(\d{2})?\s*(?:min|minutos)?"
                          r"(?:\s+(?:da|de|na)\s+(manha|tarde|noite|madrugada))?", q)
    if not achado:
        return None
    hora, minuto = int(achado.group(1)), int(achado.group(2) or 0)
    if achado.group(3) in ("tarde", "noite") and 1 <= hora < 12:
        hora += 12
    if hora > 23 or minuto > 59:
        return None
    return hora, minuto


async def ja_existe(conta: dict, titulo: str, inicio: datetime, fim: datetime, dia_todo: bool) -> bool:
    try:
        itens = await eventos_da_agenda(conta, "primary", inicio, fim)
    except ErroGoogle:
        return False  # na dúvida, cria; a falha real aparece na criação
    for item in itens:
        evento = converter(item, conta["rotulo"])
        if evento and evento.dia_todo == dia_todo and evento.inicio == inicio \
                and normalizar(evento.titulo) == normalizar(titulo):
            return True
    return False


def confirmar(chave: tuple, primeira: str, cedo_demais: str) -> str:
    """Dois passos para tudo que muda a agenda. Texto vazio = pode fazer (a mesma chamada voltou entre 5 s e 10 min
    depois, ou seja, depois de o usuário responder); senão, o que o modelo deve dizer antes."""
    agora = relogio()
    for antiga, quando in list(_pendentes.items()):
        if agora - quando > CONFIRMACAO_MAXIMA:
            del _pendentes[antiga]
    pedido = _pendentes.get(chave)
    if pedido is None:
        _pendentes[chave] = agora
        return primeira
    if agora - pedido < CONFIRMACAO_MINIMA:
        return cedo_demais
    del _pendentes[chave]
    return ""


def _confirmacao(rotulo: str, titulo: str, inicio: datetime, fim: datetime, descricao: str, regra: str) -> str:
    return confirmar(
        ("criar", rotulo, normalizar(titulo), inicio.isoformat(), fim.isoformat(), regra),
        "Ainda não criei. Pergunte ao usuário: posso criar %s, na conta %s? Só chame agenda_criar de novo, com os "
        "mesmos dados, depois que ele disser que sim." % (descricao, rotulo),
        "Ainda não criei: o usuário não confirmou. Pergunte a ele se pode criar %s e espere a resposta." % descricao)


async def criar(titulo: str, data: str, hora: str = "", duracao_minutos: int = 60, conta: str = "",
                agora: datetime | None = None, repetir: str = "") -> str:
    agora = agora or agora_local()
    titulo = " ".join((titulo or "").split())[:200]
    if not titulo:
        return "Falta o título do evento."
    if not (data or "").strip():
        return "Falta o dia do evento: hoje, amanhã, um dia da semana ou uma data como 26/09."
    try:
        dia = interpretar(data, agora, um_dia=True).dia
    except PeriodoInvalido as erro:
        return str(erro)
    if dia < agora.date():
        return "O dia %s já passou. Diga uma data de hoje em diante." % data_falada(dia, com_ano=True)
    horario = None
    if (hora or "").strip():
        horario = interpretar_hora(hora)
        if horario is None:
            return 'Não entendi a hora "%s". %s' % (hora.strip(), AJUDA_HORA)
    try:
        duracao = int(duracao_minutos)
    except (TypeError, ValueError):
        return "A duração precisa ser um número de minutos, por exemplo 60."
    if horario and not DURACAO_MINIMA <= duracao <= DURACAO_MAXIMA:
        return "A duração precisa ficar entre 5 minutos e 24 horas (1440 minutos)."
    repeticao = None
    if (repetir or "").strip():
        try:
            repeticao = interpretar_repeticao(repetir, dia, dia_todo=not horario)
        except RepeticaoInvalida as erro:
            return str(erro)
        dia = repeticao.primeiro  # "toda quinta" pedido numa segunda começa na quinta
    try:
        escolhida = google_auth.conta_para_criar(conta)
    except ErroGoogle as erro:
        return maiuscula(str(erro)) + "."

    nome_fuso = fuso().key
    if horario:
        inicio = datetime.combine(dia, time(*horario), tzinfo=fuso())
        fim = inicio + timedelta(minutes=duracao)
        if inicio < agora - timedelta(minutes=5):
            return "Hoje %s já passou. Confirme outro dia ou horário." % as_hora(inicio)
        corpo = {"summary": titulo, "start": {"dateTime": _rfc3339(inicio), "timeZone": nome_fuso},
                 "end": {"dateTime": _rfc3339(fim), "timeZone": nome_fuso}}
        quando = faixa_horas(inicio, fim)
    else:
        inicio, fim = inicio_do_dia(dia), inicio_do_dia(dia + timedelta(days=1))
        corpo = {"summary": titulo, "start": {"date": dia.isoformat()},
                 "end": {"date": (dia + timedelta(days=1)).isoformat()}}  # o fim de dia todo é exclusivo
        quando = "dia todo"
    descricao = "%s, %s, %s" % (titulo, data_falada(dia, com_ano=dia.year != agora.year), quando)
    if repeticao:
        corpo["recurrence"] = [repeticao.regra]
        descricao += ", repetindo " + repeticao.descricao
    if await ja_existe(escolhida, titulo, inicio, fim, dia_todo=not horario):
        return "Esse evento já existe na conta %s: %s. Não criei outro." % (escolhida["rotulo"], descricao)
    espera = _confirmacao(escolhida["rotulo"], titulo, inicio, fim, descricao, repeticao.regra if repeticao else "")
    if espera:
        return espera
    try:
        await google_auth.chamar(escolhida, "POST", URL_CALENDARIO + "/calendars/primary/events", corpo=corpo)
    except ErroGoogle as erro:
        return "Não consegui criar o evento: %s." % erro
    return "Evento criado na conta %s: %s." % (escolhida["rotulo"], descricao)

"""Alterar e apagar eventos do Google Agenda, inclusive os que se repetem, e desfazer a última mudança.

- O evento é achado pela descrição (título e, se quiser, o dia): o id de uma resposta anterior não fica na conversa.
- Numa série, o alcance é "esta" (só a ocorrência), "proximas" (esta e as próximas) ou "todas".
- Tudo em dois passos (agenda.confirmar): a 1ª chamada só descreve o que vai fazer.
- Convites (o organizador é outra pessoa) não são mexidos.
- Antes de mudar, guarda como desfazer (data/agenda-lixeira.json); agenda_desfazer vale por 24 horas.
"""
from __future__ import annotations

import time as relogio_parede
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app import agenda as agenda_google
from app import config, google_auth
from app.agenda import (AJUDA_HORA, DURACAO_MAXIMA, DURACAO_MINIMA, _instante, _rfc3339, confirmar,
                        interpretar_hora)
from app.google_auth import ErroGoogle
from app.periodos import PeriodoInvalido, inicio_do_dia, interpretar
from app.recorrencia import com_vezes, cortar_regra, descrever_regra, partes_da_regra
from app.textos import (PALAVRAS_VAZIAS, agora as agora_local, as_hora, data_falada, faixa_horas, fuso, maiuscula,
                        normalizar)

JANELA_PADRAO = 60  # dias à frente, quando o usuário não diz quando
VALIDADE_DESFAZER = 24 * 3600
MAX_LIXEIRA = 20
ALCANCES = {
    "esta": ("esta", "essa", "este", "esse", "so esta", "so essa", "so este", "so esse", "apenas esta",
             "somente esta", "so a de", "so hoje", "uma", "so uma", "ocorrencia"),
    "proximas": ("proximas", "esta e as proximas", "essa e as proximas", "esta e as seguintes", "daqui para frente",
                 "daqui pra frente", "a partir desta", "a partir dessa", "desta em diante", "dessa em diante",
                 "seguintes", "futuras"),
    "todas": ("todas", "todos", "toda a serie", "a serie toda", "serie", "a serie", "todas as ocorrencias", "sempre"),
}
# Palavras que o usuário fala mas não estão no título ("a aula de inglês" acha "Inglês com Jean")
GENERICAS = {"evento", "eventos", "compromisso", "aula", "aulas", "reuniao", "horario", "agenda", "marcado",
             "marcada", "consulta", "ocorrencia", "serie"}
CAMPOS_COPIADOS = ("summary", "description", "location", "colorId", "reminders", "transparency", "visibility")


@dataclass
class Achado:
    conta: dict
    agenda: str
    item: dict  # a ocorrência (numa série) ou o evento avulso, como vem da API

    @property
    def serie(self) -> str:
        return str(self.item.get("recurringEventId") or "")

    @property
    def titulo(self) -> str:
        return " ".join(str(self.item.get("summary") or "").split()) or "Evento sem título"

    @property
    def dia_todo(self) -> bool:
        return not (self.item.get("start") or {}).get("dateTime")

    @property
    def inicio(self) -> datetime:
        comeco = self.item.get("start") or {}
        return _instante(comeco["dateTime"]) if comeco.get("dateTime") else inicio_do_dia(
            date.fromisoformat(comeco["date"]))

    @property
    def fim(self) -> datetime:
        termino = self.item.get("end") or {}
        if termino.get("dateTime"):
            return _instante(termino["dateTime"])
        return inicio_do_dia(date.fromisoformat(termino["date"])) if termino.get("date") else self.inicio

    def de_outra_pessoa(self) -> str:
        """O e-mail do organizador, se o evento é um convite; "" se é do usuário."""
        organizador = self.item.get("organizer") or {}
        if not organizador or organizador.get("self"):
            return ""
        return str(organizador.get("displayName") or organizador.get("email") or "outra pessoa")


# ---------------------------------------------------------------- achar

def _url_eventos(agenda: str, *resto: str) -> str:
    return "/".join([agenda_google.URL_CALENDARIO, "calendars", urllib.parse.quote(agenda, safe=""), "events"] +
                    [urllib.parse.quote(r, safe="") for r in resto])


def _palavras(texto: str) -> list[str]:
    palavras = [p for p in normalizar(texto).split() if len(p) > 1 and p not in PALAVRAS_VAZIAS]
    especificas = [p for p in palavras if p not in GENERICAS]
    return especificas or palavras


async def _agendas_editaveis(conta: dict) -> list[str]:
    try:
        lista = await google_auth.chamar(conta, "GET", agenda_google.URL_CALENDARIO + "/users/me/calendarList",
                                         {"minAccessRole": "writer", "maxResults": 250})
    except ErroGoogle as erro:
        if "expirou ou foi revogada" in str(erro):
            raise
        return ["primary"]
    agendas = [a["id"] for a in lista.get("items") or [] if a.get("primary") or a.get("selected")]
    return agendas or ["primary"]


async def achar(evento: str, quando: str, conta: str, agora: datetime) -> tuple[list[Achado], str, str]:
    """(achados, alcance falado do período, erro). Procura só em agendas que o usuário pode editar."""
    palavras = _palavras(evento)
    if not palavras:
        return [], "", "Diga qual evento: o título, ou parte dele (ex.: inglês)."
    try:
        if (quando or "").strip():
            periodo = interpretar(quando, agora)
            inicio, fim, alcance = periodo.inicio, periodo.fim, periodo.alcance
        else:
            inicio, fim, alcance = agora, agora + timedelta(days=JANELA_PADRAO), "nos próximos %d dias" % JANELA_PADRAO
    except PeriodoInvalido as erro:
        return [], "", str(erro)
    contas = google_auth.escolher_contas(conta)
    achados = []
    for c in contas:
        for agenda in await _agendas_editaveis(c):
            dados = await google_auth.chamar(c, "GET", _url_eventos(agenda), {
                "q": " ".join(palavras), "timeMin": _rfc3339(inicio), "timeMax": _rfc3339(fim),
                "singleEvents": "true", "orderBy": "startTime", "maxResults": 50})
            for item in dados.get("items") or []:
                titulo = normalizar(item.get("summary") or "")
                if item.get("status") != "cancelled" and all(p in titulo for p in palavras):
                    achados.append(Achado(c, agenda, item))
    achados.sort(key=lambda a: a.inicio)
    return achados, alcance, ""


def _descrever_ocorrencia(a: Achado, hoje: date) -> str:
    dia = data_falada(a.inicio.date(), com_ano=a.inicio.year != hoje.year)
    return "%s, %s" % (dia, "dia todo" if a.dia_todo else faixa_horas(a.inicio, a.fim))


def _escolher(achados: list[Achado], evento: str, alcance_periodo: str, hoje: date) -> tuple[Achado | None, str]:
    """O evento a mexer (a primeira ocorrência, se for uma série), ou a pergunta a fazer."""
    if not achados:
        return None, 'Não achei nenhum evento com "%s" %s nas agendas que você pode editar.' % (
            evento.strip(), alcance_periodo)
    grupos: dict[tuple, list[Achado]] = {}
    for a in achados:
        grupos.setdefault((a.conta["rotulo"], a.agenda, a.serie or a.item.get("id")), []).append(a)
    if len(grupos) > 1:
        opcoes = ["%s (%s)" % (_aspas(g[0].titulo), _descrever_ocorrencia(g[0], hoje)) for g in list(grupos.values())[:5]]
        return None, ('Achei mais de um evento com "%s": %s. Pergunte ao usuário qual, e chame de novo com o título '
                      "ou o dia certo." % (evento.strip(), "; ".join(opcoes)))
    return next(iter(grupos.values()))[0], ""


def _aspas(texto: str) -> str:
    return '"%s"' % texto.replace('"', "'")


def _alcance(texto: str) -> str | None:
    q = normalizar(texto)
    if not q:
        return ""
    for nome, sinonimos in ALCANCES.items():
        if q in sinonimos:
            return nome
    for nome in ("proximas", "todas", "esta"):  # frases mais longas ("apague esta e as próximas")
        if any(s in q for s in ALCANCES[nome] if len(s) > 4):
            return nome
    return None


async def _mestre(a: Achado) -> dict:
    return await google_auth.chamar(a.conta, "GET", _url_eventos(a.agenda, a.serie))


def _inicio_do_mestre(mestre: dict) -> datetime:
    comeco = mestre.get("start") or {}
    return _instante(comeco["dateTime"]) if comeco.get("dateTime") else inicio_do_dia(date.fromisoformat(comeco["date"]))


def _copia(evento: dict) -> dict:
    """Os campos de um evento que dá para recriar (sem id, sem convidados: recriar não manda convites)."""
    corpo = {k: evento[k] for k in CAMPOS_COPIADOS if k in evento}
    corpo["start"], corpo["end"] = dict(evento["start"]), dict(evento["end"])
    for ponta in (corpo["start"], corpo["end"]):
        if "dateTime" in ponta:
            ponta.setdefault("timeZone", fuso().key)  # uma série precisa do fuso para repetir no horário certo
    if evento.get("recurrence"):
        corpo["recurrence"] = list(evento["recurrence"])
    return corpo


# ---------------------------------------------------------------- lixeira

def _ler_lixeira() -> list[dict]:
    dados = config.ler_json(config.dados() / "agenda-lixeira.json") or {}
    return [m for m in dados.get("mudancas") or [] if isinstance(m, dict)]


def _guardar(conta: str, descricao: str, desfazer: list[list]) -> None:
    agora = relogio_parede.time()
    mudancas = [m for m in _ler_lixeira() if agora - float(m.get("quando") or 0) < 7 * 24 * 3600]
    mudancas.append({"quando": agora, "conta": conta, "descricao": descricao, "desfazer": desfazer, "desfeita": False})
    config.salvar_json_privado(config.dados() / "agenda-lixeira.json", {"mudancas": mudancas[-MAX_LIXEIRA:]})


async def _executar(conta: dict, passos: list[list]) -> list[dict]:
    """Passos: ["POST", agenda, corpo] | ["PATCH", agenda, id, corpo] | ["DELETE", agenda, id]."""
    respostas = []
    for passo in passos:
        metodo, agenda = passo[0], passo[1]
        if metodo == "POST":
            respostas.append(await google_auth.chamar(conta, "POST", _url_eventos(agenda), corpo=passo[2]))
        elif metodo == "PATCH":
            respostas.append(await google_auth.chamar(conta, "PATCH", _url_eventos(agenda, passo[2]), corpo=passo[3]))
        else:
            respostas.append(await google_auth.chamar(conta, "DELETE", _url_eventos(agenda, passo[2])))
    return respostas


def _pergunta_alcance(acao: str, a: Achado, regra: str, hoje: date) -> str:
    return ('%s se repete (%s). Pergunte ao usuário se é para %s só a de %s, esta e as próximas, ou todas; depois '
            "chame de novo com alcance esta, proximas ou todas." % (
                _aspas(a.titulo), regra, acao, data_falada(a.inicio.date(), com_ano=a.inicio.year != hoje.year)))


# ---------------------------------------------------------------- apagar

async def apagar(evento: str, quando: str = "", alcance: str = "", conta: str = "",
                 agora: datetime | None = None) -> str:
    agora = agora or agora_local()
    hoje = agora.date()
    try:
        achados, periodo, erro = await achar(evento, quando, conta, agora)
        if erro:
            return erro
        alvo, pergunta = _escolher(achados, evento, periodo, hoje)
        if not alvo:
            return pergunta
        dono = alvo.de_outra_pessoa()
        if dono:
            return ("%s é um convite de %s: não mexo em eventos de outras pessoas. O usuário pode recusar pelo Google "
                    "Agenda." % (_aspas(alvo.titulo), dono))
        escolha = _alcance(alcance)
        if escolha is None:
            return 'Não entendi o alcance "%s". Use esta, proximas ou todas.' % alcance.strip()
        mestre = await _mestre(alvo) if alvo.serie else None
        regra = descrever_regra(mestre.get("recurrence") or [], _inicio_do_mestre(mestre).date(), hoje) if mestre else ""
        if mestre and not escolha:
            return _pergunta_alcance("apagar", alvo, regra, hoje)
        quando_falado = _descrever_ocorrencia(alvo, hoje)
        if not mestre:
            escolha = "esta"
            descricao = "%s, %s" % (_aspas(alvo.titulo), quando_falado)
            passos, desfazer = [["DELETE", alvo.agenda, alvo.item["id"]]], [["POST", alvo.agenda, _copia(alvo.item)]]
        elif escolha == "esta":
            descricao = "%s (%s), só a de %s" % (_aspas(alvo.titulo), regra, quando_falado)
            passos = [["DELETE", alvo.agenda, alvo.item["id"]]]
            desfazer = [["POST", alvo.agenda, _copia(alvo.item)]]  # volta como um evento avulso
        elif escolha == "todas" or _inicio_do_mestre(mestre) >= alvo.inicio:
            escolha = "todas"
            descricao = "%s (%s), todas as ocorrências" % (_aspas(alvo.titulo), regra)
            passos, desfazer = [["DELETE", alvo.agenda, mestre["id"]]], [["POST", alvo.agenda, _copia(mestre)]]
        else:
            descricao = "%s (%s), esta e as próximas, a partir de %s" % (_aspas(alvo.titulo), regra, quando_falado)
            corte = alvo.inicio.date() - timedelta(days=1) if alvo.dia_todo else alvo.inicio - timedelta(seconds=1)
            original = list(mestre.get("recurrence") or [])
            passos = [["PATCH", alvo.agenda, mestre["id"], {"recurrence": cortar_regra(original, corte, alvo.dia_todo)}]]
            desfazer = [["PATCH", alvo.agenda, mestre["id"], {"recurrence": original}]]
        rotulo = alvo.conta["rotulo"]
        espera = confirmar(
            ("apagar", rotulo, alvo.item.get("id"), escolha),
            "Ainda não apaguei. Pergunte ao usuário: posso apagar %s, na conta %s? Só chame agenda_apagar de novo, "
            "com os mesmos dados, depois que ele disser que sim." % (descricao, rotulo),
            "Ainda não apaguei: o usuário não confirmou. Pergunte a ele se pode apagar %s e espere a resposta."
            % descricao)
        if espera:
            return espera
        await _executar(alvo.conta, passos)
    except ErroGoogle as erro:
        return "Não consegui mexer na agenda: %s." % erro
    _guardar(rotulo, "apagou " + descricao, desfazer)
    return "Apagado na conta %s: %s. Dá para desfazer em até 24 horas." % (rotulo, descricao)


# ---------------------------------------------------------------- alterar

def _novo_horario(base_dia: date, inicio: datetime, fim: datetime, dia_todo: bool, hora: tuple | None,
                  duracao: int | None) -> tuple[dict, dict, datetime, datetime]:
    """(start, end, início, fim) do evento mudado, mantendo a duração se ela não foi dita."""
    if hora is None and dia_todo:
        dias = max(1, (fim.date() - inicio.date()).days)
        return ({"date": base_dia.isoformat()}, {"date": (base_dia + timedelta(days=dias)).isoformat()},
                inicio_do_dia(base_dia), inicio_do_dia(base_dia + timedelta(days=dias)))
    h = hora or (inicio.hour, inicio.minute)
    novo_inicio = datetime.combine(base_dia, time(*h), tzinfo=fuso())
    minutos = duracao or (60 if dia_todo else int((fim - inicio).total_seconds() // 60) or 60)
    novo_fim = novo_inicio + timedelta(minutes=minutos)
    zona = fuso().key
    return ({"dateTime": _rfc3339(novo_inicio), "timeZone": zona}, {"dateTime": _rfc3339(novo_fim), "timeZone": zona},
            novo_inicio, novo_fim)


async def alterar(evento: str, quando: str = "", alcance: str = "", novo_titulo: str = "", nova_data: str = "",
                  nova_hora: str = "", nova_duracao_minutos: int = 0, conta: str = "",
                  agora: datetime | None = None) -> str:
    agora = agora or agora_local()
    hoje = agora.date()
    novo_titulo = " ".join((novo_titulo or "").split())[:200]
    hora = None
    if (nova_hora or "").strip():
        hora = interpretar_hora(nova_hora)
        if hora is None:
            return 'Não entendi a hora "%s". %s' % (nova_hora.strip(), AJUDA_HORA)
    try:
        duracao = int(nova_duracao_minutos or 0) or None
    except (TypeError, ValueError):
        return "A duração precisa ser um número de minutos, por exemplo 60."
    if duracao and not DURACAO_MINIMA <= duracao <= DURACAO_MAXIMA:
        return "A duração precisa ficar entre 5 minutos e 24 horas (1440 minutos)."
    novo_dia = None
    if (nova_data or "").strip():
        try:
            novo_dia = interpretar(nova_data, agora, um_dia=True).dia
        except PeriodoInvalido as erro:
            return str(erro)
        if novo_dia < hoje:
            return "O dia %s já passou. Diga uma data de hoje em diante." % data_falada(novo_dia, com_ano=True)
    if not (novo_titulo or hora or duracao or novo_dia):
        return "Diga o que mudar: o título, o dia, a hora ou a duração."
    try:
        achados, periodo, erro = await achar(evento, quando, conta, agora)
        if erro:
            return erro
        alvo, pergunta = _escolher(achados, evento, periodo, hoje)
        if not alvo:
            return pergunta
        dono = alvo.de_outra_pessoa()
        if dono:
            return "%s é um convite de %s: não mexo em eventos de outras pessoas." % (_aspas(alvo.titulo), dono)
        escolha = _alcance(alcance)
        if escolha is None:
            return 'Não entendi o alcance "%s". Use esta, proximas ou todas.' % alcance.strip()
        mestre = await _mestre(alvo) if alvo.serie else None
        regra = descrever_regra(mestre.get("recurrence") or [], _inicio_do_mestre(mestre).date(), hoje) if mestre else ""
        if mestre and not escolha:
            return _pergunta_alcance("mudar", alvo, regra, hoje)
        if mestre and escolha != "esta" and novo_dia:
            return ("Para mudar o dia de uma série, crie uma nova com agenda_criar e apague a antiga (esta e as "
                    "próximas). Com alcance esta, dá para mudar o dia só desta ocorrência.")
        if mestre and escolha == "proximas" and _inicio_do_mestre(mestre) >= alvo.inicio:
            escolha = "todas"
        mudancas = []
        if novo_titulo:
            mudancas.append("título para " + _aspas(novo_titulo))
        rotulo = alvo.conta["rotulo"]

        if not mestre or escolha == "esta":
            start, end, ini, fim = _novo_horario(novo_dia or alvo.inicio.date(), alvo.inicio, alvo.fim, alvo.dia_todo,
                                                 hora, duracao)
            if novo_dia or hora or duracao:
                mudancas.append("%s, %s" % (data_falada(ini.date(), com_ano=ini.year != hoje.year),
                                            "dia todo" if "date" in start else faixa_horas(ini, fim)))
            corpo = {"start": start, "end": end} if (novo_dia or hora or duracao) else {}
            if novo_titulo:
                corpo["summary"] = novo_titulo
            onde = "%s, %s" % (_aspas(alvo.titulo), _descrever_ocorrencia(alvo, hoje)) + (
                " (só esta ocorrência)" if mestre else "")
            passos = [["PATCH", alvo.agenda, alvo.item["id"], corpo]]
            desfazer = [["PATCH", alvo.agenda, alvo.item["id"], {k: alvo.item[k] for k in ("start", "end", "summary")
                                                                 if k in alvo.item}]]
        else:
            base = _inicio_do_mestre(mestre)
            fim_mestre = _instante(mestre["end"]["dateTime"]) if mestre["end"].get("dateTime") else inicio_do_dia(
                date.fromisoformat(mestre["end"]["date"]))
            dia_base = base.date() if escolha == "todas" else alvo.inicio.date()
            start, end, ini, fim = _novo_horario(dia_base, base, fim_mestre, alvo.dia_todo, hora, duracao)
            if hora or duracao:
                mudancas.append("dia todo" if "date" in start else faixa_horas(ini, fim))
            original = list(mestre.get("recurrence") or [])
            if escolha == "todas":
                onde = "%s (%s), todas as ocorrências" % (_aspas(alvo.titulo), regra)
                corpo = {"start": start, "end": end} if (hora or duracao) else {}
                if novo_titulo:
                    corpo["summary"] = novo_titulo
                passos = [["PATCH", alvo.agenda, mestre["id"], corpo]]
                desfazer = [["PATCH", alvo.agenda, mestre["id"], {k: mestre[k] for k in ("start", "end", "summary")
                                                                  if k in mestre}]]
            else:  # esta e as próximas: a série antiga termina antes desta; uma nova começa nela, já mudada
                onde = "%s (%s), esta e as próximas, a partir de %s" % (_aspas(alvo.titulo), regra,
                                                                        _descrever_ocorrencia(alvo, hoje))
                nova_regra = original
                vezes = partes_da_regra(original).get("COUNT")
                if vezes and vezes.isdigit():
                    antes = await google_auth.chamar(alvo.conta, "GET", _url_eventos(alvo.agenda, mestre["id"], "instances"), {
                        "timeMin": _rfc3339(base), "timeMax": _rfc3339(alvo.inicio), "maxResults": 2500})
                    restantes = int(vezes) - len(antes.get("items") or [])
                    nova_regra = com_vezes(original, max(1, restantes))
                corte = alvo.inicio.date() - timedelta(days=1) if alvo.dia_todo else alvo.inicio - timedelta(seconds=1)
                nova = _copia(mestre)
                nova.update({"start": start, "end": end, "recurrence": nova_regra})
                if novo_titulo:
                    nova["summary"] = novo_titulo
                passos = [["PATCH", alvo.agenda, mestre["id"], {"recurrence": cortar_regra(original, corte,
                                                                                           alvo.dia_todo)}],
                          ["POST", alvo.agenda, nova]]
                desfazer = [["PATCH", alvo.agenda, mestre["id"], {"recurrence": original}]]  # + apagar a nova (abaixo)
        descricao = "%s: %s" % (onde, "; ".join(mudancas))
        espera = confirmar(
            ("alterar", rotulo, alvo.item.get("id"), escolha, novo_titulo, str(novo_dia), str(hora), str(duracao)),
            "Ainda não mudei. Pergunte ao usuário: posso mudar %s, na conta %s? Só chame agenda_alterar de novo, com "
            "os mesmos dados, depois que ele disser que sim." % (descricao, rotulo),
            "Ainda não mudei: o usuário não confirmou. Pergunte a ele se pode mudar %s e espere a resposta."
            % descricao)
        if espera:
            return espera
        respostas = await _executar(alvo.conta, passos)
        if passos[-1][0] == "POST" and isinstance(respostas[-1], dict) and respostas[-1].get("id"):
            desfazer.append(["DELETE", alvo.agenda, respostas[-1]["id"]])
    except ErroGoogle as erro:
        return "Não consegui mexer na agenda: %s." % erro
    _guardar(rotulo, "mudou " + descricao, desfazer)
    return "Mudado na conta %s: %s. Dá para desfazer em até 24 horas." % (rotulo, descricao)


# ---------------------------------------------------------------- desfazer

async def desfazer(agora: datetime | None = None) -> str:
    mudancas = _ler_lixeira()
    agora_s = relogio_parede.time()
    candidatas = [m for m in mudancas if not m.get("desfeita") and agora_s - float(m.get("quando") or 0)
                  < VALIDADE_DESFAZER]
    if not candidatas:
        return "Não há nenhuma mudança na agenda das últimas 24 horas para desfazer."
    ultima = candidatas[-1]
    descricao = str(ultima.get("descricao") or "a última mudança")
    espera = confirmar(
        ("desfazer", ultima.get("quando")),
        "Ainda não desfiz. Pergunte ao usuário: posso desfazer isto: %s? Só chame agenda_desfazer de novo depois que "
        "ele disser que sim." % descricao,
        "Ainda não desfiz: o usuário não confirmou. Pergunte a ele e espere a resposta.")
    if espera:
        return espera
    try:
        contas = google_auth.escolher_contas(str(ultima.get("conta") or ""))
        await _executar(contas[0], ultima.get("desfazer") or [])
    except ErroGoogle as erro:
        return "Não consegui desfazer: %s." % erro
    ultima["desfeita"] = True
    config.salvar_json_privado(config.dados() / "agenda-lixeira.json", {"mudancas": mudancas})
    extra = " Uma ocorrência apagada sozinha volta como evento avulso." if ultima.get("descricao", "").startswith(
        "apagou") and "só a de" in ultima.get("descricao", "") else ""
    return "Desfeito: %s.%s" % (maiuscula(descricao), extra)

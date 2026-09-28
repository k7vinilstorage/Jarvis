"""Períodos pedidos em português ('hoje', 'semana', 'sexta', '26/09', 'dia 30'...) viram intervalos de tempo
no fuso local, com um rótulo pronto para falar. Usado por Moodle, Agenda e E-mails."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.textos import DIAS, MESES, agora as agora_local, data_falada, dia_do_mes, fuso, normalizar

AJUDA = ("Use hoje, amanhã, depois de amanhã, um dia da semana (por exemplo sexta), uma data como 26/09 "
         "ou dia 30, fim de semana, semana, próximas semanas ou mês.")
AJUDA_UM_DIA = "Diga um dia só: hoje, amanhã, um dia da semana (por exemplo sexta) ou uma data como 26/09."
AJUDA_RECENTE = "Use hoje, ontem, 2 dias ou semana."

NOMES_DIAS = {normalizar(d.split("-")[0]): i for i, d in enumerate(DIAS)}  # segunda: 0 ... domingo: 6
NOMES_MESES = {normalizar(m): i + 1 for i, m in enumerate(MESES)}
NUMEROS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
           "quinze": 15, "trinta": 30}


class PeriodoInvalido(ValueError):
    """O texto não é um período que eu entenda; a mensagem já explica o que usar."""


@dataclass(frozen=True)
class Periodo:
    inicio: datetime  # com fuso
    fim: datetime  # exclusivo
    rotulo: str  # 'hoje, quinta-feira, 24 de setembro' ou 'até quinta-feira, 1º de outubro'
    um_dia: bool

    @property
    def dia(self) -> date:
        return self.inicio.date()

    @property
    def alcance(self) -> str:
        """Para completar frases: 'Nada na agenda para hoje, ...' / 'Nada na agenda até quinta-feira, ...'."""
        return "para " + self.rotulo if self.um_dia else self.rotulo


def proximo_sabado(hoje: date) -> date:
    """O sábado do fim de semana pedido: hoje, se já é sábado; no domingo, o da semana que vem."""
    return hoje + timedelta(days=(5 - hoje.weekday()) % 7)


def inicio_do_dia(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=fuso())


def rotulo_dia(d: date, hoje: date) -> str:
    """'hoje, quinta-feira, 24 de setembro', 'amanhã, ...', 'ontem, ...' ou só a data falada."""
    texto = data_falada(d, com_ano=d.year != hoje.year)
    prefixo = {0: "hoje, ", 1: "amanhã, ", -1: "ontem, "}.get((d - hoje).days, "")
    return prefixo + texto


def _periodo_dia(d: date, hoje: date) -> Periodo:
    return Periodo(inicio_do_dia(d), inicio_do_dia(d + timedelta(days=1)), rotulo_dia(d, hoje), True)


def _data_curta(d: date) -> str:
    return "%s de %s" % (dia_do_mes(d), MESES[d.month - 1])


def _dias(inicio: date, fim_inclusivo: date, hoje: date, rotulo: str) -> Periodo:
    if inicio == fim_inclusivo:
        return _periodo_dia(inicio, hoje)
    return Periodo(inicio_do_dia(inicio), inicio_do_dia(fim_inclusivo + timedelta(days=1)), rotulo, False)


def _criar_data(ano: int, mes: int, dia: int, original: str) -> date:
    try:
        return date(ano, mes, dia)
    except ValueError:
        raise PeriodoInvalido("A data %s não existe. %s" % (original.strip(), AJUDA)) from None


def _data_no_texto(q: str, hoje: date, original: str) -> date | None:
    """Datas: 2026-09-26, 26/09, 26/09/2026, 26/09/26, '26 de setembro', 'dia 30'. Sem ano = a próxima."""
    iso = re.search(r"\b(\d{4}) (\d{1,2}) (\d{1,2})\b", q)  # normalizar troca "-" por espaço
    if iso:
        return _criar_data(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), original)
    barra = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b", q)
    if barra:
        dia, mes = int(barra.group(1)), int(barra.group(2))
        if barra.group(3):
            ano = int(barra.group(3))
            return _criar_data(ano + 2000 if ano < 100 else ano, mes, dia, original)
        return _proxima_data(dia, mes, hoje, original)
    por_extenso = re.search(r"\b(\d{1,2}) de (%s)\b(?: de (\d{4}))?" % "|".join(NOMES_MESES), q)
    if por_extenso:
        dia, mes = int(por_extenso.group(1)), NOMES_MESES[por_extenso.group(2)]
        if por_extenso.group(3):
            return _criar_data(int(por_extenso.group(3)), mes, dia, original)
        return _proxima_data(dia, mes, hoje, original)
    solto = re.search(r"\bdia (\d{1,2})\b", q)
    if solto:
        dia = int(solto.group(1))
        if not 1 <= dia <= 31:
            raise PeriodoInvalido("O dia %d não existe. %s" % (dia, AJUDA))
        ano, mes = hoje.year, hoje.month
        for _ in range(13):  # o próximo mês que tem esse dia (dia 31 pula meses curtos)
            try:
                candidata = date(ano, mes, dia)
                if candidata >= hoje:
                    return candidata
            except ValueError:
                pass
            ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return None


def _proxima_data(dia: int, mes: int, hoje: date, original: str) -> date:
    candidata = _criar_data(hoje.year, mes, dia, original) if (mes, dia) != (2, 29) else None
    if candidata is None:  # 29/02: o próximo ano bissexto
        ano = hoje.year
        while True:
            try:
                candidata = date(ano, 2, 29)
                if candidata >= hoje:
                    return candidata
            except ValueError:
                pass
            ano += 1
    return candidata if candidata >= hoje else _criar_data(hoje.year + 1, mes, dia, original)


def interpretar(texto: str, agora: datetime | None = None, *, um_dia: bool = False) -> Periodo:
    """Período pedido, do jeito que se fala. Levanta PeriodoInvalido com uma mensagem de ajuda."""
    agora = (agora or agora_local()).astimezone(fuso())
    hoje = agora.date()
    q = normalizar(texto) or "hoje"
    palavras = q.split()
    periodo = None

    if "hoje" in palavras and "amanha" in palavras:
        periodo = _dias(hoje, hoje + timedelta(days=1), hoje, "hoje e amanhã")
    elif q in ("hj", "agora") or palavras[0] == "hoje":
        periodo = _periodo_dia(hoje, hoje)
    elif "depois de amanha" in q:
        d = hoje + timedelta(days=2)
        periodo = Periodo(inicio_do_dia(d), inicio_do_dia(d + timedelta(days=1)),
                          "depois de amanhã, " + data_falada(d), True)
    elif "amanha" in palavras:
        periodo = _periodo_dia(hoje + timedelta(days=1), hoje)
    elif "ontem" in palavras and "anteontem" not in palavras:
        periodo = _periodo_dia(hoje - timedelta(days=1), hoje)
    elif {"passado", "passada", "anterior", "retrasado", "retrasada", "anteontem"} & set(palavras):
        raise PeriodoInvalido("Só olho de hoje em diante, ou ontem. Para um dia que já passou, diga a data com "
                              "o ano, como 10/09/%d." % hoje.year)
    elif "fim de semana" in q or "final de semana" in q:
        # No sábado, este; no domingo, o próximo (o de hoje já está acabando)
        sabado = proximo_sabado(hoje)
        domingo = sabado + timedelta(days=1)
        rotulo = "no fim de semana, %s e %s" % (
            str(sabado.day) if sabado.month == domingo.month else _data_curta(sabado), _data_curta(domingo))
        periodo = _dias(sabado, domingo, hoje, rotulo)
    elif "semana que vem" in q or "proxima semana" in q:
        segunda = hoje + timedelta(days=7 - hoje.weekday())
        domingo = segunda + timedelta(days=6)
        periodo = _dias(segunda, domingo, hoje, "na semana que vem, de %s a %s" % (
            _data_curta(segunda), _data_curta(domingo)))
    else:
        data = _data_no_texto(q, hoje, texto)
        semanas = re.search(r"\b(?:(\d+|uma|duas|tres|quatro|cinco) )?semanas\b", q)
        if data is not None:
            periodo = _periodo_dia(data, hoje)
        elif semanas:
            # "próximas semanas" = 30 dias; "2 semanas", "duas semanas" = 14 dias
            numero = semanas.group(1)
            n = int(numero) if numero and numero.isdigit() else NUMEROS.get(numero or "", 0)
            if 1 <= n <= 4:
                ultimo = (agora + timedelta(days=7 * n)).date()
                periodo = Periodo(agora, inicio_do_dia(ultimo + timedelta(days=1)),
                                  "até " + data_falada(ultimo, com_ano=ultimo.year != hoje.year), False)
            else:
                periodo = Periodo(agora, agora + timedelta(days=30), "nos próximos 30 dias", False)
        elif "semana" in palavras or re.search(r"\b(7|sete) dias\b", q) or "proximos dias" in q:
            # O rótulo cita o sétimo dia; a janela vai até o fim dele (um prazo às 23h59 desse dia entra)
            ultimo = (agora + timedelta(days=7)).date()
            periodo = Periodo(agora, inicio_do_dia(ultimo + timedelta(days=1)),
                              "até " + data_falada(ultimo, com_ano=ultimo.year != hoje.year), False)
        elif "mes" in palavras or re.search(r"\b(30|trinta) dias\b", q):
            periodo = Periodo(agora, agora + timedelta(days=30), "nos próximos 30 dias", False)
        else:
            for palavra in palavras:
                if palavra in NOMES_DIAS:
                    avanco = (NOMES_DIAS[palavra] - hoje.weekday()) % 7
                    if avanco == 0 and ("proxima" in palavras or "proximo" in palavras or "que vem" in q):
                        avanco = 7
                    periodo = _periodo_dia(hoje + timedelta(days=avanco), hoje)
                    break

    if periodo is None:
        raise PeriodoInvalido('Não entendi o período "%s". %s' % (texto.strip(), AJUDA))
    if um_dia and not periodo.um_dia:
        raise PeriodoInvalido(AJUDA_UM_DIA)
    return periodo


def interpretar_recente(texto: str, agora: datetime | None = None) -> Periodo:
    """Período para trás, para e-mails: hoje, ontem (= 2 dias), 'N dias', semana (7 dias) ou mês (30)."""
    agora = (agora or agora_local()).astimezone(fuso())
    hoje = agora.date()
    q = normalizar(texto) or "hoje"
    palavras = q.split()
    numero = re.search(r"\b(\d{1,2}|%s) dias?\b" % "|".join(NUMEROS), q)
    if q in ("hj", "agora") or palavras[0] == "hoje":
        dias, rotulo = 1, "hoje"
    elif "ontem" in palavras:
        dias, rotulo = 2, "desde ontem"
    elif "semana" in palavras:
        dias, rotulo = 7, "nos últimos 7 dias"
    elif "mes" in palavras:
        dias, rotulo = 30, "nos últimos 30 dias"
    elif numero:
        valor = numero.group(1)
        dias = int(valor) if valor.isdigit() else NUMEROS[valor]
        if not 1 <= dias <= 30:
            raise PeriodoInvalido("Posso olhar de 1 a 30 dias para trás. " + AJUDA_RECENTE)
        rotulo = "hoje" if dias == 1 else "desde ontem" if dias == 2 else "nos últimos %d dias" % dias
    else:
        raise PeriodoInvalido('Não entendi o período "%s". %s' % (texto.strip(), AJUDA_RECENTE))
    inicio = inicio_do_dia(hoje - timedelta(days=dias - 1))
    return Periodo(inicio, agora, rotulo, dias == 1)

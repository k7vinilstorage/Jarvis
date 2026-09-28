"""Eventos que se repetem. O pedido em português ('toda segunda e quarta até 20/12') vira a regra do Google (RRULE,
RFC 5545); uma regra que já existe vira texto para falar; e uma série pode ser cortada numa data ('esta e as
próximas'). Só contas, sem rede: quem interpreta a repetição é a ferramenta, nunca o modelo."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from app.periodos import NOMES_DIAS, NUMEROS, PeriodoInvalido, interpretar
from app.textos import MESES, data_falada, fuso, juntar_com_e, normalizar

CODIGOS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_NOMES = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
_PLURAIS = ["segundas", "terças", "quartas", "quintas", "sextas", "sábados", "domingos"]
_MASCULINOS = {5, 6}  # "todo sábado", "todo domingo"; os outros são "toda"
MAX_VEZES = 500

AJUDA = ("Use toda semana, toda segunda e quarta, todo dia, dias úteis, a cada 2 semanas, todo mês ou todo ano; "
         "e, se quiser, até 20/12 ou 10 vezes.")


class RepeticaoInvalida(ValueError):
    """O pedido de repetição não dá para entender; a mensagem já explica o que usar."""


@dataclass(frozen=True)
class Repeticao:
    regra: str      # 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261221T025959Z'
    descricao: str  # 'às segundas e quartas, até sexta-feira, 18 de dezembro'
    primeiro: date  # o primeiro dia que bate com a regra, a partir do dia pedido


def _numero(palavra: str) -> int | None:
    if palavra.isdigit():
        return int(palavra)
    return NUMEROS.get(palavra)


def _dias_no_texto(palavras: list[str]) -> list[int]:
    dias = []
    for palavra in palavras:
        base = palavra[:-1] if palavra.endswith("s") and palavra[:-1] in NOMES_DIAS else palavra
        if base in NOMES_DIAS and NOMES_DIAS[base] not in dias:
            dias.append(NOMES_DIAS[base])
    return sorted(dias)


def _fim_do_texto(q: str, inicio: date) -> tuple[str, date | None, int | None]:
    """Tira o fim do pedido ('até 20/12', '10 vezes') e devolve (o resto, a data final, a quantidade)."""
    vezes = re.search(r"\b(?:por )?(\d+|%s) (?:vezes|vez|ocorrencias?|repeticoes|repeticao)\b" % "|".join(NUMEROS), q)
    if vezes:
        n = _numero(vezes.group(1))
        if not n or not 2 <= n <= MAX_VEZES:
            raise RepeticaoInvalida("A quantidade de vezes precisa ficar entre 2 e %d." % MAX_VEZES)
        return (q[:vezes.start()] + q[vezes.end():]).strip(), None, n
    ate = re.search(r"\bate (?:o |a )?(?:dia )?(.+)$", q)
    if ate:
        referencia = datetime.combine(inicio, time(0, 1), tzinfo=fuso())
        try:
            dia = interpretar(ate.group(1), referencia, um_dia=True).dia
        except PeriodoInvalido:
            raise RepeticaoInvalida('Não entendi até quando: "%s". Diga uma data, como até 20/12.'
                                    % ate.group(1)) from None
        return q[:ate.start()].strip(), dia, None
    return q, None, None


def _frase_dias(dias: list[int]) -> str:
    if dias == [0, 1, 2, 3, 4]:
        return "de segunda a sexta"
    if len(dias) == 1:
        d = dias[0]
        return ("todo " if d in _MASCULINOS else "toda ") + _NOMES[d]
    femininos = [_PLURAIS[d] for d in dias if d not in _MASCULINOS]
    masculinos = [_PLURAIS[d] for d in dias if d in _MASCULINOS]
    partes = (["às " + juntar_com_e(femininos)] if femininos else []) + (
        ["aos " + juntar_com_e(masculinos)] if masculinos else [])
    return " e ".join(partes)


def _descrever(freq: str, intervalo: int, dias: list[int], inicio: date) -> str:
    if freq == "DAILY":
        return "todo dia" if intervalo == 1 else "a cada %d dias" % intervalo
    if freq == "WEEKLY":
        frase = _frase_dias(dias or [inicio.weekday()])
        return frase if intervalo == 1 else "a cada %d semanas, %s" % (intervalo, frase.replace("toda ", "na ").replace(
            "todo ", "no "))
    if freq == "MONTHLY":
        base = "todo mês" if intervalo == 1 else "a cada %d meses" % intervalo
        return "%s, no dia %d" % (base, inicio.day)
    base = "todo ano" if intervalo == 1 else "a cada %d anos" % intervalo
    return "%s, em %d de %s" % (base, inicio.day, MESES[inicio.month - 1])


def _ate_na_regra(ate: date, dia_todo: bool) -> str:
    if dia_todo:
        return ate.strftime("%Y%m%d")
    ultimo = datetime.combine(ate, time(23, 59, 59), tzinfo=fuso())
    return ultimo.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _descrever_fim(ate: date | None, vezes: int | None, hoje: date) -> str:
    if vezes:
        return ", %d vezes" % vezes
    if ate:
        return ", até " + data_falada(ate, com_ano=ate.year != hoje.year)
    return ", sem data para acabar"


def interpretar_repeticao(texto: str, inicio: date, dia_todo: bool = False) -> Repeticao:
    q = " ".join(re.sub(r"[,;.!?()]", " ", normalizar(texto)).split())  # "toda quinta, 10 vezes"
    if not q:
        raise RepeticaoInvalida(AJUDA)
    q, ate, vezes = _fim_do_texto(q, inicio)
    palavras = q.split()
    dias = _dias_no_texto(palavras)
    intervalo = 1
    cada = re.search(r"\ba cada (\d+|%s) (dias?|semanas?|mes|meses|anos?)\b" % "|".join(NUMEROS), q)
    if cada:
        intervalo = _numero(cada.group(1)) or 1
        unidade = cada.group(2)
        freq = {"d": "DAILY", "s": "WEEKLY", "m": "MONTHLY", "a": "YEARLY"}[unidade[0]]
    elif "dias uteis" in q or "dia util" in q or re.search(r"\bsegunda a sexta\b", q):
        freq, dias = "WEEKLY", [0, 1, 2, 3, 4]
    elif "fim de semana" in q or "fins de semana" in q:
        freq, dias = "WEEKLY", [5, 6]
    elif "quinzena" in q or "quinzenal" in q:
        freq, intervalo = "WEEKLY", 2
    elif dias:
        freq = "WEEKLY"
    elif re.search(r"\b(todo dia|todos os dias|diari\w*)\b", q):
        freq = "DAILY"
    elif re.search(r"\b(toda semana|todas as semanas|semana\w*)\b", q):
        freq = "WEEKLY"
    elif re.search(r"\b(todo mes|todos os meses|mensal\w*)\b", q):
        freq = "MONTHLY"
    elif re.search(r"\b(todo ano|todos os anos|anual\w*)\b", q):
        freq = "YEARLY"
    else:
        raise RepeticaoInvalida('Não entendi a repetição "%s". %s' % (texto.strip(), AJUDA))
    if not 1 <= intervalo <= 12:
        raise RepeticaoInvalida("O intervalo precisa ficar entre 1 e 12.")
    if freq == "WEEKLY" and not dias:
        dias = [inicio.weekday()]
    if freq != "WEEKLY":
        dias = []

    primeiro = inicio
    if dias:  # o Google conta o primeiro dia mesmo que ele não bata com a regra: começa no primeiro que bate
        while primeiro.weekday() not in dias:
            primeiro += timedelta(days=1)
    if ate and ate < primeiro:
        raise RepeticaoInvalida("A data final (%s) vem antes do primeiro dia (%s)." % (
            data_falada(ate), data_falada(primeiro)))

    partes = ["FREQ=" + freq]
    if intervalo > 1:
        partes.append("INTERVAL=%d" % intervalo)
    if dias:
        partes.append("BYDAY=" + ",".join(CODIGOS[d] for d in dias))
    if ate:
        partes.append("UNTIL=" + _ate_na_regra(ate, dia_todo))
    if vezes:
        partes.append("COUNT=%d" % vezes)
    descricao = _descrever(freq, intervalo, dias, primeiro) + _descrever_fim(ate, vezes, inicio)
    return Repeticao("RRULE:" + ";".join(partes), descricao, primeiro)


# ---------------------------------------------------------------- regras que já existem

def partes_da_regra(linhas: list[str]) -> dict[str, str]:
    """As partes da linha RRULE ({'FREQ': 'WEEKLY', 'BYDAY': 'TU'}); {} se não há."""
    for linha in linhas or []:
        if linha.upper().startswith("RRULE:"):
            return {k.upper(): v for k, _, v in (p.partition("=") for p in linha[6:].split(";")) if k}
    return {}


def _data_da_regra(valor: str) -> date | None:
    try:
        if "T" in valor:
            return datetime.strptime(valor, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(fuso()).date()
        return datetime.strptime(valor, "%Y%m%d").date()
    except ValueError:
        return None


def descrever_regra(linhas: list[str], inicio: date, hoje: date) -> str:
    """'às terças, até sexta-feira, 18 de dezembro'; 'que se repete' para o que não sei descrever."""
    partes = partes_da_regra(linhas)
    freq = partes.get("FREQ", "")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return "que se repete"
    dias = []
    for codigo in (partes.get("BYDAY") or "").split(","):
        if codigo in CODIGOS:
            dias.append(CODIGOS.index(codigo))
        elif codigo:  # "1MO" (a primeira segunda do mês) e outras formas: descreve só a frequência
            dias = []
            break
    try:
        intervalo = int(partes.get("INTERVAL") or 1)
    except ValueError:
        intervalo = 1
    ate = _data_da_regra(partes["UNTIL"]) if partes.get("UNTIL") else None
    vezes = int(partes["COUNT"]) if (partes.get("COUNT") or "").isdigit() else None
    return _descrever(freq, intervalo, sorted(dias), inicio) + _descrever_fim(ate, vezes, hoje)


def _trocar_fim(linhas: list[str], fim: str) -> list[str]:
    saida = []
    for linha in linhas or []:
        if linha.upper().startswith("RRULE:"):
            partes = [p for p in linha[6:].split(";") if p and p.split("=")[0].upper() not in ("UNTIL", "COUNT")]
            linha = "RRULE:" + ";".join(partes + [fim])
        saida.append(linha)
    return saida


def cortar_regra(linhas: list[str], ultimo: datetime | date, dia_todo: bool) -> list[str]:
    """A mesma regra, terminando em `ultimo` (o momento de antes da ocorrência onde a série é cortada)."""
    if dia_todo:
        dia = ultimo if isinstance(ultimo, date) and not isinstance(ultimo, datetime) else ultimo.date()
        return _trocar_fim(linhas, "UNTIL=" + dia.strftime("%Y%m%d"))
    return _trocar_fim(linhas, "UNTIL=" + ultimo.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))


def com_vezes(linhas: list[str], vezes: int) -> list[str]:
    return _trocar_fim(linhas, "COUNT=%d" % vezes)

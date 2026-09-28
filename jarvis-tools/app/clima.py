"""Ferramenta de clima: previsão do Open-Meteo (gratuito, sem chave) para a cidade padrão ou outra."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.periodos import proximo_sabado
from app.rede import ErroRede, obter_json
from app.textos import DIAS, data_falada, inteiro, normalizar

URL_BUSCA = "https://geocoding-api.open-meteo.com/v1/search"
URL_PREVISAO = "https://api.open-meteo.com/v1/forecast"
VALIDADE_PREVISAO = 600  # segundos; a previsão muda devagar
DIAS_DE_PREVISAO = 8  # no domingo, o fim de semana seguinte acaba no oitavo dia
DIAS_DA_SEMANA = 7

VARIAVEIS_AGORA = "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m"
VARIAVEIS_DIA = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,wind_speed_10m_max")

# Códigos de tempo da OMM usados pelo Open-Meteo
CODIGOS = {
    0: "céu limpo", 1: "céu quase limpo", 2: "parcialmente nublado", 3: "nublado",
    45: "neblina", 48: "neblina com geada",
    51: "garoa fraca", 53: "garoa", 55: "garoa forte", 56: "garoa congelante", 57: "garoa congelante forte",
    61: "chuva fraca", 63: "chuva moderada", 65: "chuva forte", 66: "chuva congelante", 67: "chuva congelante forte",
    71: "neve fraca", 73: "neve", 75: "neve forte", 77: "grãos de neve",
    80: "pancadas de chuva fracas", 81: "pancadas de chuva", 82: "pancadas de chuva fortes",
    85: "pancadas de neve", 86: "pancadas de neve fortes",
    95: "trovoadas", 96: "trovoadas com granizo", 99: "trovoadas com granizo forte",
}

UFS = {
    "ac": "acre", "al": "alagoas", "ap": "amapa", "am": "amazonas", "ba": "bahia", "ce": "ceara",
    "df": "distrito federal", "es": "espirito santo", "go": "goias", "ma": "maranhao", "mt": "mato grosso",
    "ms": "mato grosso do sul", "mg": "minas gerais", "pa": "para", "pb": "paraiba", "pr": "parana",
    "pe": "pernambuco", "pi": "piaui", "rj": "rio de janeiro", "rn": "rio grande do norte",
    "rs": "rio grande do sul", "ro": "rondonia", "rr": "roraima", "sc": "santa catarina", "sp": "sao paulo",
    "se": "sergipe", "to": "tocantins",
}

PERIODOS = (("manhã", 6, 12), ("tarde", 12, 18), ("noite", 18, 24))

AJUDA_QUANDO = ("Use hoje, agora, amanhã, depois de amanhã, um dia da semana (por exemplo sexta), "
                "fim de semana ou semana.")


@dataclass(frozen=True)
class Local:
    nome: str  # como falar: "Cornélio Procópio", "Londrina, Paraná", "Lisboa, Portugal"
    latitude: float
    longitude: float


_locais: dict[str, Local] = {}
_previsoes: dict[tuple, tuple[float, dict]] = {}


def cidade_padrao() -> str:
    return (os.environ.get("JARVIS_CIDADE") or "Cornélio Procópio, PR").strip()


# ---------------------------------------------------------------- cidade

def _combina(resultado: dict, qualificador: str) -> bool:
    if not qualificador:
        return True
    alvos = {normalizar(str(resultado.get(k) or "")) for k in ("admin1", "country", "country_code")}
    return qualificador in alvos or UFS.get(qualificador) in alvos


def escolher_resultado(resultados: list, qualificador: str = "") -> dict | None:
    """Escolhe o resultado da busca: cidades antes de aeroportos e bairros; no Brasil, a menos que
    uma cidade de fora seja muito maior (quem pede "Paris" quer a de lá)."""
    candidatos = [r for r in resultados if _combina(r, qualificador)]
    cidades = [r for r in candidatos if str(r.get("feature_code") or "").startswith("PPL")] or candidatos
    if not cidades:
        return None
    melhor = cidades[0]
    if qualificador or melhor.get("country_code") == "BR":
        return melhor
    brasil = next((r for r in cidades if r.get("country_code") == "BR"), None)
    if brasil and (brasil.get("population") or 0) * 20 >= (melhor.get("population") or 0):
        return brasil
    return melhor


def nome_falado(resultado: dict, curto: bool = False) -> str:
    nome = resultado.get("name") or "?"
    if curto:
        return nome
    regiao = resultado.get("admin1") if resultado.get("country_code") == "BR" else resultado.get("country")
    return "%s, %s" % (nome, regiao) if regiao and regiao != nome else nome


async def buscar_local(consulta: str) -> Local | None:
    chave = normalizar(consulta)
    if chave in _locais:
        return _locais[chave]
    nome, _, qualificador = consulta.partition(",")
    nome = nome.strip()
    if len(nome) < 2:
        return None
    dados = await obter_json(URL_BUSCA, {"name": nome, "count": 10, "language": "pt", "format": "json"})
    escolhido = escolher_resultado((dados or {}).get("results") or [], normalizar(qualificador))
    if escolhido is None:
        return None
    local = Local(nome_falado(escolhido, curto=chave == normalizar(cidade_padrao())),
                  float(escolhido["latitude"]), float(escolhido["longitude"]))
    _locais[chave] = local
    return local


async def obter_previsao(local: Local) -> dict:
    chave = (round(local.latitude, 3), round(local.longitude, 3))
    guardada = _previsoes.get(chave)
    if guardada and time.monotonic() - guardada[0] < VALIDADE_PREVISAO:
        return guardada[1]
    dados = await obter_json(URL_PREVISAO, {
        "latitude": local.latitude, "longitude": local.longitude, "current": VARIAVEIS_AGORA,
        "hourly": "precipitation_probability", "daily": VARIAVEIS_DIA, "timezone": "auto",
        "forecast_days": DIAS_DE_PREVISAO,
    })
    _previsoes[chave] = (time.monotonic(), dados)
    return dados


# ---------------------------------------------------------------- "quando"

_NOMES_DIAS = {normalizar(d.split("-")[0]): i for i, d in enumerate(DIAS)}


def interpretar_quando(quando: str, datas: list[date]) -> tuple[str, list[int]] | None:
    """Traduz o pedido em (modo, índices dos dias). Modos: agora, hoje, dia, semana. None = não entendi."""
    q = normalizar(quando)
    # "à tarde", "de noite", "hoje de manhã": só um período, sem outro dia = hoje
    palavras = [p for p in q.split() if p not in ("a", "de", "da", "na", "no", "pela", "esta", "nesta", "essa", "nessa")]
    if "hoje" in palavras and "amanha" in palavras:
        return "dia", [0, 1][:len(datas)]
    if q in ("", "hj") or q.startswith("hoje") or (palavras and set(palavras) <= {"manha", "tarde", "noite"}):
        return "hoje", [0]
    if q in ("agora", "atual", "neste momento", "nesse momento", "agora mesmo"):
        return "agora", [0]
    if "depois de amanha" in q:
        return "dia", [2] if len(datas) > 2 else []
    if "amanha" in q:
        return "dia", [1] if len(datas) > 1 else []
    if "fim de semana" in q or "final de semana" in q:
        sabado = proximo_sabado(datas[0])
        return "dia", [i for i, d in enumerate(datas) if d in (sabado, sabado + timedelta(days=1))]
    if "semana" in q or "proximos dias" in q or "7 dias" in q or "sete dias" in q:
        return "semana", list(range(min(len(datas), DIAS_DA_SEMANA)))
    for palavra in q.split():
        if palavra in _NOMES_DIAS:
            alvo = _NOMES_DIAS[palavra]
            return "dia", [i for i, d in enumerate(datas) if d.weekday() == alvo][:1]
    iso = re.search(r"(\d{4})[- ](\d{1,2})[- ](\d{1,2})", q)  # normalizar troca "-" por espaço
    barra = re.search(r"\b(\d{1,2})/(\d{1,2})\b", q)
    dia_solto = re.search(r"\bdia (\d{1,2})\b", q)
    for d_i, d in enumerate(datas):
        if (iso and (d.year, d.month, d.day) == tuple(int(x) for x in iso.groups())) \
                or (barra and (d.day, d.month) == (int(barra.group(1)), int(barra.group(2)))) \
                or (dia_solto and d.day == int(dia_solto.group(1))):
            return "dia", [d_i]
    if iso or barra or dia_solto:
        return "dia", []  # data válida, mas fora dos 7 dias
    return None


# ---------------------------------------------------------------- textos

def _valor(dados: dict, bloco: str, campo: str, indice: int):
    lista = (dados.get(bloco) or {}).get(campo) or []
    return lista[indice] if indice < len(lista) else None


def descricao(codigo) -> str:
    return CODIGOS.get(int(codigo), "tempo variável") if codigo is not None else "tempo sem descrição"


def chuva_por_periodo(dados: dict, dia: date, a_partir_da_hora: int = 0) -> list[tuple[str, int]]:
    horarios = (dados.get("hourly") or {}).get("time") or []
    chances = (dados.get("hourly") or {}).get("precipitation_probability") or []
    por_hora = {}
    for texto, chance in zip(horarios, chances):
        if texto[:10] == dia.isoformat() and chance is not None:
            por_hora[int(texto[11:13])] = chance
    periodos = []
    for nome, inicio, fim in PERIODOS:
        if fim <= a_partir_da_hora:
            continue
        valores = [c for h, c in por_hora.items() if max(inicio, a_partir_da_hora) <= h < fim]
        if valores:
            periodos.append((nome, inteiro(max(valores))))
    return periodos


def frase_chuva(chance, milimetros, periodos: list[tuple[str, int]]) -> str:
    if chance is None:
        return ""
    chance = inteiro(chance)
    if chance < 20:
        return "Chance de chuva baixa, %d%%." % chance
    texto = "Chance de chuva de %d%%" % chance
    if milimetros is not None and milimetros >= 1:
        texto += ", cerca de %d milímetros" % inteiro(milimetros)
    if len(periodos) > 1:
        texto += "; por período: " + ", ".join("%s %d%%" % p for p in periodos)
    return texto + "."


def rotulo_dia(indice: int, dia: date) -> str:
    if indice == 0:
        return "hoje, " + data_falada(dia)
    if indice == 1:
        return "amanhã, " + data_falada(dia)
    return data_falada(dia)


def resumo_dia(dados: dict, indice: int, dia: date, a_partir_da_hora: int = 0) -> str:
    maxima = inteiro(_valor(dados, "daily", "temperature_2m_max", indice))
    minima = inteiro(_valor(dados, "daily", "temperature_2m_min", indice))
    partes = ["%s: %s" % (rotulo_dia(indice, dia), descricao(_valor(dados, "daily", "weather_code", indice)))]
    if maxima is not None and minima is not None:
        partes[0] += ", máxima de %d e mínima de %d graus." % (maxima, minima)
    else:
        partes[0] += "."
    chuva = frase_chuva(_valor(dados, "daily", "precipitation_probability_max", indice),
                        _valor(dados, "daily", "precipitation_sum", indice),
                        chuva_por_periodo(dados, dia, a_partir_da_hora))
    if chuva:
        partes.append(chuva)
    vento = inteiro(_valor(dados, "daily", "wind_speed_10m_max", indice))
    if vento is not None and vento >= 30:
        partes.append("Ventos de até %d quilômetros por hora." % vento)
    return " ".join(partes)


def frase_agora(dados: dict) -> str:
    atual = dados.get("current") or {}
    temperatura, sensacao = inteiro(atual.get("temperature_2m")), inteiro(atual.get("apparent_temperature"))
    texto = "agora faz %s graus" % ("?" if temperatura is None else temperatura)
    if sensacao is not None and sensacao != temperatura:
        texto += ", com sensação de %d" % sensacao
    texto += ", %s." % descricao(atual.get("weather_code"))
    umidade = inteiro(atual.get("relative_humidity_2m"))
    if umidade is not None and umidade < 30:
        texto += " Umidade do ar baixa, %d%%." % umidade
    return texto


def curto_dia(indice: int, dia: date) -> str:
    return "hoje" if indice == 0 else DIAS[dia.weekday()].split("-")[0]


def montar_texto(local: Local, dados: dict, quando: str) -> str:
    datas = [date.fromisoformat(t) for t in (dados.get("daily") or {}).get("time") or []]
    if not datas:
        return "A previsão veio vazia. Tente de novo em alguns minutos."
    interpretado = interpretar_quando(quando, datas)
    if interpretado is None:
        return "Não entendi o dia pedido (%s). %s" % (quando, AJUDA_QUANDO)
    modo, indices = interpretado
    if not indices:
        return "Só tenho previsão para os próximos %d dias." % len(datas)
    momento = (dados.get("current") or {}).get("time")
    hora_atual = datetime.fromisoformat(momento).hour if momento else 0

    if modo == "agora":
        texto = "Em %s, %s" % (local.nome, frase_agora(dados))
        maxima = inteiro(_valor(dados, "daily", "temperature_2m_max", 0))
        minima = inteiro(_valor(dados, "daily", "temperature_2m_min", 0))
        if maxima is not None and minima is not None:
            texto += " Hoje a máxima é de %d e a mínima de %d graus." % (maxima, minima)
        proximas = [c for t, c in zip((dados.get("hourly") or {}).get("time") or [],
                                      (dados.get("hourly") or {}).get("precipitation_probability") or [])
                    if c is not None and momento and momento[:13] <= t[:13] < _somar_horas(momento, 6)]
        if proximas:
            texto += " Chance de chuva nas próximas 6 horas: %d%%." % inteiro(max(proximas))
        return texto
    if modo == "hoje":
        return "Em %s, %s %s" % (local.nome, frase_agora(dados), _maiuscula(resumo_dia(dados, 0, datas[0], hora_atual)))
    if modo == "semana":
        linhas = []
        for i in indices:
            maxima = inteiro(_valor(dados, "daily", "temperature_2m_max", i))
            minima = inteiro(_valor(dados, "daily", "temperature_2m_min", i))
            chance = _valor(dados, "daily", "precipitation_probability_max", i)
            linha = "%s (%d): %s" % (curto_dia(i, datas[i]), datas[i].day,
                                     descricao(_valor(dados, "daily", "weather_code", i)))
            if maxima is not None and minima is not None:
                linha += ", %d a %d graus" % (minima, maxima)
            if chance is not None:
                linha += ", chuva %d%%" % inteiro(chance)
            linhas.append(linha + ".")
        return "Previsão para %s nos próximos dias: %s" % (local.nome, " ".join(linhas))
    resumos = [resumo_dia(dados, i, datas[i], hora_atual if i == 0 else 0) for i in indices]
    return "Em %s, %s" % (local.nome, " ".join([resumos[0]] + [_maiuscula(r) for r in resumos[1:]]))


def _maiuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def _somar_horas(momento_iso: str, horas: int) -> str:
    """'2026-09-24T19:00' + 6 h, no mesmo formato (só até a hora), para comparar como texto."""
    return (datetime.fromisoformat(momento_iso[:13] + ":00") + timedelta(hours=horas)).isoformat()[:13]


# ---------------------------------------------------------------- ferramenta

async def previsao(cidade: str = "", quando: str = "hoje") -> str:
    consulta = (cidade or "").strip() or cidade_padrao()
    try:
        local = await buscar_local(consulta)
        if local is None:
            return ("Não encontrei a cidade %s. Tente com o estado ou o país, por exemplo \"Londrina, PR\" "
                    "ou \"Lisboa, Portugal\"." % consulta)
        dados = await obter_previsao(local)
    except ErroRede as erro:
        return "Não consegui consultar a previsão agora: %s." % erro
    return montar_texto(local, dados, quando or "hoje")

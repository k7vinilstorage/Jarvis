"""Textos: limpeza da resposta para a fala (markdown, números, horas), divisão em frases e transcrição do Whisper."""
from __future__ import annotations

import re
import unicodedata

LIMITE_FALA = 600        # caracteres; a resposta é cortada no fim de uma frase perto disso
LIMITE_CABECALHO = 300   # caracteres em X-Jarvis-Pergunta e X-Jarvis-Resposta, antes de codificar

NAO_OUVI = "Não ouvi nada. Tente de novo."
FALHA_WHISPER = "Não consegui entender o áudio agora. Tente de novo."
FALHA_HERMES = "Não consegui pensar numa resposta agora. Tente de novo daqui a pouco."
UM_MOMENTO = "Um momento."
RESPOSTA_VAZIA = "Não tenho uma resposta para isso."

# ---------------------------------------------------------------- resposta do Hermes -> fala

_PENSAMENTO = re.compile(r"<think>.*?(?:</think>|$)", re.S | re.I)
_CERCA = re.compile(r"```[^\n]*")                     # ```python ... ```: fica só o conteúdo
_IMAGEM = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")               # <br>, <https://...>
_REGUA = re.compile(r"^\s*([-*_=])(?:\s*\1){2,}\s*$", re.M)
_TITULO = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_CITACAO = re.compile(r"^\s{0,3}>\s?", re.M)
_ITEM = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+", re.M)
_ENFASE = re.compile(r"\*+|~~|`+|(?<!\w)_+|_+(?!\w)")
_EMOJIS = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # emojis, bandeiras e pictogramas
    "\U00002600-\U000027BF"   # símbolos diversos e dingbats (☀ ✓ ✨)
    "\U00002B00-\U00002BFF"   # setas e estrelas (⭐)
    "\U00002190-\U000021FF"   # setas
    "\U00002300-\U000023FF"   # símbolos técnicos (⌚ ⏰)
    "\U0000FE00-\U0000FE0F"   # seletores de variação
    "\U0000200D"              # junção de emojis compostos
    "\U000E0020-\U000E007F"   # marcas de bandeiras regionais
    "\U00003030\U0000303D\U00003297\U00003299"
    "]+")
_FIM_DE_FRASE = re.compile(r"[.!?…](?=\s|$)")
_ESPACO_ANTES = re.compile(r"\s+([.,!?;:…])")
_PARENTESES_VAZIOS = re.compile(r"\(\s*\)")


def limpar_para_fala(texto: str, limite: int = LIMITE_FALA) -> str:
    """Tira markdown, links, URLs e emojis, junta os espaços e corta num fim de frase perto do limite."""
    t = unicodedata.normalize("NFC", texto or "")
    t = _PENSAMENTO.sub(" ", t)
    t = _CERCA.sub("\n", t)
    t = _IMAGEM.sub(r"\1", t)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub(" ", t)
    t = _TAG.sub(" ", t)
    t = t.replace("|", " ")  # tabelas
    for padrao in (_REGUA, _TITULO, _CITACAO, _ITEM):
        t = padrao.sub("", t)
    t = _ENFASE.sub("", t)
    t = _EMOJIS.sub(" ", t)
    # Cada linha (item de lista, título) vira uma frase, para a voz fazer a pausa
    linhas = [" ".join(linha.split()) for linha in t.splitlines()]
    linhas = [linha for linha in linhas if re.search(r"\w", linha)]
    if len(linhas) > 1:
        linhas = [linha if linha[-1] in ".!?…:;," else linha + "." for linha in linhas]
    t = _ESPACO_ANTES.sub(r"\1", " ".join(linhas))
    t = _PARENTESES_VAZIOS.sub("", t)
    return cortar(" ".join(t.split()), limite)


def cortar(texto: str, limite: int = LIMITE_FALA) -> str:
    if len(texto) <= limite:
        return texto
    trecho = texto[:limite + 1]
    fins = [m.end() for m in _FIM_DE_FRASE.finditer(trecho)]
    if fins and fins[-1] >= limite * 0.4:
        return trecho[:fins[-1]].strip()
    # Nenhuma frase termina a tempo: corta na última palavra inteira
    espaco = trecho.rfind(" ", 0, limite)
    return trecho[:espaco if espaco > 0 else limite].rstrip(" ,;:-") + "."


# ---------------------------------------------------------------- transcrição do Whisper

_ANOTACOES = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")  # [BLANK_AUDIO], (música), *risos*
# Frases que o Whisper inventa em trechos sem fala (vêm das legendas com que foi treinado)
_ALUCINACOES = {"obrigado por assistir", "obrigada por assistir", "inscreva se no canal",
                "legendas pela comunidade amara org"}


def _comparavel(texto: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", texto.lower()).split())


def limpar_transcricao(texto: str) -> str:
    """Texto da pergunta, ou vazio quando o Whisper não ouviu fala de verdade."""
    t = " ".join(_ANOTACOES.sub(" ", unicodedata.normalize("NFC", texto or "")).split())
    if not re.search(r"\w", t):
        return ""
    comparavel = _comparavel(t)
    if comparavel in _ALUCINACOES or "amara org" in comparavel:
        return ""
    return t


# ---------------------------------------------------------------- números, horas e símbolos -> fala
# O Piper (espeak-ng) já lê números soltos em português ("23" -> "vinte e três"); o que ele lê mal são os
# formatos compostos: "23h59" sairia "vinte e três agá cinquenta e nove".

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
_ORDINAIS = {"1": "primeir", "2": "segund", "3": "terceir", "4": "quart", "5": "quint", "6": "sext",
             "7": "sétim", "8": "oitav", "9": "non", "10": "décim"}
_ABREVIACOES = [
    (re.compile(r"\bProfa\.(?=\s)"), "Professora"), (re.compile(r"\bProf\.(?=\s)"), "Professor"),
    (re.compile(r"\bDra\.(?=\s)"), "Doutora"), (re.compile(r"\bDr\.(?=\s)"), "Doutor"),
    (re.compile(r"\bSra\.(?=\s)"), "Senhora"), (re.compile(r"\bSr\.(?=\s)"), "Senhor"),
    (re.compile(r"\betc\.", re.I), "etcétera"), (re.compile(r"\bn[º°]\s?(?=\d)", re.I), "número "),
    (re.compile(r"\bkm/h\b", re.I), "quilômetros por hora"), (re.compile(r"(?<=\d)\s?km\b"), " quilômetros"),
    (re.compile(r"(?<=\d)\s?mm\b"), " milímetros"),
]


def _hora_e_minuto(m) -> str:
    hora, minuto = int(m.group(1)), int(m.group(2))
    if minuto == 0:
        return _so_hora(hora)
    return "%d e %d" % (hora, minuto)


def _so_hora(hora: int) -> str:
    if hora == 0:
        return "meia-noite"
    if hora == 12:
        return "meio-dia"
    return "%d %s" % (hora, "hora" if hora == 1 else "horas")


def _data(m) -> str:
    dia, mes = int(m.group(1)), int(m.group(2))
    if not (1 <= dia <= 31 and 1 <= mes <= 12) or (len(m.group(1)) == 1 and not m.group(3)):
        return m.group(0)  # "8/10" sem ano é mais provável uma nota que 8 de outubro
    texto = "%s de %s" % ("primeiro" if dia == 1 else dia, MESES[mes - 1])
    return texto + (" de %s" % m.group(3) if m.group(3) else "")


def _reais(m) -> str:
    inteiro = m.group(1).replace(".", "")
    centavos = int(m.group(2) or 0)
    texto = "%s %s" % (inteiro, "real" if inteiro == "1" else "reais")
    return texto + (" e %d centavos" % centavos if centavos else "")


def numeros_para_fala(t: str) -> str:
    """Formatos que a voz lê mal viram o jeito falado: 23h59 -> 23 e 59; 15h -> 15 horas; 25/09 -> 25 de
    setembro; 2% -> 2 por cento; 31°C -> 31 graus; R$ 120,50 -> 120 reais e 50 centavos; 1º -> primeiro."""
    t = re.sub(r"\b([àÀ])s 0h\b", r"\1 meia-noite", t)
    t = re.sub(r"\b[àÀ]s 12h\b", "ao meio-dia", t)
    t = re.sub(r"\b(\d{1,2})h(\d{2})(?:min)?\b", _hora_e_minuto, t)
    t = re.sub(r"\b(\d{1,2})\s?h\b", lambda m: _so_hora(int(m.group(1))), t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})(?!:?\d)", _hora_e_minuto, t)
    # Data: mês com 2 dígitos ("3/4" é fração) e sem número colado antes ("7,5/10" é nota)
    t = re.sub(r"(?<![\d,.])\b(\d{1,2})/(\d{2})(?:/(\d{4}))?\b", _data, t)
    t = re.sub(r"R\$\s?(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{2}))?", _reais, t)
    t = re.sub(r"(\d+(?:,\d+)?)\s?%", r"\1 por cento", t)
    t = re.sub(r"(\d+(?:,\d+)?)\s?[°º]\s?C\b", r"\1 graus", t)  # 31°C e 31ºC (º no lugar de °)
    t = re.sub(r"\b(\d{1,2})([ºª])", lambda m: (_ORDINAIS[m.group(1)] + ("o" if m.group(2) == "º" else "a"))
               if m.group(1) in _ORDINAIS else m.group(0), t)
    # "1° semestre": ° no lugar de º antes de uma palavra é ordinal; o resto é grau
    t = re.sub(r"\b(\d{1,2})°(?=\s+[a-zà-ú])", lambda m: _ORDINAIS[m.group(1)] + "o"
               if m.group(1) in _ORDINAIS else m.group(0), t)
    t = re.sub(r"(\d+(?:,\d+)?)\s?°", r"\1 graus", t)
    t = re.sub(r"\b(\d{1,3})((?:\.\d{3})+)(?![.,]?\d)", lambda m: m.group(1) + m.group(2).replace(".", ""), t)
    for padrao, troca in _ABREVIACOES:
        t = padrao.sub(troca, t)
    return t


def para_fala(texto: str, limite: int = LIMITE_FALA) -> str:
    """Texto pronto para o Piper: sem markdown, links e emojis, e com números e horas como se fala."""
    return numeros_para_fala(limpar_para_fala(texto, limite))


# ---------------------------------------------------------------- frases, enquanto a resposta chega

# Ponto depois destas palavras não termina a frase ("Prof. Ana", "Sr. Carlos")
_NAO_TERMINA = {"prof", "profa", "dr", "dra", "sr", "sra", "srta", "etc", "ex", "obs", "p", "pág", "n", "nº",
                "av", "tel", "min", "máx", "aprox", "vs"}
_FIM = re.compile(r"([.!?…]+)[\"')\]]*(\s+)|\n+")


class DivisorDeFrases:
    """Recebe o texto em pedaços (streaming) e devolve frases completas assim que terminam.

    A primeira sai sozinha, para a voz começar o quanto antes; depois as frases curtas são juntadas até
    `minimo` caracteres, para o Piper falar com uma entonação mais natural e fazer menos pedidos."""

    def __init__(self, minimo: int = 60):
        self.minimo = minimo
        self.buffer = ""
        self.acumulado = ""
        self.primeira = True

    def _proxima_frase(self) -> str | None:
        for m in _FIM.finditer(self.buffer):
            if m.group(1):
                antes = re.findall(r"(\w+)$", self.buffer[:m.start(1)])
                if antes and antes[0].lower() in _NAO_TERMINA and m.group(1) == ".":
                    continue
                linha = self.buffer[:m.start(1)].rsplit("\n", 1)[-1].strip()
                if m.group(1) == "." and linha.isdigit() and len(linha) <= 3:
                    continue  # "1. item" de uma lista numerada
            frase, self.buffer = self.buffer[:m.end()], self.buffer[m.end():]
            return frase
        return None

    def adicionar(self, pedaco: str) -> list[str]:
        self.buffer += pedaco or ""
        prontas = []
        while True:
            frase = self._proxima_frase()
            if frase is None:
                break
            if not re.search(r"\w", frase):
                continue
            self.acumulado += frase
            if self.primeira or len(self.acumulado) >= self.minimo:
                prontas.append(self.acumulado.strip())
                self.acumulado, self.primeira = "", False
        # Resposta sem pontuação nenhuma: não deixa a voz esperando para sempre
        if len(self.buffer) > 400:
            corte = self.buffer.rfind(" ", 0, 300)
            corte = corte if corte > 0 else 300
            self.acumulado += self.buffer[:corte]
            self.buffer = self.buffer[corte:]
            prontas.append(self.acumulado.strip())
            self.acumulado, self.primeira = "", False
        return [p for p in prontas if p]

    def terminar(self) -> list[str]:
        resto = (self.acumulado + self.buffer).strip()
        self.acumulado = self.buffer = ""
        return [resto] if re.search(r"\w", resto) else []


# ---------------------------------------------------------------- log

def resumo(texto: str, limite: int = 80) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= limite else t[:limite - 1] + "…"

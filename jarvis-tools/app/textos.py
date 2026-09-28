"""Datas, horas e números escritos como se fala em português."""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro"]


def fuso() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ") or "America/Sao_Paulo")


def agora() -> datetime:
    return datetime.now(fuso())


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos e sem espaços sobrando: para comparar nomes."""
    sem = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")
    return " ".join(sem.lower().replace("-", " ").split())


def dia_do_mes(d: date) -> str:
    """'1º' no primeiro dia, como se fala; o resto é o número."""
    return "1º" if d.day == 1 else str(d.day)


def data_falada(d: date, com_ano: bool = False) -> str:
    """'sexta-feira, 25 de setembro' (e o ano, se pedido)."""
    texto = "%s, %s de %s" % (DIAS[d.weekday()], dia_do_mes(d), MESES[d.month - 1])
    return texto + (" de %d" % d.year if com_ano else "")


def hora_falada(momento: datetime) -> str:
    """'15h30', ou '15h' na hora cheia."""
    return "%dh%02d" % (momento.hour, momento.minute) if momento.minute else "%dh" % momento.hour


def _hora_com(momento: datetime, plural: str, singular: str, masculino: str) -> str:
    if (momento.hour, momento.minute) == (0, 0):
        return singular + " meia-noite"
    if (momento.hour, momento.minute) == (12, 0):
        return masculino + " meio-dia"
    return (singular if momento.hour in (0, 1) else plural) + " " + hora_falada(momento)


def as_hora(momento: datetime) -> str:
    """'às 15h', 'à 1h', 'à meia-noite', 'ao meio-dia'."""
    return _hora_com(momento, "às", "à", "ao")


def das_hora(momento: datetime) -> str:
    """'das 15h', 'da 1h', 'da meia-noite', 'do meio-dia'."""
    return _hora_com(momento, "das", "da", "do")


def faixa_horas(inicio: datetime, fim: datetime) -> str:
    """'das 9h às 10h', 'da 1h às 2h', 'do meio-dia à 1h'... ou só 'às 9h' quando não há duração."""
    if fim <= inicio:
        return as_hora(inicio)
    return "%s %s" % (das_hora(inicio), as_hora(fim))


def juntar_com_e(itens: list[str]) -> str:
    """'A', 'A e B', 'A, B e C'."""
    itens = [i for i in itens if i]
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1] if itens else ""


def dia_curto(d: date, hoje: date) -> str:
    """'hoje', 'amanhã', 'sexta' (até 6 dias à frente) ou 'segunda, 5 de outubro'."""
    diferenca = (d - hoje).days
    if diferenca == 0:
        return "hoje"
    if diferenca == 1:
        return "amanhã"
    if diferenca == -1:
        return "ontem"
    nome = DIAS[d.weekday()].split("-")[0]
    if 1 < diferenca < 7:
        return nome
    texto = "%s, %s de %s" % (nome, dia_do_mes(d), MESES[d.month - 1])
    return texto + (" de %d" % d.year if d.year != hoje.year else "")


def maiuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def cortar(texto: str, limite: int) -> str:
    """Junta os espaços e corta numa palavra inteira, com reticências."""
    texto = " ".join((texto or "").split())
    if len(texto) <= limite:
        return texto
    corte = texto[:limite].rsplit(" ", 1)[0].rstrip(" ,;:.-–—")
    return (corte or texto[:limite]) + "…"


def como_responder(n: int) -> str:
    """Última linha das listas: o modelo pequeno segue melhor o que leu por último, e tudo pode virar voz."""
    if n > 3:
        return ("Ao responder: em frases corridas, sem lista, diga que são %d e cite só os 3 primeiros; os outros, só "
                "se o usuário pedir." % n)
    return "Ao responder: em frases corridas, sem lista."


RESUMIR = "Ao responder: em poucas frases corridas, sem lista nem passo a passo, só o que responde à pergunta."


def contagem(n: int, singular: str, plural: str) -> str:
    """'1 atividade', '3 atividades'."""
    return "%d %s" % (n, singular if n == 1 else plural)


def inteiro(valor) -> int | None:
    return None if valor is None else int(round(float(valor)))


# ---------------------------------------------------------------- HTML e palavras-chave

class _SemTags(HTMLParser):
    IGNORAR = {"script", "style", "noscript", "template", "svg", "head", "title"}
    BLOCOS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section",
              "article", "blockquote", "pre", "dd", "dt", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignorando = 0
        self.partes: list[str] = []

    def handle_starttag(self, tag, atributos):
        if tag == "body":  # <head> sem </head> é HTML5 válido: o corpo começa aqui de qualquer jeito
            self.ignorando = 0
        elif tag in self.IGNORAR:
            self.ignorando += 1
        elif tag in self.BLOCOS:
            self.partes.append("\n")
        elif tag in ("td", "th"):
            self.partes.append(" ")

    def handle_startendtag(self, tag, atributos):
        if tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORAR and self.ignorando:
            self.ignorando -= 1
        elif tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_data(self, dados):
        if not self.ignorando:
            self.partes.append(dados)


def html_para_texto(documento: str) -> str:
    """Texto simples de um trecho HTML (descrições do Moodle, corpo de e-mail): sem tags nem entidades,
    um parágrafo por linha, sem linhas vazias."""
    documento = str(documento or "")
    if "<" not in documento and "&" not in documento:
        bruto = documento
    else:
        extrator = _SemTags()
        try:
            extrator.feed(documento)
            extrator.close()
        except Exception:  # HTML quebrado: fica com o que deu para ler
            pass
        bruto = "".join(extrator.partes)
    linhas = (" ".join(linha.replace(" ", " ").split()) for linha in bruto.split("\n"))
    return "\n".join(linha for linha in linhas if linha)


# Palavras que não ajudam a achar nada (português e inglês, já sem acento)
PALAVRAS_VAZIAS = set("""
a o as os um uma uns umas de da do das dos em na no nas nos para pra pro por pelo pela pelos pelas com sem
e ou que se ao aos à às é ser sao são era foi como mais menos muito muita sobre entre ate até ja já
me mim meu minha meus minhas seu sua seus suas voce você voces vocês ele ela eles elas isso isto esse essa
este esta qual quais quando onde porque qual tem ter há ha la lá aqui ai aí
the of and or to in on for with by at from is are was be an as it this that what which who how
""".split())


def palavras_chave(texto: str) -> list[str]:
    """Palavras que importam para comparar textos: minúsculas, sem acento, sem as palavras vazias.
    Números ficam (semana 6, TCC 1), inclusive grudados em letras ('p2', '26.04')."""
    tokens = re.findall(r"[a-z0-9]+(?:[.][0-9]+)*", normalizar(texto))
    return [t for t in tokens if t not in PALAVRAS_VAZIAS and (len(t) > 1 or t.isdigit())]


def mesma_palavra(a: str, b: str) -> bool:
    """Compara com tolerância a plural e flexão: 'provas' ~ 'prova', 'distribuidos' ~ 'distribuido'.
    Números só batem com o mesmo número."""
    if a == b:
        return True
    if a.isdigit() or b.isdigit() or min(len(a), len(b)) < 4:
        return False
    tamanho = max(4, min(len(a), len(b)) - 2)
    return a[:tamanho] == b[:tamanho]

"""Peças comuns para conversar com o Jarvis pela API do Hermes (usadas por testar-jarvis.py e chat.py).

Só usa a biblioteca padrão do Python (3.8 ou mais novo). A chave da API é lida do .env e nunca aparece
na tela, no relatório nem nas mensagens de erro.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PASTA_SCRIPTS = Path(__file__).resolve().parent
RAIZ = PASTA_SCRIPTS.parent
SEM_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
URL_HERMES = "http://127.0.0.1:8642"
MODELO = "hermes-agent"
CMD_RECURSOS = ["docker", "compose", "exec", "-T", "jarvis-tools", "python", "-m", "app.cli", "recursos"]


# ---------------------------------------------------------------- utilidades

def log(msg: str = "") -> None:
    print(msg, flush=True)


def num(x, casas: int = 1) -> str:
    """Número no formato brasileiro: 12,3."""
    if x is None:
        return "?"
    return (("%." + str(casas) + "f") % x).replace(".", ",") if casas else "%d" % round(x)


def seg(x) -> str:
    return "?" if x is None else num(x, 1) + " s"


def normalizar(s) -> str:
    """Minúsculas e sem acentos, para comparar textos ("Não" e "nao" ficam iguais)."""
    s = unicodedata.normalize("NFD", str(s or "")).replace(" ", " ")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def sem_acento_regex(padrao: str) -> str:
    """Tira os acentos de um padrão de regex sem mexer em maiúsculas (\\S e \\s são coisas diferentes)."""
    return "".join(c for c in unicodedata.normalize("NFD", padrao) if unicodedata.category(c) != "Mn")


def sem_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)


def ler_env(caminho) -> dict:
    """Lê um .env simples: aceita export, aspas e comentário no fim da linha."""
    valores = {}
    try:
        linhas = Path(caminho).read_text(encoding="utf-8").splitlines()
    except OSError:
        return valores
    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave = chave.strip()
        if chave.startswith("export "):
            chave = chave[7:].strip()
        valor = valor.strip()
        if valor[:1] in ("'", '"') and valor.find(valor[0], 1) > 0:
            valor = valor[1:valor.find(valor[0], 1)]
        else:
            valor = re.split(r"\s+#", valor, maxsplit=1)[0]
        valores[chave] = valor
    return valores


def chave_api(env: dict) -> str:
    return (os.environ.get("HERMES_API_KEY") or env.get("HERMES_API_KEY") or "").strip()


def limpar(texto, chave: str) -> str:
    """Garante que a chave nunca vaze numa mensagem de erro ou num relatório."""
    texto = str(texto)
    if chave and len(chave) >= 8:
        texto = texto.replace(chave, "***")
    return texto


def agora_local(fuso: str = "America/Sao_Paulo") -> datetime:
    try:
        from zoneinfo import ZoneInfo  # novermin (Python 3.9+; no 3.8 cai no fuso fixo abaixo)
        return datetime.now(ZoneInfo(fuso))
    except Exception:
        if fuso == "America/Sao_Paulo":
            return datetime.now(timezone(timedelta(hours=-3)))
        return datetime.now()


class Estilo:
    """Cores ANSI só quando a saída é um terminal (e sem NO_COLOR)."""

    def __init__(self, fluxo=None):
        fluxo = fluxo or sys.stdout
        self.ativo = bool(getattr(fluxo, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")

    def _c(self, codigo: str, texto: str) -> str:
        return "\x1b[%sm%s\x1b[0m" % (codigo, texto) if self.ativo else texto

    def fraco(self, t: str) -> str:
        return self._c("2", t)

    def negrito(self, t: str) -> str:
        return self._c("1", t)

    def verde(self, t: str) -> str:
        return self._c("32", t)

    def vermelho(self, t: str) -> str:
        return self._c("31", t)

    def amarelo(self, t: str) -> str:
        return self._c("33", t)

    def ciano(self, t: str) -> str:
        return self._c("36", t)


# ---------------------------------------------------------------- HTTP

def descrever_erro(e: BaseException) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            corpo = e.read().decode("utf-8", "replace").strip()[:300]
        except Exception:
            corpo = ""
        return ("HTTP %d %s" % (e.code, corpo)).strip()
    if isinstance(e, urllib.error.URLError):
        if isinstance(e.reason, socket.timeout):
            return "tempo esgotado esperando o Hermes"
        return "sem conexão (%s)" % (e.reason,)
    if isinstance(e, socket.timeout):
        return "tempo esgotado esperando o Hermes"
    return "%s: %s" % (type(e).__name__, e)


def http_json(url: str, corpo=None, cabecalhos=None, timeout: float = 30):
    dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(url, data=dados, method="GET" if dados is None else "POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (cabecalhos or {}).items():
        req.add_header(k, v)
    with SEM_PROXY.open(req, timeout=timeout) as r:
        bruto = r.read().decode("utf-8", "replace")
    return json.loads(bruto) if bruto.strip() else None


def verificar_hermes(url: str, chave: str, timeout: float = 10):
    """Confere o /health e a chave (/v1/models). Devolve None se está tudo certo ou a mensagem do problema."""
    url = url.rstrip("/")
    if not chave:
        return ("A HERMES_API_KEY está vazia ou não existe no .env. Rode o script de dentro da pasta do "
                "projeto (cd /opt/jarvis) e confira o .env.")
    try:
        http_json(url + "/health", timeout=timeout)
    except Exception as e:
        return limpar("O Hermes não respondeu em %s (%s). Ele está rodando? Confira com: docker compose ps"
                      % (url, descrever_erro(e)), chave)
    try:
        http_json(url + "/v1/models", cabecalhos={"Authorization": "Bearer " + chave}, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ("O Hermes recusou a HERMES_API_KEY do .env (HTTP %d). Confira se o .env é o mesmo que o "
                    "Hermes usa e reinicie com: docker compose up -d hermes" % e.code)
        return limpar("O Hermes respondeu com erro em /v1/models (%s)." % descrever_erro(e), chave)
    except Exception as e:
        return limpar("O Hermes não respondeu em /v1/models (%s)." % descrever_erro(e), chave)
    return None


# ---------------------------------------------------------------- ferramentas

def nome_ferramenta(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    for k in ("tool", "name", "tool_name", "label", "title"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    for k in ("data", "payload"):
        if isinstance(obj.get(k), dict):
            return nome_ferramenta(obj[k])
    return ""


def nome_curto(nome: str) -> str:
    """mcp__jarvis__clima -> clima."""
    return nome.rsplit("__", 1)[-1] if "__" in nome else nome


def ferramenta_bate(usada: str, esperada: str) -> bool:
    """Compara pelo fim do nome: 'mcp__jarvis__clima' e 'clima' batem com 'clima'; 'agenda_criar' não bate com 'agenda'."""
    u, e = normalizar(usada).strip(), normalizar(esperada).strip()
    if not e:
        return False
    return u == e or (u.endswith(e) and not u[-len(e) - 1].isalnum())


STATUS_FIM = ("completed", "complete", "done", "finished", "success", "succeeded", "failed", "error", "end")


# ---------------------------------------------------------------- conversa

def conversar(url: str, chave: str, mensagens: list, timeout: float = 180.0,
              ao_texto=None, ao_ferramenta=None, com_uso: bool = True) -> dict:
    """Manda o histórico inteiro (a API sem cabeçalho de sessão não guarda nada) e lê a resposta em streaming.

    ao_texto(pedaço) e ao_ferramenta(nome) são chamados enquanto a resposta chega.
    Ctrl+C durante a resposta corta a conexão e devolve o que chegou, com cancelado=True.
    """
    corpo = {"model": MODELO, "messages": list(mensagens), "stream": True}
    if com_uso:
        corpo["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(corpo).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    req.add_header("Authorization", "Bearer " + chave)

    r = {"texto": "", "ferramentas": [], "raciocinio_chars": 0, "uso": None, "eventos": {},
         "t_primeiro_byte": None, "t_primeiro_token": None, "t_primeira_palavra": None, "t_total": None,
         "erro": None, "cancelado": False, "completo": False, "fim": None}
    partes, sobra, evento, chamadas = [], [], None, set()
    inicio = time.monotonic()
    resp = None
    try:
        try:
            resp = SEM_PROXY.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if com_uso and e.code in (400, 422):
                # Versão do Hermes que não aceita stream_options: tenta de novo sem pedir o uso de tokens.
                return conversar(url, chave, mensagens, timeout, ao_texto, ao_ferramenta, com_uso=False)
            raise
        for bruto in resp:
            agora = time.monotonic() - inicio
            if r["t_primeiro_byte"] is None:
                r["t_primeiro_byte"] = agora
            if agora > timeout:
                r["erro"] = "tempo esgotado (%s)" % seg(timeout)
                break
            linha = bruto.decode("utf-8", "replace").rstrip("\r\n")
            if not linha:
                evento = None
                continue
            if linha.startswith(":"):
                continue  # comentário SSE, por exemplo ": keepalive"
            if linha.startswith("event:"):
                evento = linha[6:].strip()
                continue
            if not linha.startswith("data:"):
                if sum(len(s) for s in sobra) < 200000:
                    sobra.append(linha)
                continue
            dado = linha[5:].strip()
            if dado == "[DONE]":
                r["completo"] = True
                break
            try:
                obj = json.loads(dado)
            except ValueError:
                continue
            if evento and evento != "message":
                r["eventos"][evento] = r["eventos"].get(evento, 0) + 1
                if "tool" in evento and isinstance(obj, dict):
                    # O Hermes manda dois eventos por chamada (running e completed) com o mesmo toolCallId.
                    ident = obj.get("toolCallId") or obj.get("tool_call_id") or obj.get("call_id")
                    status = str(obj.get("status") or "").lower()
                    if ident:
                        if ident in chamadas:
                            continue
                        chamadas.add(ident)
                    elif status in STATUS_FIM:
                        continue
                    nome = nome_ferramenta(obj) or json.dumps(obj, ensure_ascii=False)[:60]
                    r["ferramentas"].append(nome)
                    if ao_ferramenta:
                        ao_ferramenta(nome)
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("error"):
                erro = obj["error"]
                if isinstance(erro, dict):
                    erro = erro.get("message") or json.dumps(erro, ensure_ascii=False)
                r["erro"] = limpar(str(erro)[:300], chave)
            if obj.get("usage"):
                r["uso"] = obj["usage"]
            for escolha in obj.get("choices") or []:
                if escolha.get("finish_reason"):
                    r["fim"] = escolha["finish_reason"]
                delta = escolha.get("delta") or escolha.get("message") or {}
                rac = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if rac:
                    r["raciocinio_chars"] += len(rac)
                    if r["t_primeiro_token"] is None:
                        r["t_primeiro_token"] = agora
                txt = delta.get("content") or ""
                if txt:
                    partes.append(txt)
                    if r["t_primeiro_token"] is None:
                        r["t_primeiro_token"] = agora
                    if r["t_primeira_palavra"] is None and txt.strip():
                        r["t_primeira_palavra"] = agora
                    if ao_texto:
                        ao_texto(txt)
                for chamada in delta.get("tool_calls") or []:
                    nome = ((chamada or {}).get("function") or {}).get("name")
                    if nome:
                        r["ferramentas"].append(nome)
                        if ao_ferramenta:
                            ao_ferramenta(nome)
    except KeyboardInterrupt:
        r["cancelado"] = True
        r["erro"] = "cancelado com Ctrl+C"
    except Exception as e:
        r["erro"] = limpar(descrever_erro(e), chave)
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
    r["t_total"] = time.monotonic() - inicio

    if not partes and sobra and not r["cancelado"]:
        # O servidor respondeu sem streaming: aproveita o JSON inteiro.
        try:
            d = json.loads("\n".join(sobra))
            texto = d["choices"][0]["message"].get("content") or ""
            partes.append(texto)
            r["uso"] = d.get("usage") or r["uso"]
            r["t_primeira_palavra"] = r["t_total"]
            r["completo"] = True
            if ao_texto and texto:
                ao_texto(texto)
        except Exception:
            if not r["erro"]:
                r["erro"] = limpar("resposta inesperada: " + " ".join(sobra)[:300], chave)
    r["texto"] = "".join(partes).strip()
    return r


# ---------------------------------------------------------------- integrações

def recursos(raiz=None, timeout: float = 40):
    """Pergunta ao jarvis-tools quais integrações estão configuradas.

    Devolve (dicionário, None) ou (None, motivo). Exemplo: {"moodle": true, "google": ["pessoal"], "busca": true}.
    """
    try:
        p = subprocess.run(CMD_RECURSOS, cwd=str(raiz or RAIZ), capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return None, "comando docker não encontrado"
    except subprocess.TimeoutExpired:
        return None, "o docker não respondeu em %s" % seg(timeout)
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    saida = sem_ansi(p.stdout.decode("utf-8", "replace"))
    if p.returncode != 0:
        erro = sem_ansi(p.stderr.decode("utf-8", "replace")).strip() or saida.strip()
        ultima = (erro.splitlines() or ["código de saída %d" % p.returncode])[-1]
        return None, ultima[:200]
    for linha in reversed([l.strip() for l in saida.splitlines() if l.strip()]):
        try:
            d = json.loads(linha)
        except ValueError:
            continue
        if isinstance(d, dict):
            return d, None
    try:
        d = json.loads(saida)
        if isinstance(d, dict):
            return d, None
    except ValueError:
        pass
    return None, "saída inesperada: " + saida.strip()[:120]


# ---------------------------------------------------------------- medicoes.py

def carregar_medicoes():
    """Carrega o scripts/medicoes.py da mesma pasta (para reaproveitar o conferir_hora).

    Devolve (módulo, None) ou (None, motivo).
    """
    caminho = PASTA_SCRIPTS / "medicoes.py"
    try:
        spec = importlib.util.spec_from_file_location("jarvis_medicoes", str(caminho))
        if spec is None or spec.loader is None:
            return None, "não encontrei %s" % caminho.name
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        if not callable(getattr(modulo, "conferir_hora", None)):
            return None, "%s não tem conferir_hora" % caminho.name
        return modulo, None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- formato para voz

EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
MARKDOWN = (
    ("título", re.compile(r"^\s{0,3}#{1,6}\s", re.M)),
    ("lista", re.compile(r"^\s*[-*+•]\s+\S", re.M)),
    ("lista numerada", re.compile(r"^\s*\d{1,2}[.)]\s+\S", re.M)),
    ("negrito", re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")),
    ("itálico", re.compile(r"(?<![*\w])\*[^*\s][^*\n]*\*(?![*\w])")),
    ("código", re.compile(r"`")),
    ("link", re.compile(r"\[[^\]\n]+\]\([^)\s]+\)")),
    ("tabela", re.compile(r"^\s*\|.*\|\s*$", re.M)),
)


def detectar_markdown(texto: str) -> list:
    """O que na resposta atrapalha a leitura em voz alta: listas, negrito, títulos, emojis..."""
    achados = [nome for nome, padrao in MARKDOWN if padrao.search(texto or "")]
    if EMOJI.search(texto or ""):
        achados.append("emoji")
    return achados

"""Requisições HTTP simples (biblioteca padrão), fora do loop assíncrono.

As mensagens de ErroRede são curtas e em português; nunca trazem o endereço, os parâmetros nem os
cabeçalhos do pedido (onde podem estar tokens).
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request

AGENTE = "jarvis-tools/1.0 (assistente pessoal local)"
LIMITE_RESPOSTA = 5 * 1024 * 1024  # APIs de JSON; nada legítimo passa disso


class ErroRede(Exception):
    """Falha ao consultar um serviço externo, com uma explicação curta em português."""

    def __init__(self, mensagem: str, codigo: int | None = None):
        super().__init__(mensagem)
        self.codigo = codigo  # status HTTP, quando o serviço chegou a responder


def _obter(url: str, parametros: dict, tempo_limite: float):
    endereco = url + ("?" + urllib.parse.urlencode(parametros, safe=",") if parametros else "")
    pedido = urllib.request.Request(endereco, headers={"User-Agent": AGENTE, "Accept": "application/json"})
    with urllib.request.urlopen(pedido, timeout=tempo_limite) as resposta:
        return json.loads(resposta.read(LIMITE_RESPOSTA).decode("utf-8"))


async def obter_json(url: str, parametros: dict | None = None, tempo_limite: float = 10.0):
    """GET que devolve o JSON; qualquer status de erro vira ErroRede (com o código)."""
    try:
        return await asyncio.to_thread(_obter, url, parametros or {}, tempo_limite)
    except urllib.error.HTTPError as erro:
        raise ErroRede("o serviço respondeu com erro %d" % erro.code, erro.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroRede("sem conexão com o serviço") from erro
    except ValueError as erro:
        raise ErroRede("resposta inválida do serviço") from erro


def _pedir(metodo: str, url: str, parametros, cabecalhos: dict, corpo: bytes | None, tempo_limite: float):
    endereco = url + ("?" + urllib.parse.urlencode(parametros, doseq=True, safe=",") if parametros else "")
    todos = {"User-Agent": AGENTE, "Accept": "application/json"}
    todos.update(cabecalhos)
    pedido = urllib.request.Request(endereco, data=corpo, method=metodo, headers=todos)
    try:
        with urllib.request.urlopen(pedido, timeout=tempo_limite) as resposta:
            status, bruto = resposta.status, resposta.read(LIMITE_RESPOSTA)
    except urllib.error.HTTPError as erro:
        status = erro.code
        try:
            bruto = erro.read(LIMITE_RESPOSTA)
        except OSError:
            bruto = b""
        finally:
            erro.close()
    texto = bruto.decode("utf-8", "replace").strip()
    if not texto:
        return status, None
    try:
        return status, json.loads(texto)
    except ValueError:
        if 200 <= status < 300:
            raise
        return status, None  # página de erro em HTML: o status já basta


async def pedir(metodo: str, url: str, *, parametros=None, cabecalhos: dict | None = None,
                corpo: bytes | None = None, tempo_limite: float = 15.0) -> tuple[int, object]:
    """Faz o pedido e devolve (status, JSON ou None) mesmo em 4xx/5xx; ErroRede só sem resposta."""
    try:
        return await asyncio.to_thread(_pedir, metodo, url, parametros, cabecalhos or {}, corpo, tempo_limite)
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroRede("sem conexão com o serviço") from erro
    except ValueError as erro:
        raise ErroRede("resposta inválida do serviço") from erro


async def obter_com_cabecalhos(url: str, parametros=None, cabecalhos: dict | None = None,
                               tempo_limite: float = 15.0) -> tuple[int, object]:
    """GET com cabeçalhos extras (por exemplo Authorization); devolve (status, JSON)."""
    return await pedir("GET", url, parametros=parametros, cabecalhos=cabecalhos, tempo_limite=tempo_limite)


async def postar_form(url: str, campos: dict, cabecalhos: dict | None = None,
                      tempo_limite: float = 15.0) -> tuple[int, object]:
    """POST application/x-www-form-urlencoded; devolve (status, JSON)."""
    extra = {"Content-Type": "application/x-www-form-urlencoded"}
    extra.update(cabecalhos or {})
    corpo = urllib.parse.urlencode(campos, doseq=True).encode()
    return await pedir("POST", url, cabecalhos=extra, corpo=corpo, tempo_limite=tempo_limite)


async def postar_json(url: str, dados, parametros=None, cabecalhos: dict | None = None,
                      tempo_limite: float = 15.0) -> tuple[int, object]:
    """POST com corpo JSON; devolve (status, JSON)."""
    extra = {"Content-Type": "application/json; charset=utf-8"}
    extra.update(cabecalhos or {})
    corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    return await pedir("POST", url, parametros=parametros, cabecalhos=extra, corpo=corpo,
                       tempo_limite=tempo_limite)

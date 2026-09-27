"""Conversa com o Hermes pela API compatível com OpenAI, em streaming (SSE).

A leitura é feita numa thread (biblioteca padrão) e os eventos chegam ao loop assíncrono por uma fila:
  ("texto", pedaço), ("ferramenta", nome), ("erro", mensagem) e, sempre por último, ("fim", None).
"""
from __future__ import annotations

import asyncio
import http.client
import json
import socket
import threading
import urllib.parse
from typing import AsyncIterator

MODELO = "hermes-agent"
TEMPO_LIMITE = 120.0  # segundos; o primeiro pedido depois de o modelo sair da VRAM pode demorar
STATUS_FIM = ("completed", "complete", "done", "finished", "success", "succeeded", "failed", "error", "end")

# Regra de fala, somada ao prompt do próprio Hermes (o SOUL.md já pede frases corridas)
SISTEMA = ("Esta conversa é por voz: responda em uma a três frases curtas, sem listas, títulos, negrito, "
           "emojis ou links. Diga números, datas e horas como se fala.")


class ErroHTTP(Exception):
    def __init__(self, codigo: int):
        super().__init__(codigo)
        self.codigo = codigo


def montar_mensagens(historico: list[dict], pergunta: str) -> list[dict]:
    return [{"role": "system", "content": SISTEMA}] + list(historico) + [{"role": "user", "content": pergunta}]


def nome_ferramenta(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    for chave in ("tool", "name", "tool_name", "label", "title"):
        valor = obj.get(chave)
        if isinstance(valor, str) and valor.strip():
            nome = valor.strip()[:60]
            return nome.rsplit("__", 1)[-1] if "__" in nome else nome  # mcp__jarvis__clima -> clima
    for chave in ("data", "payload"):
        if isinstance(obj.get(chave), dict):
            return nome_ferramenta(obj[chave])
    return ""


def _descrever(erro: BaseException) -> str:
    if isinstance(erro, ErroHTTP):
        return "o Hermes respondeu com erro %d" % erro.codigo
    if isinstance(erro, (TimeoutError, socket.timeout)):
        return "o Hermes demorou demais para responder"
    if isinstance(erro, (OSError, http.client.HTTPException)):
        return "sem conexão com o Hermes"
    return "falha ao ler a resposta do Hermes (%s)" % type(erro).__name__


def ler_sse(linhas, emitir, parar: threading.Event) -> None:
    """Interpreta as linhas SSE do Hermes e chama emitir(tipo, valor). Separado para os testes."""
    evento, chamadas = None, set()
    for bruto in linhas:
        if parar.is_set():
            return
        linha = bruto.decode("utf-8", "replace").rstrip("\r\n") if isinstance(bruto, bytes) else bruto.rstrip("\r\n")
        if not linha:
            evento = None
            continue
        if linha.startswith(":"):
            continue  # comentário SSE (keepalive)
        if linha.startswith("event:"):
            evento = linha[6:].strip()
            continue
        if not linha.startswith("data:"):
            continue
        dado = linha[5:].strip()
        if dado == "[DONE]":
            return
        try:
            obj = json.loads(dado)
        except ValueError:
            continue
        if evento and evento != "message":
            if "tool" in evento and isinstance(obj, dict):
                # O Hermes manda dois eventos por chamada (running e completed) com o mesmo toolCallId
                ident = obj.get("toolCallId") or obj.get("tool_call_id") or obj.get("call_id")
                status = str(obj.get("status") or "").lower()
                if ident:
                    if ident in chamadas:
                        continue
                    chamadas.add(ident)
                elif status in STATUS_FIM:
                    continue
                emitir("ferramenta", nome_ferramenta(obj) or "ferramenta")
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("error"):
            erro = obj["error"]
            if isinstance(erro, dict):
                erro = erro.get("message") or "erro"
            emitir("erro", "o Hermes devolveu um erro: %s" % str(erro)[:200])
        for escolha in obj.get("choices") or []:
            delta = escolha.get("delta") or escolha.get("message") or {}
            texto = delta.get("content") or ""
            if isinstance(texto, list):
                texto = "".join(p.get("text", "") for p in texto if isinstance(p, dict))
            if texto:
                emitir("texto", texto)
            for chamada in delta.get("tool_calls") or []:
                nome = ((chamada or {}).get("function") or {}).get("name")
                if nome:
                    emitir("ferramenta", nome.rsplit("__", 1)[-1])


async def conversar(url_base: str, chave: str, mensagens: list[dict], sessao: str = "",
                    tempo_limite: float = TEMPO_LIMITE) -> AsyncIterator[tuple[str, str | None]]:
    """Eventos da resposta, à medida que chegam. Cancelar o consumidor corta a conexão com o Hermes."""
    loop = asyncio.get_running_loop()
    fila: asyncio.Queue = asyncio.Queue()
    parar = threading.Event()
    conexao_aberta: list[http.client.HTTPConnection] = []

    def emitir(tipo, valor):
        loop.call_soon_threadsafe(fila.put_nowait, (tipo, valor))

    def trabalhar():
        partes = urllib.parse.urlsplit(url_base.rstrip("/") + "/v1/chat/completions")
        classe = http.client.HTTPSConnection if partes.scheme == "https" else http.client.HTTPConnection
        conexao = classe(partes.hostname, partes.port, timeout=tempo_limite)
        conexao_aberta.append(conexao)
        cabecalhos = {"Content-Type": "application/json", "Accept": "text/event-stream",
                      "Authorization": "Bearer " + chave}
        if sessao:
            cabecalhos["X-Hermes-Session-Key"] = sessao
        corpo = json.dumps({"model": MODELO, "messages": mensagens, "stream": True}).encode("utf-8")
        try:
            conexao.request("POST", partes.path, body=corpo, headers=cabecalhos)
            resposta = conexao.getresponse()
            if resposta.status != 200:
                raise ErroHTTP(resposta.status)
            ler_sse(resposta, emitir, parar)
        except Exception as erro:  # nunca inclui a chave: só o tipo e o código
            if not parar.is_set():
                emitir("erro", _descrever(erro))
        finally:
            conexao.close()
            emitir("fim", None)

    thread = threading.Thread(target=trabalhar, name="hermes", daemon=True)
    thread.start()
    try:
        while True:
            tipo, valor = await fila.get()
            if tipo == "fim":
                return
            yield tipo, valor
    finally:
        parar.set()
        # shutdown no socket destrava a thread na hora e não bloqueia o loop (fechar a resposta daqui
        # esperaria a thread largar a trava de leitura, ou seja, o próximo byte do Hermes)
        for conexao in conexao_aberta:
            sock = getattr(conexao, "sock", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

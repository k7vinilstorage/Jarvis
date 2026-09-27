"""Ponte de voz do Jarvis (jarvis-voz): o PC ou o ESP32 fala por WebSocket; a ponte passa pelo Whisper, pelo
Hermes e pelo Piper e devolve a resposta falada, frase a frase.

    GET /saude                                   {"status": "ok"} (sem token, para o healthcheck)
    WS  /voz?token=<JARVIS_VOZ_TOKEN>&sala=pc    a conversa (o token também vale em Authorization: Bearer)

Protocolo (mensagens de texto são JSON; as binárias são áudio):
  cliente -> ponte
    {"tipo": "config", "saida_taxa": 16000, "saida_formato": "s16le" | "u8"}   opcional, a qualquer hora
    {"tipo": "inicio"}  + áudio PCM 16 kHz, mono, 16 bits (binário, em pedaços)  + {"tipo": "fim"}
    {"tipo": "texto", "texto": "que horas são?"}   pergunta digitada (pula o Whisper)
    {"tipo": "falar", "texto": "..."}              só fala o texto (teste do alto-falante e do DAC)
    {"tipo": "cancelar"}   para a resposta atual        {"tipo": "nova"}   esquece o histórico da sala
  ponte -> cliente
    {"tipo": "pronto", ...}
    {"tipo": "estado", "estado": "ouvindo|aguardando|transcrevendo|pensando|falando|pronto"}
    {"tipo": "transcricao", "texto"}   {"tipo": "ferramenta", "nome"}   {"tipo": "frase", "texto", "falada"}
    {"tipo": "audio_inicio", "taxa", "formato", "canais"}  + áudio (binário)  + {"tipo": "audio_fim"}
    {"tipo": "fim", "pergunta", "resposta", "ferramentas", "tempos", "erro"}   (toda vez termina com um "fim";
                                                   interrompida, com erro "interrompido")
    {"tipo": "erro", "mensagem"}   {"tipo": "config", ...} (eco)   {"tipo": "nova"}   {"tipo": "pong"}
Recusas antes de conectar: HTTP 401 (token), 400 (sala inválida), 429 (conexões demais).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from app import audio, textos, turno
from app.config import PORTA, Config
from app.sessoes import Sessoes, sessao_valida

log = logging.getLogger("jarvis-voz")

LIMITE_MENSAGEM = 4096  # bytes de uma mensagem de texto (JSON)
TEMPO_ENVIO = 15.0      # segundos para uma mensagem sair; mais que isso, o cliente parou de ler
LIMITE_AUDIO = audio.MAX_SEGUNDOS * audio.BYTES_POR_SEGUNDO


class Ponte:
    def __init__(self, config: Config, sessoes: Sessoes | None = None):
        self.config = config
        self.sessoes = sessoes if sessoes is not None else Sessoes()
        self.vez = asyncio.Lock()  # uma resposta por vez: o Ollama atende um pedido de cada vez
        self.conexoes = 0

    def token_ok(self, websocket: WebSocket) -> bool:
        recebido = websocket.query_params.get("token", "")
        cabecalho = websocket.headers.get("authorization", "")
        if not recebido and cabecalho.lower().startswith("bearer "):
            recebido = cabecalho[7:].strip()
        return hmac.compare_digest(recebido.encode(), self.config.token.encode())

    @staticmethod
    async def recusar(websocket: WebSocket, status: int, motivo: str) -> None:
        """Responde HTTP antes do WebSocket abrir (o cliente vê 401/400/429); sem suporte, fecha (403)."""
        try:
            await websocket.send_denial_response(PlainTextResponse(motivo, status_code=status))
        except RuntimeError:
            await websocket.close(code=4000 + status)

    async def saude(self, request):
        return JSONResponse({"status": "ok"})

    async def voz(self, websocket: WebSocket):
        cliente = (websocket.client.host if websocket.client else "?")
        if not self.token_ok(websocket):
            log.warning("conexão recusada de %s: token ausente ou errado", cliente)
            await self.recusar(websocket, 401, "token ausente ou errado")
            return
        sala = websocket.query_params.get("sala") or "pc"
        if not sessao_valida(sala):
            await self.recusar(websocket, 400, "sala inválida: até 64 letras, números, '.', '-' ou '_'")
            return
        if self.conexoes >= self.config.max_conexoes:
            log.warning("conexão recusada de %s: já há %d abertas", cliente, self.conexoes)
            await self.recusar(websocket, 429, "conexões demais abertas")
            return
        self.conexoes += 1
        await websocket.accept()
        log.info("conectado: %s (sala %s)", cliente, sala)
        try:
            await Conexao(self, websocket, sala).rodar()
        finally:
            self.conexoes -= 1
            log.info("desconectado: %s (sala %s)", cliente, sala)


class Conexao:
    def __init__(self, ponte: Ponte, websocket: WebSocket, sala: str):
        self.ponte = ponte
        self.ws = websocket
        self.sala = sala
        self.trava_envio = asyncio.Lock()
        self.saida = turno.Saida(self.enviar_json, self.enviar_bytes, ativa=lambda: self.aberta)
        self.gravando = False
        self.fala = bytearray()
        self.tarefa: asyncio.Task | None = None
        self.tarefa_eh_resposta = False
        self.leitura: asyncio.Task | None = None
        self.aberta = True

    async def _enviar(self, envio) -> None:
        if not self.aberta:
            envio.close()
            return
        async with self.trava_envio:
            try:
                await asyncio.wait_for(envio, TEMPO_ENVIO)
            except asyncio.TimeoutError:
                # O cliente parou de ler (ESP32 travado, Wi-Fi caído): desiste dele e libera a ponte
                log.warning("sala %s parou de receber; fechando a conexão", self.sala)
                self.aberta = False
                asyncio.get_running_loop().call_soon(self._fechar_ja)
            except (WebSocketDisconnect, RuntimeError, OSError):
                self.aberta = False

    def _fechar_ja(self) -> None:
        """Encerra a leitura da conexão (o handler sai e cancela a resposta em andamento)."""
        if self.leitura and not self.leitura.done():
            self.leitura.cancel()

    async def enviar_json(self, dados: dict) -> None:
        await self._enviar(self.ws.send_text(json.dumps(dados, ensure_ascii=False)))

    async def enviar_bytes(self, dados: bytes) -> None:
        await self._enviar(self.ws.send_bytes(dados))

    async def estado(self, estado: str) -> None:
        await self.enviar_json({"tipo": "estado", "estado": estado})

    async def rodar(self) -> None:
        await self.enviar_json({"tipo": "pronto", "sala": self.sala, "entrada": {
            "taxa": audio.TAXA, "formato": "s16le", "canais": 1, "max_segundos": audio.MAX_SEGUNDOS}})
        self.leitura = asyncio.create_task(self._ler())
        try:
            await self.leitura
        except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self.aberta = False
            await self.interromper()
            if self.leitura.cancelled():  # fomos nós que desistimos do cliente; se ele saiu, não há o que fechar
                try:
                    await self.ws.close()
                except (WebSocketDisconnect, RuntimeError, OSError):
                    pass

    async def _ler(self) -> None:
        while True:
            mensagem = await self.ws.receive()
            if mensagem["type"] == "websocket.disconnect":
                return
            if mensagem.get("bytes") is not None:
                await self.receber_audio(mensagem["bytes"])
            elif mensagem.get("text") is not None:
                await self.receber_texto(mensagem["text"])

    async def receber_audio(self, dados: bytes) -> None:
        if not self.gravando:
            return
        self.fala.extend(dados)
        if len(self.fala) >= LIMITE_AUDIO:  # passou do máximo: responde com o que chegou
            del self.fala[LIMITE_AUDIO:]
            await self.enviar_json({"tipo": "erro", "mensagem": "fala longa demais; usei os primeiros %d s"
                                    % audio.MAX_SEGUNDOS})
            await self.fim_da_fala()

    async def receber_texto(self, bruto: str) -> None:
        if len(bruto.encode("utf-8", "replace")) > LIMITE_MENSAGEM:
            await self.enviar_json({"tipo": "erro", "mensagem": "mensagem grande demais"})
            return
        try:
            dados = json.loads(bruto)
            tipo = str(dados.get("tipo") or "")
        except (ValueError, AttributeError):
            await self.enviar_json({"tipo": "erro", "mensagem": "mensagem inválida (esperado JSON com 'tipo')"})
            return
        if tipo == "config":
            await self.configurar(dados)
        elif tipo == "inicio":
            await self.interromper()  # falar por cima corta a resposta anterior
            self.gravando, self.fala = True, bytearray()
            await self.estado("ouvindo")
        elif tipo == "fim":
            await self.fim_da_fala()
        elif tipo == "texto":
            texto = " ".join(str(dados.get("texto") or "").split())
            if texto:
                await self.comecar(texto=texto)
        elif tipo == "falar":
            texto = " ".join(str(dados.get("texto") or "").split())
            if texto:
                await self.parar_gravacao()
                await self.interromper()
                self.tarefa, self.tarefa_eh_resposta = asyncio.create_task(self.so_falar(texto)), True
        elif tipo == "cancelar":
            await self.parar_gravacao()
            await self.interromper()
            await self.estado("pronto")
        elif tipo == "nova":
            await self.interromper()  # a resposta em andamento não volta a gravar o histórico antigo
            self.ponte.sessoes.limpar(self.sala)
            await self.enviar_json({"tipo": "nova", "sala": self.sala})
        elif tipo == "ping":
            await self.enviar_json({"tipo": "pong"})
        else:
            await self.enviar_json({"tipo": "erro", "mensagem": "tipo desconhecido: %s" % tipo[:30]})

    async def configurar(self, dados: dict) -> None:
        taxa, formato = dados.get("saida_taxa"), dados.get("saida_formato") or "s16le"
        if taxa is not None and (not isinstance(taxa, int) or taxa not in audio.TAXAS_SAIDA):
            await self.enviar_json({"tipo": "erro", "mensagem": "saida_taxa precisa ser de 8000 a 48000"})
            return
        if formato not in audio.FORMATOS_SAIDA:
            await self.enviar_json({"tipo": "erro", "mensagem": "saida_formato precisa ser s16le ou u8"})
            return
        self.saida.taxa, self.saida.formato = taxa, formato
        await self.enviar_json({"tipo": "config", "saida_taxa": taxa, "saida_formato": formato})

    async def parar_gravacao(self) -> None:
        self.gravando, self.fala = False, bytearray()

    async def fim_da_fala(self) -> None:
        if not self.gravando:
            return
        pcm = bytes(self.fala[:len(self.fala) - len(self.fala) % 2])
        await self.parar_gravacao()
        await self.comecar(pcm=pcm)

    async def comecar(self, pcm: bytes | None = None, texto: str | None = None) -> None:
        tempos = turno.Tempos()  # o relógio começa quando a fala termina (ou a pergunta chega)
        await self.parar_gravacao()
        await self.interromper()  # nunca duas respostas na mesma conexão
        self.tarefa = asyncio.create_task(self.uma_vez(pcm, texto, tempos))
        self.tarefa_eh_resposta = True

    async def uma_vez(self, pcm: bytes | None, texto: str | None, tempos: turno.Tempos) -> None:
        ponte = self.ponte
        try:
            resumo = await turno.responder(ponte.config, ponte.sessoes, self.sala, self.saida, pcm=pcm,
                                           texto=texto, tempos=tempos, vez=ponte.vez)
        except Exception as erro:  # um defeito aqui não pode deixar o cliente esperando para sempre
            log.exception("falha inesperada na resposta")
            resumo = {"pergunta": texto or "", "resposta": "", "ferramentas": [], "tempos": tempos.valores,
                      "erro": "falha inesperada na ponte (%s)" % type(erro).__name__}
            await self.enviar_json({"tipo": "erro", "mensagem": resumo["erro"]})
        self.tarefa_eh_resposta = False  # o "fim" vai daqui; uma interrupção agora não manda outro
        await self.estado("pronto")
        await self.enviar_json({"tipo": "fim", **resumo})
        segundos = audio.duracao(pcm) if pcm is not None else 0
        log.info('sala=%s fala=%.1fs pergunta="%s" ferramentas=%s tempos=%s%s', self.sala, segundos,
                 textos.resumo(resumo["pergunta"]), ",".join(resumo["ferramentas"]) or "-",
                 " ".join("%s=%.2f" % item for item in resumo["tempos"].items()),
                 (" erro=%s" % resumo["erro"]) if resumo["erro"] else "")

    async def so_falar(self, texto: str) -> None:
        falador = turno.Falador(self.ponte.config, self.saida, turno.Tempos())
        tarefa = asyncio.create_task(falador.rodar())
        falador.dizer(texto)
        falador.terminar()
        try:
            await tarefa
        except asyncio.CancelledError:
            tarefa.cancel()
            raise
        self.tarefa_eh_resposta = False
        await self.estado("pronto")
        await self.enviar_json({"tipo": "fim", "pergunta": "", "resposta": texto, "ferramentas": [],
                                "tempos": falador.tempos.valores, "erro": falador.erro})

    async def interromper(self) -> None:
        tarefa, self.tarefa = self.tarefa, None
        if tarefa and not tarefa.done():
            tarefa.cancel()
            try:
                await tarefa
            except (asyncio.CancelledError, Exception):
                pass
            if self.saida.audio_aberto:
                self.saida.audio_aberto = False
                await self.enviar_json({"tipo": "audio_fim"})
            if self.tarefa_eh_resposta:
                await self.enviar_json({"tipo": "fim", "pergunta": "", "resposta": "", "ferramentas": [],
                                        "tempos": {}, "erro": "interrompido"})


def criar_app(config: Config, sessoes: Sessoes | None = None) -> Starlette:
    ponte = Ponte(config, sessoes)
    return Starlette(routes=[Route("/saude", ponte.saude), WebSocketRoute("/voz", ponte.voz)])


def _hora_do_log():
    """O log sai no fuso do TZ (a imagem slim não traz o tzdata do sistema, só o pacote Python)."""
    try:
        fuso = ZoneInfo(os.environ.get("TZ") or "America/Sao_Paulo")
    except (ZoneInfoNotFoundError, ValueError):
        return
    logging.Formatter.converter = staticmethod(lambda segundos: datetime.fromtimestamp(segundos, fuso).timetuple())


class _SemRuidoDeRecusa(logging.Filter):
    """O uvicorn registra como erro toda conexão recusada antes do WebSocket abrir (token errado); a ponte já
    registra a recusa com o motivo."""

    def filter(self, registro) -> bool:
        return "without completing handshake" not in registro.getMessage()


def main():
    _hora_do_log()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    for barulhento in ("uvicorn.access", "websockets", "uvicorn.error", "uvicorn"):
        logging.getLogger(barulhento).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").addFilter(_SemRuidoDeRecusa())
    config = Config.do_ambiente()
    log.info("Whisper em %s, Piper em %s (voz %s), Hermes em %s", config.whisper_uri, config.piper_uri,
             config.piper_voz, config.hermes_url)
    uvicorn.run(criar_app(config), host="0.0.0.0", port=int(os.environ.get("PORTA", PORTA)),
                log_config=None, access_log=False, proxy_headers=False, server_header=False,
                ws="websockets-sansio", ws_max_size=256 * 1024, ws_ping_interval=20, ws_ping_timeout=20)


if __name__ == "__main__":
    main()

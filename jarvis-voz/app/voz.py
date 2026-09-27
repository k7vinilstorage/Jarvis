"""Whisper (fala -> texto) e Piper (texto -> fala) pelo protocolo Wyoming, o mesmo dos contêineres
rhasspy/wyoming-whisper e rhasspy/wyoming-piper que já rodam no compose."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.error import Error
from wyoming.tts import Synthesize, SynthesizeVoice

from app import audio

TEMPO_CONEXAO = 5.0
TEMPO_STT = 60.0   # transcrever até 30 s de áudio na CPU
TEMPO_TTS = 60.0   # sintetizar uma frase na CPU
BYTES_POR_PEDACO = 3200  # 100 ms de áudio a 16 kHz


class ErroVoz(Exception):
    """Falha no Whisper ou no Piper, com uma explicação curta em português."""


@dataclass(frozen=True)
class Formato:
    taxa: int
    largura: int
    canais: int


def _traduzir(nome: str, erro: BaseException, tempo_limite: float) -> ErroVoz:
    if isinstance(erro, ErroVoz):
        return erro
    if isinstance(erro, (asyncio.TimeoutError, TimeoutError)):
        return ErroVoz("o %s não respondeu em %d s" % (nome, tempo_limite))
    if isinstance(erro, ConnectionRefusedError):
        return ErroVoz("sem conexão com o %s" % nome)
    if isinstance(erro, (ConnectionError, asyncio.IncompleteReadError)):
        return ErroVoz("o %s fechou a conexão" % nome)
    if isinstance(erro, OSError):  # inclui nome que não resolve (serviço fora do compose)
        return ErroVoz("sem conexão com o %s" % nome)
    return ErroVoz("resposta inválida do %s (%s)" % (nome, type(erro).__name__))


async def transcrever(uri: str, pcm: bytes, idioma: str = "pt", tempo_limite: float = TEMPO_STT) -> str:
    """Manda PCM 16 kHz mono de 16 bits e devolve o texto (pode vir vazio)."""
    async def conversa() -> str:
        async with AsyncClient.from_uri(uri, connect_timeout=TEMPO_CONEXAO, read_timeout=tempo_limite) as cliente:
            await cliente.write_event(Transcribe(language=idioma).event())
            formato = {"rate": audio.TAXA, "width": audio.LARGURA, "channels": audio.CANAIS}
            await cliente.write_event(AudioStart(**formato).event())
            for inicio in range(0, len(pcm), BYTES_POR_PEDACO):
                await cliente.write_event(AudioChunk(audio=pcm[inicio:inicio + BYTES_POR_PEDACO], **formato).event())
            await cliente.write_event(AudioStop().event())
            while True:
                evento = await cliente.read_event()
                if evento is None:
                    raise ErroVoz("o Whisper fechou a conexão sem transcrever")
                if Transcript.is_type(evento.type):
                    return Transcript.from_event(evento).text or ""
                if Error.is_type(evento.type):
                    raise ErroVoz("erro no Whisper: %s" % Error.from_event(evento).text)

    try:
        return await asyncio.wait_for(conversa(), tempo_limite)
    except Exception as erro:
        raise _traduzir("Whisper", erro, tempo_limite) from None


async def sintetizar_pedacos(uri: str, texto: str, voz: str = "",
                             tempo_limite: float = TEMPO_TTS) -> AsyncIterator[tuple[Formato, bytes]]:
    """A fala de uma frase em pedaços de PCM, à medida que o Piper manda (a voz começa antes de a frase
    inteira ficar pronta, quando o Piper divide o áudio)."""
    prazo = asyncio.get_running_loop().time() + tempo_limite
    try:
        async with AsyncClient.from_uri(uri, connect_timeout=TEMPO_CONEXAO, read_timeout=tempo_limite) as cliente:
            await cliente.write_event(
                Synthesize(text=texto, voice=SynthesizeVoice(name=voz) if voz else None).event())
            formato = None
            recebeu = False
            while True:
                restante = prazo - asyncio.get_running_loop().time()
                if restante <= 0:
                    raise asyncio.TimeoutError()
                evento = await asyncio.wait_for(cliente.read_event(), restante)
                if evento is None:
                    raise ErroVoz("o Piper fechou a conexão no meio do áudio")
                if AudioStart.is_type(evento.type):
                    inicio = AudioStart.from_event(evento)
                    formato = Formato(inicio.rate, inicio.width, inicio.channels)
                elif AudioChunk.is_type(evento.type):
                    pedaco = AudioChunk.from_event(evento)
                    formato = formato or Formato(pedaco.rate, pedaco.width, pedaco.channels)
                    if pedaco.audio:
                        recebeu = True
                        yield formato, pedaco.audio
                elif AudioStop.is_type(evento.type):
                    break
                elif Error.is_type(evento.type):
                    raise ErroVoz("erro no Piper: %s" % Error.from_event(evento).text)
            if not recebeu:
                raise ErroVoz("o Piper não devolveu áudio")
    except (GeneratorExit, asyncio.CancelledError):
        raise
    except Exception as erro:
        raise _traduzir("Piper", erro, tempo_limite) from None


async def sintetizar(uri: str, texto: str, voz: str = "", tempo_limite: float = TEMPO_TTS) -> tuple[Formato, bytes]:
    """A fala inteira de um texto: (formato, PCM)."""
    formato, partes = None, []
    async for formato, pedaco in sintetizar_pedacos(uri, texto, voz, tempo_limite):
        partes.append(pedaco)
    return formato, b"".join(partes)

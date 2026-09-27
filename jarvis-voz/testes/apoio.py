"""Apoio aos testes: Whisper e Piper falsos (protocolo Wyoming de verdade), Hermes falso (SSE) e a ponte
rodando num uvicorn local. Nada sai para a internet."""
import asyncio
import json
import math
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn
from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import async_read_event, async_write_event

from app import servidor
from app.config import Config
from app.sessoes import Sessoes

TOKEN = "t" * 32
TAXA_PIPER = 22050
SEGUNDOS_POR_CARACTERE = 0.06  # a fala falsa do Piper: 60 ms por caractere


def fala(segundos=1.0, amplitude=8000, taxa=16000):
    """Um tom de 300 Hz: passa no filtro de silêncio e o Whisper falso 'transcreve'."""
    return b"".join(struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 300 * i / taxa)))
                    for i in range(int(segundos * taxa)))


class LoopEmThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def rodar(self, corrotina, tempo=10):
        return asyncio.run_coroutine_threadsafe(corrotina, self.loop).result(tempo)

    def parar(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


class WyomingFalso:
    """Whisper e Piper numa porta só: responde a 'transcribe' e a 'synthesize'.

    transcricao: texto devolvido pelo Whisper (ou função(bytes) -> texto); atraso_stt/atraso_tts em segundos;
    falhar_tts: responde erro no Piper; textos_falados guarda o que o Piper recebeu."""

    def __init__(self, transcricao="Que horas são?", atraso_stt=0.0, atraso_tts=0.0):
        self.transcricao = transcricao
        self.atraso_stt = atraso_stt
        self.atraso_tts = atraso_tts
        self.falhar_tts = False
        self.textos_falados = []
        self.audios_recebidos = []
        self.loop = LoopEmThread()
        self.servidor = self.loop.rodar(asyncio.start_server(self._tratar, "127.0.0.1", 0))
        self.porta = self.servidor.sockets[0].getsockname()[1]
        self.uri = "tcp://127.0.0.1:%d" % self.porta

    async def _tratar(self, leitor, escritor):
        try:
            recebido = bytearray()
            while True:
                evento = await async_read_event(leitor)
                if evento is None:
                    return
                if evento.type == "audio-chunk":
                    recebido += AudioChunk.from_event(evento).audio
                elif evento.type == "audio-stop":
                    self.audios_recebidos.append(bytes(recebido))
                    await asyncio.sleep(self.atraso_stt)
                    texto = self.transcricao(bytes(recebido)) if callable(self.transcricao) else self.transcricao
                    await async_write_event(Transcript(text=texto).event(), escritor)
                    return
                elif evento.type == "synthesize":
                    texto = (evento.data or {}).get("text", "")
                    self.textos_falados.append(texto)
                    await asyncio.sleep(self.atraso_tts)
                    if self.falhar_tts:
                        from wyoming.error import Error
                        await async_write_event(Error(text="voz não encontrada").event(), escritor)
                        return
                    pcm = fala(max(0.05, len(texto) * SEGUNDOS_POR_CARACTERE), taxa=TAXA_PIPER)  # em dois pedaços
                    formato = {"rate": TAXA_PIPER, "width": 2, "channels": 1}
                    await async_write_event(AudioStart(**formato).event(), escritor)
                    meio = len(pcm) // 4 * 2
                    for pedaco in (pcm[:meio], pcm[meio:]):
                        await async_write_event(AudioChunk(audio=pedaco, **formato).event(), escritor)
                    await async_write_event(AudioStop().event(), escritor)
                    return
        finally:
            escritor.close()

    def fechar(self):
        self.servidor.close()
        self.loop.parar()


class HermesFalso:
    """/v1/chat/completions em SSE. roteiro: lista de ('texto', str) | ('ferramenta', nome) | ('pausa', s) |
    ('erro_http', código). Guarda os corpos e cabeçalhos recebidos."""

    def __init__(self, roteiro=None):
        self.roteiro = roteiro or [("texto", "São 15h30. "), ("texto", "Algo mais?")]
        self.pedidos = []
        dono = self

        class Tratador(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                tamanho = int(self.headers.get("Content-Length") or 0)
                corpo = json.loads(self.rfile.read(tamanho) or b"{}")
                dono.pedidos.append({"corpo": corpo, "cabecalhos": {k.lower(): v for k, v in self.headers.items()}})
                erro = next((v for t, v in dono.roteiro if t == "erro_http"), None)
                if erro:
                    self.send_response(erro)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                chamada = 0
                try:
                    for tipo, valor in dono.roteiro:
                        if tipo == "pausa":
                            time.sleep(valor)
                            continue
                        if tipo == "texto":
                            dado = {"choices": [{"delta": {"content": valor}}]}
                            self.wfile.write(("data: %s\n\n" % json.dumps(dado)).encode())
                        elif tipo == "ferramenta":
                            chamada += 1
                            for status in ("running", "completed"):
                                dado = {"tool": "mcp__jarvis__" + valor, "toolCallId": "c%d" % chamada,
                                        "status": status}
                                self.wfile.write(("event: hermes.tool.progress\ndata: %s\n\n"
                                                  % json.dumps(dado)).encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Tratador)
        self.servidor.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.servidor.server_address[1]
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def fechar(self):
        self.servidor.shutdown()
        self.servidor.server_close()


def configuracao(hermes_url, wyoming_uri, **extras):
    return Config(token=TOKEN, hermes_url=hermes_url, hermes_chave="chave-hermes", whisper_uri=wyoming_uri,
                  piper_uri=wyoming_uri, piper_voz="pt_BR-faber-medium", **extras)


class PonteRodando:
    """A ponte num uvicorn de verdade (thread própria), numa porta livre."""

    def __init__(self, config, sessoes=None):
        self.sessoes = sessoes or Sessoes()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.porta = s.getsockname()[1]
        self.servidor = uvicorn.Server(uvicorn.Config(servidor.criar_app(config, self.sessoes), host="127.0.0.1",
                                                      port=self.porta, log_level="error", ws="websockets-sansio"))
        self.thread = threading.Thread(target=self.servidor.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.servidor.started:
                break
            time.sleep(0.05)
        self.url = "ws://127.0.0.1:%d/voz" % self.porta

    def fechar(self):
        self.servidor.should_exit = True
        self.thread.join(5)

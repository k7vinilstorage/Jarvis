#!/usr/bin/env python3
"""Cliente de voz do Jarvis para o seu PC (Windows, Linux ou Mac): fale no microfone e ouça a resposta.

Instalação no PC (uma vez):   pip install websockets sounddevice
Uso:
    python voz-pc.py --servidor 192.168.0.10 --token SEU_TOKEN
    (o token é o JARVIS_VOZ_TOKEN do .env do servidor; também pode vir da variável de ambiente)

Na conversa:
    Enter          começa a gravar; Enter de novo termina e manda
    texto + Enter  pergunta digitada (sem microfone)
    /nova          esquece a conversa        /sair   sai

Outras opções:
    --wav pergunta.wav      manda um arquivo em vez do microfone (16 kHz, mono, 16 bits de preferência)
    --texto "que horas são" uma pergunta digitada e sai
    --salvar resposta.wav   guarda o áudio da última resposta
    --listar-audio          mostra os microfones e alto-falantes (use o número em --entrada / --saida)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import sys
import threading
import time
import urllib.parse
import wave

TAXA = 16000
PORTA = 10800

try:
    import websockets
except ImportError:
    sys.exit("Falta a biblioteca websockets. Instale com: pip install websockets sounddevice")


def reamostrar(pcm: bytes, de: int, para: int) -> bytes:
    """PCM mono de 16 bits de uma taxa para outra (interpolação linear)."""
    if de == para or len(pcm) < 4:
        return pcm
    import array
    amostras = array.array("h")
    amostras.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    largura = round(de / para)
    if largura > 1:  # média antes de reduzir a taxa: tira os agudos que virariam ruído (aliasing)
        soma, suavizado = 0, array.array("h", bytes(2 * len(amostras)))
        for i, valor in enumerate(amostras):
            soma += valor - (amostras[i - largura] if i >= largura else 0)
            suavizado[i] = soma // min(i + 1, largura)
        amostras = suavizado
    n = len(amostras)
    total = max(1, int(n * para / de))
    passo = (n - 1) / max(1, total - 1)
    saida = array.array("h", bytes(2 * total))
    for i in range(total):
        pos = i * passo
        j = int(pos)
        a = amostras[j]
        b = amostras[j + 1] if j + 1 < n else a
        saida[i] = int(a + (b - a) * (pos - j))
    return saida.tobytes()


def ler_wav(caminho: str) -> bytes:
    with wave.open(caminho, "rb") as arquivo:
        if arquivo.getsampwidth() != 2:
            sys.exit("O WAV precisa ser de 16 bits.")
        canais, taxa = arquivo.getnchannels(), arquivo.getframerate()
        dados = arquivo.readframes(arquivo.getnframes())
    if canais == 2:
        import array
        a = array.array("h")
        a.frombytes(dados)
        dados = array.array("h", [(a[i] + a[i + 1]) // 2 for i in range(0, len(a) - 1, 2)]).tobytes()
    return reamostrar(dados, taxa, TAXA)


class Tocador:
    """Toca o áudio da resposta numa thread (o sounddevice bloqueia ao escrever)."""

    def __init__(self, saida=None):
        self.saida = saida
        self.fila: queue.Queue = queue.Queue()
        self.taxa = None
        self.stream = None
        self.gravado = bytearray()
        self.taxa_gravada = TAXA
        self.descartando = False
        self.avisou = False
        threading.Thread(target=self._rodar, daemon=True).start()

    def comecar(self, taxa: int):
        self.descartando = False
        self.fila.put(("taxa", taxa))
        self.gravado = bytearray()
        self.taxa_gravada = taxa

    def tocar(self, dados: bytes):
        if self.descartando:  # resto da resposta anterior, que ainda vinha a caminho
            return
        self.gravado += dados
        self.fila.put(("audio", dados))

    def parar(self):
        """Joga fora o que ainda não tocou (quando você fala por cima), e o que ainda chegar dessa resposta."""
        self.descartando = True
        try:
            while True:
                self.fila.get_nowait()
                self.fila.task_done()
        except queue.Empty:
            pass

    def esperar(self):
        """Espera terminar de tocar o que já chegou."""
        self.fila.join()
        time.sleep(0.4)  # o que ainda está no buffer da placa de som

    def _rodar(self):
        import sounddevice as sd
        while True:
            tipo, valor = self.fila.get()
            try:
                self._tratar(sd, tipo, valor)
            except Exception as erro:  # alto-falante errado ou desligado: avisa e segue sem som
                if not self.avisou:
                    print("  ! não consegui tocar o áudio (%s); veja --listar-audio e --saida" % erro)
                    self.avisou = True
                self.stream, self.taxa = None, None
            finally:
                self.fila.task_done()

    def _tratar(self, sd, tipo, valor):
        if tipo == "taxa" and self.taxa != valor:
            if self.stream:
                self.stream.stop()
                self.stream.close()
            self.stream = sd.RawOutputStream(samplerate=valor, channels=1, dtype="int16", device=self.saida)
            self.stream.start()
            self.taxa = valor
        elif tipo == "audio" and self.stream:
            self.stream.write(valor)


class Microfone:
    """Grava a 16 kHz (ou na taxa do aparelho, convertendo) e entrega pedaços de 100 ms."""

    def __init__(self, loop, fila: asyncio.Queue, entrada=None):
        self.loop, self.fila, self.entrada = loop, fila, entrada
        self.stream = None
        self.taxa = TAXA

    def _chegou(self, dados, quadros, tempo, status):
        pedaco = bytes(dados)
        if self.taxa != TAXA:
            pedaco = reamostrar(pedaco, self.taxa, TAXA)
        self.loop.call_soon_threadsafe(self.fila.put_nowait, pedaco)

    def abrir(self):
        import sounddevice as sd
        try:
            self.taxa = TAXA
            self.stream = sd.RawInputStream(samplerate=TAXA, channels=1, dtype="int16", blocksize=TAXA // 10,
                                            device=self.entrada, callback=self._chegou)
        except Exception:
            # Alguns aparelhos (WASAPI no Windows) só aceitam a própria taxa: grava nela e converte
            self.taxa = int(sd.query_devices(self.entrada, "input")["default_samplerate"])
            self.stream = sd.RawInputStream(samplerate=self.taxa, channels=1, dtype="int16",
                                            blocksize=self.taxa // 10, device=self.entrada, callback=self._chegou)
        self.stream.start()

    def fechar(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def mostrar(evento: dict):
    tipo = evento["tipo"]
    if tipo == "transcricao":
        print("  você: %s" % evento["texto"])
    elif tipo == "ferramenta":
        print("  (usando %s)" % evento["nome"])
    elif tipo == "frase":
        print("  jarvis: %s" % evento["texto"])
    elif tipo == "estado" and evento["estado"] == "aguardando":
        print("  (esperando outra conversa terminar)")
    elif tipo == "erro":
        print("  ! %s" % evento["mensagem"])
    elif tipo == "fim":
        t = evento.get("tempos") or {}
        partes = []
        for nome, rotulo in (("stt", "entender"), ("primeira_palavra", "1ª palavra"), ("primeiro_audio", "1º áudio")):
            if nome in t:
                partes.append(("%s %.1f s" % (rotulo, t[nome])).replace(".", ","))
        if partes:
            print("  [depois que você parou de falar: %s]" % ", ".join(partes))
        if evento.get("erro"):
            print("  [erro: %s]" % evento["erro"])


async def conversar(opcoes) -> int:
    token = opcoes.token or os.environ.get("JARVIS_VOZ_TOKEN", "")
    if not token:
        print("Falta o token: --token (é o JARVIS_VOZ_TOKEN do .env do servidor).", file=sys.stderr)
        return 2
    url = "ws://%s:%d/voz?token=%s&sala=%s" % (opcoes.servidor, opcoes.porta, urllib.parse.quote(token, safe=""),
                                                urllib.parse.quote(opcoes.sala, safe=""))
    tocador = Tocador(opcoes.saida)
    loop = asyncio.get_running_loop()
    comandos: asyncio.Queue = asyncio.Queue()
    audio_mic: asyncio.Queue = asyncio.Queue()
    mic = Microfone(loop, audio_mic, opcoes.entrada)
    terminou = asyncio.Event()

    try:
        ws = await websockets.connect(url, max_size=None, ping_interval=20)
    except Exception as erro:
        status = getattr(getattr(erro, "response", None), "status_code", None)
        motivos = {401: "token errado (use o JARVIS_VOZ_TOKEN do .env do servidor)",
                   400: "nome de sala inválido (use letras, números, '.', '-' ou '_')",
                   429: "conexões demais abertas; feche outro cliente"}
        if status in motivos:
            print("O servidor recusou a conexão: %s." % motivos[status], file=sys.stderr)
        else:
            print("Não consegui conectar em %s:%d (%s). Confira o IP, a porta e se o jarvis-voz está rodando."
                  % (opcoes.servidor, opcoes.porta, type(erro).__name__), file=sys.stderr)
        return 1

    async def receber():
        try:
            async for mensagem in ws:
                if isinstance(mensagem, bytes):
                    tocador.tocar(mensagem)
                    continue
                evento = json.loads(mensagem)
                if evento["tipo"] == "audio_inicio":
                    tocador.comecar(evento["taxa"])
                mostrar(evento)
                if evento["tipo"] == "fim":
                    terminou.set()
        except websockets.exceptions.ConnectionClosed as erro:
            print("  ! a conexão com o servidor caiu (%s)" % (erro.rcvd.code if erro.rcvd else "sem código"))
        caiu.set()
        terminou.set()
        comandos.put_nowait("/sair")

    caiu = asyncio.Event()
    try:
        await asyncio.wait_for(ws.recv(), 10)  # "pronto": lido antes de o recebedor começar a ler
    except Exception:
        print("O servidor não respondeu depois de conectar.", file=sys.stderr)
        await ws.close()
        return 1
    recebedor = asyncio.create_task(receber())

    async def enviar_microfone():
        while True:
            pedaco = await audio_mic.get()
            try:
                await ws.send(pedaco)
            except websockets.exceptions.ConnectionClosed:
                return

    enviador = asyncio.create_task(enviar_microfone())

    async def perguntar_e_esperar(mensagens):
        terminou.clear()
        for mensagem in mensagens:
            await ws.send(mensagem if isinstance(mensagem, bytes) else json.dumps(mensagem))
        await terminou.wait()
        await asyncio.sleep(0.2)
        return not caiu.is_set()

    try:
        if opcoes.texto:
            ok = await perguntar_e_esperar([{"tipo": "texto", "texto": opcoes.texto}])
            tocador.esperar()
            return 0 if ok else 1
        if opcoes.wav:
            pcm = ler_wav(opcoes.wav)
            pedacos = [pcm[i:i + 3200] for i in range(0, len(pcm), 3200)]
            ok = await perguntar_e_esperar([{"tipo": "inicio"}, *pedacos, {"tipo": "fim"}])
            tocador.esperar()
            return 0 if ok else 1

        def ler_teclado():
            for linha in sys.stdin:
                loop.call_soon_threadsafe(comandos.put_nowait, linha.rstrip("\r\n"))
            loop.call_soon_threadsafe(comandos.put_nowait, "/sair")

        threading.Thread(target=ler_teclado, daemon=True).start()
        print("Conectado. Enter para falar (Enter de novo para mandar), ou digite uma pergunta. /nova, /sair")
        gravando = False
        while True:
            linha = await comandos.get()
            if linha.strip() == "/sair" or caiu.is_set():
                break
            if linha.strip() == "/nova":
                await ws.send(json.dumps({"tipo": "nova"}))
                print("  (conversa nova)")
                continue
            if linha.strip() and not gravando:
                tocador.parar()
                await ws.send(json.dumps({"tipo": "texto", "texto": linha.strip()}))
                continue
            if not gravando:
                tocador.parar()
                while not audio_mic.empty():
                    audio_mic.get_nowait()
                await ws.send(json.dumps({"tipo": "inicio"}))
                mic.abrir()
                gravando = True
                print("  ... gravando (Enter para mandar)")
            else:
                mic.fechar()
                await asyncio.sleep(0.15)  # deixa o último pedaço sair
                await ws.send(json.dumps({"tipo": "fim"}))
                gravando = False
        return 0
    finally:
        mic.fechar()
        enviador.cancel()
        await ws.close()
        recebedor.cancel()
        if opcoes.salvar and tocador.gravado:
            with wave.open(opcoes.salvar, "wb") as arquivo:
                arquivo.setnchannels(1)
                arquivo.setsampwidth(2)
                arquivo.setframerate(tocador.taxa_gravada)
                arquivo.writeframes(bytes(tocador.gravado))
            print("Resposta salva em %s" % opcoes.salvar)
        time.sleep(0.3)


def main():
    leitor = argparse.ArgumentParser(description="Cliente de voz do Jarvis para o PC.")
    leitor.add_argument("--servidor", default=os.environ.get("JARVIS_SERVIDOR", "127.0.0.1"),
                        help="IP do servidor (padrão: variável JARVIS_SERVIDOR ou 127.0.0.1)")
    leitor.add_argument("--porta", type=int, default=PORTA)
    leitor.add_argument("--token", help="JARVIS_VOZ_TOKEN do servidor (ou a variável de ambiente)")
    leitor.add_argument("--sala", default="pc", help="nome desta conversa (cada sala tem o seu histórico)")
    leitor.add_argument("--texto", help="uma pergunta digitada e sai")
    leitor.add_argument("--wav", help="manda este WAV como se fosse a sua fala e sai")
    leitor.add_argument("--salvar", help="guarda o áudio da resposta neste WAV")
    leitor.add_argument("--entrada", type=int, help="número do microfone (veja --listar-audio)")
    leitor.add_argument("--saida", type=int, help="número do alto-falante (veja --listar-audio)")
    leitor.add_argument("--listar-audio", action="store_true", help="mostra os aparelhos de áudio e sai")
    opcoes = leitor.parse_args()
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        sys.exit("Falta a biblioteca sounddevice (ou o PortAudio). Instale com: pip install sounddevice")
    if opcoes.listar_audio:
        print(sd.query_devices())
        return 0
    try:
        return asyncio.run(conversar(opcoes))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

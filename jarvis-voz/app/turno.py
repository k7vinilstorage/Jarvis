"""Uma vez de conversa: fala -> Whisper -> Hermes (streaming) -> frases -> Piper -> áudio para o cliente.

A voz começa na primeira frase pronta, enquanto o Hermes ainda escreve o resto. Quando o Hermes chama uma
ferramenta antes de dizer qualquer coisa, o Jarvis diz "Um momento." para o silêncio não parecer travamento.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app import audio, hermes, textos, voz
from app.config import Config
from app.sessoes import Sessoes

log = logging.getLogger("jarvis-voz")

LIMITE_RESPOSTA = 1500  # caracteres falados por resposta; o resto só aparece como texto
TAMANHO_QUADRO = 4096   # bytes de áudio por mensagem binária (cabe na memória do ESP32)
FOLGA_AUDIO = 2.0       # segundos de áudio mandados à frente do que o cliente já tocou


@dataclass
class Saida:
    """Como falar com o cliente: enviar_json(dict) e enviar_bytes(bytes), já serializados pelo servidor."""
    enviar_json: object
    enviar_bytes: object
    taxa: int | None = None       # taxa de saída pedida pelo cliente (None = a do Piper)
    formato: str = "s16le"        # s16le ou u8
    ativa: object = lambda: True  # False quando o cliente sumiu: para de gastar Hermes e Piper
    audio_aberto: bool = False    # entre um audio_inicio e o audio_fim


@dataclass
class Tempos:
    inicio: float = field(default_factory=time.monotonic)
    valores: dict = field(default_factory=dict)

    def marcar(self, nome: str, sobrescrever: bool = False) -> None:
        if sobrescrever or nome not in self.valores:
            self.valores[nome] = round(time.monotonic() - self.inicio, 2)


class Falador:
    """Sintetiza as frases em ordem e manda o áudio. Guarda a fala de frases fixas ("Um momento.")."""

    _cache: dict[tuple, tuple[voz.Formato, bytes]] = {}

    def __init__(self, config: Config, saida: Saida, tempos: Tempos, folga: float = FOLGA_AUDIO):
        self.config = config
        self.saida = saida
        self.tempos = tempos
        self.fila: asyncio.Queue = asyncio.Queue()
        self.audio_iniciado = False
        self.falado = 0
        self.limite_passou = False
        self.frases: list[str] = []
        self.erro = ""
        # O formato fica fixo durante a resposta: um "config" no meio só vale para a próxima
        self.taxa, self.formato = saida.taxa, saida.formato
        self.folga = folga
        self.relogio_audio = 0.0   # quando o primeiro áudio saiu
        self.segundos_enviados = 0.0

    async def _no_ritmo(self, segundos: float) -> None:
        """Manda o áudio no ritmo em que ele toca, com uns segundos de folga: um cliente lento (o ESP32) não
        entope a conexão, e os pings e um 'cancelar' continuam passando."""
        if not self.relogio_audio:
            self.relogio_audio = time.monotonic()
        adiantado = self.segundos_enviados - (time.monotonic() - self.relogio_audio)
        if adiantado > self.folga:
            await asyncio.sleep(adiantado - self.folga)
        self.segundos_enviados += segundos

    async def _enviar_audio(self, formato: voz.Formato, pcm: bytes) -> None:
        dados, taxa = audio.converter_saida(pcm, formato.taxa, formato.largura, formato.canais,
                                            self.taxa, self.formato)
        if not dados or not self.saida.ativa():
            return
        if not self.audio_iniciado:
            self.audio_iniciado = True
            self.tempos.marcar("primeiro_audio")
            await self.saida.enviar_json({"tipo": "estado", "estado": "falando"})
            await self.saida.enviar_json({"tipo": "audio_inicio", "taxa": taxa, "formato": self.formato,
                                          "canais": 1})
            self.saida.audio_aberto = True
        bytes_por_segundo = taxa * (1 if self.formato == "u8" else 2)
        for inicio in range(0, len(dados), TAMANHO_QUADRO):
            quadro = dados[inicio:inicio + TAMANHO_QUADRO]
            await self._no_ritmo(len(quadro) / bytes_por_segundo)
            if not self.saida.ativa():
                return
            await self.saida.enviar_bytes(quadro)

    async def _falar(self, texto: str, guardar: bool = False) -> None:
        chave = (self.config.piper_uri, self.config.piper_voz, texto)
        if guardar and chave in self._cache:
            await self._enviar_audio(*self._cache[chave])
            return
        partes, formato = [], None
        async for formato, pedaco in voz.sintetizar_pedacos(self.config.piper_uri, texto, self.config.piper_voz):
            if guardar:
                partes.append(pedaco)
            await self._enviar_audio(formato, pedaco)
        if guardar and formato:
            self._cache[chave] = (formato, b"".join(partes))

    async def rodar(self) -> None:
        while True:
            item = await self.fila.get()
            if item is None:
                break
            frase, fixa = item
            texto = textos.para_fala(frase)
            if not texto or not self.saida.ativa():
                continue
            if self.limite_passou or self.falado + len(texto) > LIMITE_RESPOSTA:
                self.limite_passou = True  # daqui em diante, só texto (sem pular trechos do meio)
                self.frases.append(texto)
                await self.saida.enviar_json({"tipo": "frase", "texto": texto, "falada": False})
                continue
            self.falado += len(texto)
            if not fixa:
                self.frases.append(texto)
            await self.saida.enviar_json({"tipo": "frase", "texto": texto, "falada": True})
            try:
                await self._falar(texto, guardar=fixa)
            except voz.ErroVoz as erro:
                self.erro = "piper: %s" % erro
                log.warning("Piper: %s", erro)
                await self.saida.enviar_json({"tipo": "erro", "mensagem": "não consegui gerar a fala: %s" % erro})
        if self.saida.audio_aberto:
            self.saida.audio_aberto = False
            await self.saida.enviar_json({"tipo": "audio_fim"})

    def dizer(self, frase: str, fixa: bool = False) -> None:
        self.fila.put_nowait((frase, fixa))

    def terminar(self) -> None:
        self.fila.put_nowait(None)


async def _ler_hermes(config, saida, mensagens, chave_sessao, falador, divisor, partes, resumo, tempos) -> None:
    """Lê a resposta do Hermes e manda as frases para o falador assim que ficam prontas."""
    disse_algo = False
    async for tipo, valor in hermes.conversar(config.hermes_url, config.hermes_chave, mensagens, chave_sessao):
        if not saida.ativa():
            return  # o cliente sumiu: sair daqui corta a conexão com o Hermes
        if tipo == "texto":
            partes.append(valor)
            if valor.strip():
                tempos.marcar("primeira_palavra")
            for frase in divisor.adicionar(valor):
                falador.dizer(frase)
                disse_algo = True
        elif tipo == "ferramenta":
            resumo["ferramentas"].append(valor)
            await saida.enviar_json({"tipo": "ferramenta", "nome": valor})
            # O que veio antes da ferramenta ("Vou verificar...") é uma frase completa
            for frase in divisor.terminar():
                falador.dizer(frase)
                disse_algo = True
            if not disse_algo:
                falador.dizer(textos.UM_MOMENTO, fixa=True)
                disse_algo = True
        elif tipo == "erro":
            resumo["erro"] = valor or "erro no Hermes"


async def responder(config: Config, sessoes: Sessoes, sala: str, saida: Saida, *, pcm: bytes | None = None,
                    texto: str | None = None, tempos: Tempos | None = None, vez: asyncio.Lock | None = None,
                    folga: float = FOLGA_AUDIO) -> dict:
    """Uma vez inteira. Devolve o resumo (pergunta, resposta, ferramentas, tempos, erro) que também vai no
    evento "fim". pcm: a fala (16 kHz, mono, 16 bits); texto: pergunta digitada (pula o Whisper).
    vez: trava do Ollama (um pedido por vez); fica presa só enquanto o Hermes escreve, não durante a fala."""
    tempos = tempos or Tempos()
    vez = vez or asyncio.Lock()
    falador = Falador(config, saida, tempos, folga)
    tarefa_falador = asyncio.create_task(falador.rodar())
    resumo = {"pergunta": "", "resposta": "", "ferramentas": [], "erro": ""}
    interrompido = False
    try:
        pergunta = (texto or "").strip()
        if pcm is not None:
            await saida.enviar_json({"tipo": "estado", "estado": "transcrevendo"})
            motivo = audio.motivo_sem_fala(pcm)
            if motivo is None:
                try:
                    bruto = await voz.transcrever(config.whisper_uri, pcm, config.idioma)
                except voz.ErroVoz as erro:
                    resumo["erro"] = "whisper: %s" % erro
                    falador.dizer(textos.FALHA_WHISPER, fixa=True)
                    return resumo
                finally:
                    tempos.marcar("stt")
                pergunta = textos.limpar_transcricao(bruto)
            if not pergunta:
                resumo["erro"] = "sem fala (%s)" % (motivo or "transcrição vazia")
                falador.dizer(textos.NAO_OUVI, fixa=True)
                return resumo
        resumo["pergunta"] = pergunta
        await saida.enviar_json({"tipo": "transcricao", "texto": pergunta})
        await saida.enviar_json({"tipo": "estado", "estado": "pensando"})

        mensagens = hermes.montar_mensagens(sessoes.historico(sala), pergunta)
        divisor = textos.DivisorDeFrases()
        partes: list[str] = []
        chave_sessao = "voz-%s" % sala if config.sessao_hermes else ""
        esperou = vez.locked()
        if esperou:
            await saida.enviar_json({"tipo": "estado", "estado": "aguardando"})
        async with vez:
            if esperou:
                await saida.enviar_json({"tipo": "estado", "estado": "pensando"})
            await _ler_hermes(config, saida, mensagens, chave_sessao, falador, divisor, partes, resumo, tempos)
        for frase in divisor.terminar():
            falador.dizer(frase)
        resposta = "".join(partes).strip()
        resumo["resposta"] = resposta
        if not resposta:
            falador.dizer(textos.FALHA_HERMES if resumo["erro"] else textos.RESPOSTA_VAZIA, fixa=True)
        else:
            sessoes.registrar(sala, pergunta, resposta)
        return resumo
    except asyncio.CancelledError:
        interrompido = True
        tarefa_falador.cancel()  # interrompido (o usuário começou a falar de novo): a voz para na hora
        raise
    finally:
        if not interrompido:
            falador.terminar()
            await tarefa_falador  # o que já está na fila ainda é falado
        if falador.erro and not resumo["erro"]:
            resumo["erro"] = falador.erro
        tempos.marcar("total", sobrescrever=True)
        resumo["tempos"] = dict(tempos.valores)

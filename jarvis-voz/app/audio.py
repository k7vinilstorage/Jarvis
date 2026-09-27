"""Áudio de entrada (PCM cru do ESP32 ou WAV) e de saída (WAV), só com a biblioteca padrão.

Internamente tudo vira PCM de 16 bits, little-endian, 16 kHz, mono: o formato que o Whisper usa.
"""
from __future__ import annotations

import io
import sys
import wave
from array import array

TAXA = 16000
LARGURA = 2  # bytes por amostra
CANAIS = 1
BYTES_POR_SEGUNDO = TAXA * LARGURA * CANAIS

MAX_SEGUNDOS = 30
MIN_SEGUNDOS = 0.3
PICO_MINIMO = 400  # de 32767 (cerca de -38 dBFS): abaixo disso é silêncio ou microfone mudo

# Conversões que não pedem um reamostrador de verdade: múltiplos inteiros de 16 kHz
TAXAS_ACEITAS = (16000, 32000, 48000)

# "audio/L16" pela RFC 2586 seria big-endian; aqui é little-endian, que é o que o ESP32 manda
TIPOS_PCM = {"audio/l16", "audio/pcm", "audio/x-raw", "application/octet-stream"}
TIPOS_WAV = {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}

DICA_CONVERSAO = "Converta com: ffmpeg -i entrada -ar 16000 -ac 1 -sample_fmt s16 saida.wav"


class FormatoInvalido(Exception):
    """Áudio em formato que a bridge não aceita (vira HTTP 415)."""


class AudioLongo(Exception):
    """Mais de MAX_SEGUNDOS de áudio (vira HTTP 413)."""


def tipo_de_conteudo(cabecalho: str) -> tuple[str, dict[str, str]]:
    """'audio/L16; rate=16000; channels=1' -> ('audio/l16', {'rate': '16000', 'channels': '1'})."""
    partes = [p.strip() for p in (cabecalho or "").split(";")]
    parametros = {}
    for parte in partes[1:]:
        chave, _, valor = parte.partition("=")
        if chave:
            parametros[chave.strip().lower()] = valor.strip().strip('"')
    return partes[0].lower(), parametros


def limite_do_corpo(cabecalho: str) -> int:
    """Quantos bytes ler no máximo. No WAV cabe o pior caso aceito (48 kHz estéreo) mais o cabeçalho."""
    tipo, _ = tipo_de_conteudo(cabecalho)
    if tipo in TIPOS_WAV or tipo not in TIPOS_PCM:
        return MAX_SEGUNDOS * max(TAXAS_ACEITAS) * LARGURA * 2 + 64 * 1024
    return MAX_SEGUNDOS * BYTES_POR_SEGUNDO


def ler_entrada(cabecalho: str, corpo: bytes) -> bytes:
    """Devolve o áudio como PCM 16 kHz mono de 16 bits, ou levanta FormatoInvalido / AudioLongo."""
    tipo, parametros = tipo_de_conteudo(cabecalho)
    if tipo in TIPOS_WAV or (corpo[:4] == b"RIFF" and corpo[8:12] == b"WAVE"):
        pcm = _ler_wav(corpo)
    elif tipo in TIPOS_PCM:
        pcm = _ler_pcm(corpo, parametros)
    else:
        raise FormatoInvalido("Content-Type %r não aceito. Use audio/L16; rate=16000; channels=1 "
                              "(PCM de 16 bits) ou audio/wav." % (cabecalho or ""))
    if duracao(pcm) > MAX_SEGUNDOS + 0.05:
        raise AudioLongo("áudio de %.1f s; o máximo é %d s" % (duracao(pcm), MAX_SEGUNDOS))
    return pcm


def _inteiro(valor: str, padrao: int, nome: str) -> int:
    if not valor:
        return padrao
    try:
        return int(valor)
    except ValueError:
        raise FormatoInvalido("parâmetro %s inválido: %r" % (nome, valor)) from None


def _ler_pcm(corpo: bytes, parametros: dict[str, str]) -> bytes:
    taxa = _inteiro(parametros.get("rate", ""), TAXA, "rate")
    canais = _inteiro(parametros.get("channels", ""), CANAIS, "channels")
    return _para_16k_mono(corpo, taxa, canais)


def _ler_wav(corpo: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(corpo), "rb") as arquivo:
            taxa, canais, largura = arquivo.getframerate(), arquivo.getnchannels(), arquivo.getsampwidth()
            quadros = arquivo.readframes(arquivo.getnframes())
    except (wave.Error, EOFError) as erro:
        raise FormatoInvalido("WAV inválido (%s). Precisa ser PCM de 16 bits." % erro) from None
    if largura != LARGURA:
        raise FormatoInvalido("WAV de %d bits; precisa ser 16 kHz, mono, 16 bits. %s" % (largura * 8, DICA_CONVERSAO))
    return _para_16k_mono(quadros, taxa, canais)


def _para_16k_mono(pcm: bytes, taxa: int, canais: int) -> bytes:
    """Estéreo vira mono pela média; 32 e 48 kHz viram 16 kHz pela média de 2 ou 3 amostras."""
    if taxa not in TAXAS_ACEITAS or canais not in (1, 2):
        raise FormatoInvalido("Áudio de %d Hz com %d canal(is); precisa ser 16 kHz, mono, 16 bits. %s"
                              % (taxa, canais, DICA_CONVERSAO))
    quadro = LARGURA * canais
    pcm = pcm[:len(pcm) - len(pcm) % quadro]  # descarta um quadro incompleto no fim
    if taxa == TAXA and canais == 1:
        return pcm
    amostras = _amostras(pcm)
    if canais == 2:
        amostras = array("h", [(e + d) >> 1 for e, d in zip(amostras[0::2], amostras[1::2])])
    fator = taxa // TAXA
    if fator == 2:
        amostras = array("h", [(a + b) >> 1 for a, b in zip(amostras[0::2], amostras[1::2])])
    elif fator == 3:
        amostras = array("h", [(a + b + c) // 3 for a, b, c in zip(amostras[0::3], amostras[1::3], amostras[2::3])])
    return _bytes(amostras)


def _amostras(pcm: bytes) -> array:
    amostras = array("h")
    amostras.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        amostras.byteswap()
    return amostras


def _bytes(amostras: array) -> bytes:
    if sys.byteorder == "big":
        amostras = array("h", amostras)
        amostras.byteswap()
    return amostras.tobytes()


def duracao(pcm: bytes) -> float:
    return len(pcm) / BYTES_POR_SEGUNDO


def pico(pcm: bytes) -> int:
    amostras = _amostras(pcm)
    return max(max(amostras), -min(amostras)) if amostras else 0


def motivo_sem_fala(pcm: bytes) -> str | None:
    """'curto' ou 'mudo' quando nem vale a pena mandar ao Whisper; None quando pode ter fala."""
    if duracao(pcm) < MIN_SEGUNDOS:
        return "curto"
    if pico(pcm) < PICO_MINIMO:
        return "mudo"
    return None


def wav(pcm: bytes, taxa: int, largura: int = LARGURA, canais: int = CANAIS) -> bytes:
    """Monta um WAV simples (cabeçalho de 44 bytes, que o ESP32 lê sem complicação)."""
    saida = io.BytesIO()
    with wave.open(saida, "wb") as arquivo:
        arquivo.setnchannels(canais)
        arquivo.setsampwidth(largura)
        arquivo.setframerate(taxa)
        arquivo.writeframes(pcm)
    return saida.getvalue()


# ---------------------------------------------------------------- saída para o cliente

FORMATOS_SAIDA = ("s16le", "u8")  # u8: 8 bits sem sinal, para o DAC interno do ESP32
TAXAS_SAIDA = range(8000, 48001)


def reamostrar(pcm: bytes, de: int, para: int) -> bytes:
    """PCM mono de 16 bits de uma taxa para outra, por interpolação linear (suficiente para voz)."""
    if de == para or not pcm:
        return pcm
    amostras = _amostras(pcm)
    n = len(amostras)
    if n < 2:
        return pcm
    total = max(1, int(n * para / de))
    passo = (n - 1) / max(1, total - 1)
    saida = array("h", bytes(2 * total))
    for i in range(total):
        posicao = i * passo
        j = int(posicao)
        fracao = posicao - j
        a = amostras[j]
        b = amostras[j + 1] if j + 1 < n else a
        saida[i] = int(a + (b - a) * fracao)
    return _bytes(saida)


def para_mono(pcm: bytes, canais: int) -> bytes:
    if canais == 1:
        return pcm
    amostras = _amostras(pcm)
    mono = array("h", [sum(amostras[i:i + canais]) // canais for i in range(0, len(amostras) - canais + 1, canais)])
    return _bytes(mono)


def para_u8(pcm: bytes) -> bytes:
    """16 bits com sinal -> 8 bits sem sinal (o que o DAC do ESP32 toca direto)."""
    return bytes((s >> 8) + 128 for s in _amostras(pcm))


def converter_saida(pcm: bytes, taxa: int, largura: int, canais: int, taxa_saida: int | None,
                    formato_saida: str) -> tuple[bytes, int]:
    """Áudio do Piper -> formato pedido pelo cliente. Devolve (dados, taxa final)."""
    if largura != LARGURA:
        raise FormatoInvalido("o Piper mandou áudio de %d bits; esperado 16" % (largura * 8))
    pcm = para_mono(pcm, canais)
    destino = taxa_saida or taxa
    pcm = reamostrar(pcm, taxa, destino)
    if formato_saida == "u8":
        return para_u8(pcm), destino
    return pcm, destino


def silencio(segundos: float, taxa: int = TAXA) -> bytes:
    return bytes(int(segundos * taxa) * LARGURA)

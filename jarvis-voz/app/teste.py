"""Teste de ponta a ponta da voz, sem microfone: roda dentro do contêiner jarvis-voz.

    docker compose exec -T jarvis-voz python -m app.teste
    docker compose exec -T jarvis-voz python -m app.teste --pergunta "Que horas são?" --pergunta "Vai chover amanhã?"

Para cada pergunta: o Piper fala a pergunta, esse áudio vai pela ponte como se fosse você falando (Whisper,
Hermes, Piper) e os tempos são medidos a partir do fim da fala. Sai um relatório em markdown (o
scripts/voz-teste.sh guarda em medicoes/).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import unicodedata

import websockets

from app import audio
from app.config import PORTA

PERGUNTAS = ["Que horas são?", "Como vai estar o tempo amanhã?", "Tenho atividades para entregar esta semana?"]
SALA = "teste-voz"


def comparavel(texto: str) -> list[str]:
    sem = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return re.findall(r"\w+", sem)


def acerto(pergunta: str, transcricao: str) -> float:
    esperadas, ouvidas = comparavel(pergunta), set(comparavel(transcricao))
    return sum(1 for p in esperadas if p in ouvidas) / max(1, len(esperadas))


class Cliente:
    def __init__(self, ws):
        self.ws = ws

    async def enviar(self, dados):
        await self.ws.send(dados if isinstance(dados, bytes) else json.dumps(dados))

    async def ate_o_fim(self, tempo: float = 180) -> tuple[list[dict], bytes, dict]:
        eventos, dados, formato = [], bytearray(), {}
        prazo = time.monotonic() + tempo
        while True:
            mensagem = await asyncio.wait_for(self.ws.recv(), max(1, prazo - time.monotonic()))
            if isinstance(mensagem, bytes):
                dados += mensagem
                continue
            evento = json.loads(mensagem)
            eventos.append(evento)
            if evento["tipo"] == "audio_inicio":
                formato = evento
            if evento["tipo"] == "fim":
                return eventos, bytes(dados), formato


def segundos(valor) -> str:
    return "-" if valor is None else ("%.1f s" % valor).replace(".", ",")


async def rodar(perguntas: list[str], url: str, aquecer: bool) -> str:
    linhas_tabela, detalhes = [], []
    async with websockets.connect(url, max_size=None) as ws:
        cliente = Cliente(ws)
        await ws.recv()  # pronto
        if aquecer:  # carrega o modelo na GPU; não entra na conta
            await cliente.enviar({"tipo": "nova"})
            await cliente.enviar({"tipo": "texto", "texto": "Responda só com a palavra: pronto."})
            await cliente.ate_o_fim()
        for pergunta in perguntas:
            await cliente.enviar({"tipo": "nova"})
            # 1. A pergunta falada pelo próprio Piper
            await cliente.enviar({"tipo": "falar", "texto": pergunta})
            _, voz_pergunta, formato = await cliente.ate_o_fim()
            if not voz_pergunta:
                detalhes.append("### %s\n\nO Piper não gerou a fala da pergunta; confira o contêiner piper." % pergunta)
                continue
            pcm = audio.reamostrar(voz_pergunta, formato.get("taxa", 22050), audio.TAXA)
            # 2. Como se fosse você falando
            await cliente.enviar({"tipo": "inicio"})
            for inicio in range(0, len(pcm), 3200):
                await cliente.enviar(pcm[inicio:inicio + 3200])
            await cliente.enviar({"tipo": "fim"})
            eventos, resposta_audio, formato_resposta = await cliente.ate_o_fim()
            fim = eventos[-1]
            tempos = fim.get("tempos") or {}
            taxa = formato_resposta.get("taxa") or 22050
            duracao_resposta = len(resposta_audio) / 2 / taxa if resposta_audio else 0
            nota = acerto(pergunta, fim.get("pergunta", ""))
            ferramentas = ", ".join(fim.get("ferramentas") or []) or "-"
            linhas_tabela.append("| %s | %s | %.0f%% | %s | %s | %s | %s | %s | %s |" % (
                pergunta, fim.get("pergunta") or "(nada)", nota * 100, segundos(tempos.get("stt")),
                segundos(tempos.get("primeira_palavra")), segundos(tempos.get("primeiro_audio")),
                segundos(tempos.get("total")), segundos(duracao_resposta), ferramentas))
            frases = [e["texto"] for e in eventos if e["tipo"] == "frase"]
            detalhes.append("### %s\n\n- Ouvido: %s\n- Falado: %s\n- Ferramentas: %s%s" % (
                pergunta, fim.get("pergunta") or "(nada)", " ".join(frases) or "(nada)", ferramentas,
                "\n- Erro: %s" % fim["erro"] if fim.get("erro") else ""))
    agora = time.strftime("%d/%m/%Y %H:%M")
    return "\n".join([
        "# Teste de voz do Jarvis: %s" % agora, "",
        "Gerado por `scripts/voz-teste.sh`. A pergunta é falada pelo Piper e entra na ponte como se fosse você. "
        "Os tempos contam a partir do fim da fala: Whisper, primeira palavra do Hermes, primeiro áudio "
        "da resposta (quando você começa a ouvir) e o fim do envio do áudio.", "",
        "| Pergunta | Ouvido | Acerto | Whisper | 1ª palavra | 1º áudio | Tudo enviado | Duração da fala | Ferramentas |",
        "|---|---|---|---|---|---|---|---|---|",
        *linhas_tabela, "", "## Respostas", "", "\n\n".join(detalhes), ""])


def main(argumentos=None) -> int:
    leitor = argparse.ArgumentParser(description="Teste de ponta a ponta da voz, sem microfone.")
    leitor.add_argument("--pergunta", action="append", help="pergunta (pode repetir); padrão: três perguntas")
    leitor.add_argument("--sem-aquecer", action="store_true", help="não carrega o modelo antes")
    opcoes = leitor.parse_args(argumentos)
    token = os.environ.get("JARVIS_VOZ_TOKEN", "")
    url = "ws://127.0.0.1:%s/voz?token=%s&sala=%s" % (os.environ.get("PORTA", PORTA), token, SALA)
    try:
        print(asyncio.run(rodar(opcoes.pergunta or PERGUNTAS, url, not opcoes.sem_aquecer)))
    except (OSError, websockets.exceptions.WebSocketException, asyncio.TimeoutError) as erro:
        print("Não consegui falar com a ponte de voz: %s" % type(erro).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

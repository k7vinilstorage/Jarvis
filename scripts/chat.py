#!/usr/bin/env python3
"""Chat com o Jarvis no terminal, pela API do Hermes, com os tempos e as ferramentas de cada resposta.

Uso, na pasta do projeto:
    python3 scripts/chat.py                              conversa (várias perguntas seguidas)
    python3 scripts/chat.py --pergunta "Que horas são?"  uma pergunta só

Comandos durante a conversa: /nova, /historico, /tempo, /ajuda e /sair (ou Ctrl+D).
Ctrl+C durante uma resposta cancela só aquela resposta; o chat continua.
O chat manda o histórico inteiro a cada pergunta (a API não guarda a conversa), então o Jarvis lembra
o que foi dito até um /nova. Só usa a biblioteca padrão do Python (3.8 ou mais novo).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # não deixa __pycache__ na pasta scripts
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_cliente as jc  # noqa: E402

try:
    import readline  # noqa: F401  (só de importar, o input() ganha setas, histórico e edição da linha)
except ImportError:
    pass

AJUDA = """Comandos:
  /nova       começa uma conversa nova (o Jarvis esquece o que foi dito aqui)
  /historico  mostra a conversa até agora
  /tempo      liga ou desliga os tempos depois de cada resposta
  /ajuda      mostra esta lista
  /sair       sai (Ctrl+D também)
Ctrl+C durante uma resposta cancela só aquela resposta."""


class Chat:
    def __init__(self, url: str, chave: str, timeout: float):
        self.url = url
        self.chave = chave
        self.timeout = timeout
        self.historico = []
        self.mostrar_tempo = True
        self.estilo = jc.Estilo()

    def escrever(self, texto: str) -> None:
        sys.stdout.write(texto)
        sys.stdout.flush()

    def perguntar(self, pergunta: str) -> dict:
        e = self.estilo
        self.historico.append({"role": "user", "content": pergunta})
        estado = {"escreveu": False}

        def ao_texto(pedaco: str) -> None:
            if not estado["escreveu"]:
                pedaco = pedaco.lstrip()
                if not pedaco:
                    return
                estado["escreveu"] = True
            self.escrever(pedaco)

        def ao_ferramenta(nome: str) -> None:
            if not estado["escreveu"]:
                self.escrever(e.fraco("[%s] " % jc.nome_curto(nome)))

        self.escrever(e.ciano("Jarvis: "))
        r = jc.conversar(self.url, self.chave, self.historico, timeout=self.timeout,
                         ao_texto=ao_texto, ao_ferramenta=ao_ferramenta)
        self.escrever("\n")

        if r.get("cancelado"):
            self.historico.pop()
            print(e.fraco("(resposta cancelada; a pergunta saiu do histórico)"))
        elif r.get("erro") and not r.get("texto"):
            self.historico.pop()
            print(e.vermelho("Erro: ") + r["erro"])
        elif not r.get("texto"):
            self.historico.pop()
            print(e.amarelo("(o Jarvis terminou sem responder em texto; na voz, isso vira silêncio)"))
        else:
            self.historico.append({"role": "assistant", "content": r["texto"]})
            if r.get("erro"):
                print(e.amarelo("aviso: ") + "erro no fim da resposta: " + r["erro"])
        if not r.get("cancelado"):
            print(e.fraco("  " + self.info(r)))
        return r

    def info(self, r: dict) -> str:
        nomes = []
        for f in r.get("ferramentas") or []:
            curto = jc.nome_curto(f)
            if curto not in nomes:
                nomes.append(curto)
        partes = ["ferramentas: " + (", ".join(nomes) if nomes else "nenhuma")]
        if self.mostrar_tempo:
            partes.append("1ª palavra %s" % jc.seg(r.get("t_primeira_palavra")))
            partes.append("total %s" % jc.seg(r.get("t_total")))
        if r.get("raciocinio_chars"):
            partes.append("raciocínio: %d caracteres" % r["raciocinio_chars"])
        md = jc.detectar_markdown(r.get("texto") or "")
        if md:
            partes.append("formato ruim para voz: " + ", ".join(md))
        return " · ".join(partes)

    def mostrar_historico(self) -> None:
        if not self.historico:
            print("(a conversa está vazia)")
            return
        print("Conversa atual (%d mensagens):" % len(self.historico))
        for m in self.historico:
            quem = "Você" if m["role"] == "user" else "Jarvis"
            linhas = (m["content"] or "").splitlines() or [""]
            print("  %s: %s" % (quem, linhas[0]))
            for l in linhas[1:]:
                print("  %s  %s" % (" " * len(quem), l))

    def comando(self, linha: str) -> bool:
        """Trata um /comando. Devolve False para sair."""
        cmd = jc.normalizar(linha.split()[0])
        if cmd in ("/sair", "/exit", "/quit"):
            return False
        if cmd == "/nova":
            self.historico = []
            print("Conversa nova: o Jarvis não vê mais as mensagens anteriores.")
        elif cmd == "/historico":
            self.mostrar_historico()
        elif cmd == "/tempo":
            self.mostrar_tempo = not self.mostrar_tempo
            print("Tempos %s." % ("ligados" if self.mostrar_tempo else "desligados"))
        elif cmd in ("/ajuda", "/help", "/?"):
            print(AJUDA)
        else:
            print("Comando desconhecido: %s. Digite /ajuda para ver os comandos." % linha.split()[0])
        return True

    def laco(self) -> None:
        interativo = sys.stdin.isatty()
        while True:
            try:
                linha = input("Você: ")
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                print(self.estilo.fraco("(para sair, use /sair ou Ctrl+D)"))
                continue
            linha = linha.strip()
            if not interativo:
                print(linha)  # entrada vinda de arquivo: mostra a pergunta para a conversa ficar legível
            if not linha:
                continue
            if linha.startswith("/"):
                if not self.comando(linha):
                    break
                continue
            try:
                self.perguntar(linha)
            except KeyboardInterrupt:
                print(self.estilo.fraco("\n(cancelado)"))
            print()


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Chat com o Jarvis no terminal, pela API do Hermes.")
    p.add_argument("--pergunta", help="faz uma pergunta só, mostra a resposta e sai")
    p.add_argument("--timeout", type=float, default=300.0, help="limite por resposta, em segundos (padrão 300)")
    p.add_argument("--hermes", default=jc.URL_HERMES, help=argparse.SUPPRESS)
    p.add_argument("--raiz", default=str(jc.RAIZ), help=argparse.SUPPRESS)
    a = p.parse_args()

    raiz = Path(a.raiz).resolve()
    env = jc.ler_env(raiz / ".env")
    if not env:
        print("Aviso: não encontrei %s. Rode o script de dentro da pasta do projeto." % (raiz / ".env"))
    chave = jc.chave_api(env)
    url = a.hermes.rstrip("/")
    problema = jc.verificar_hermes(url, chave)
    if problema:
        print(problema)
        return 1

    chat = Chat(url, chave, a.timeout)
    if a.pergunta is not None:
        pergunta = a.pergunta.strip()
        if not pergunta:
            print("A pergunta está vazia.")
            return 1
        print("Você: " + pergunta)
        r = chat.perguntar(pergunta)
        return 0 if r.get("texto") and not r.get("cancelado") else 1

    print(chat.estilo.negrito("Chat com o Jarvis") + chat.estilo.fraco(
        " (Hermes em %s). /ajuda mostra os comandos; /sair ou Ctrl+D sai." % url))
    print()
    chat.laco()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Apaga da memória do Hermes as anotações que contêm um texto.

Uso, na pasta do projeto:
    python3 scripts/memoria-remover.py SEU_NOME          mostra o que vai sair e pede confirmação
    python3 scripts/memoria-remover.py SEU_NOME --sim    apaga sem perguntar

Mexe em data/hermes/memories/MEMORY.md e USER.md. As anotações são separadas por uma linha com "§".
"""
import argparse
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent.parent / "data" / "hermes" / "memories"
SEPARADOR = "\n§\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Apaga anotações da memória do Hermes que contêm um texto.")
    p.add_argument("texto", help="texto a procurar (maiúsculas e minúsculas tanto faz)")
    p.add_argument("--sim", action="store_true", help="apaga sem pedir confirmação")
    a = p.parse_args()
    alvo = a.texto.lower()

    mudancas = []
    for nome in ("MEMORY.md", "USER.md"):
        arquivo = PASTA / nome
        if not arquivo.exists():
            continue
        entradas = [e.strip() for e in arquivo.read_text(encoding="utf-8").split(SEPARADOR) if e.strip()]
        saem = [e for e in entradas if alvo in e.lower()]
        if saem:
            mudancas.append((arquivo, [e for e in entradas if alvo not in e.lower()], saem))

    if not mudancas:
        print("Nenhuma anotação com \"%s\" em %s." % (a.texto, PASTA))
        return 0
    for arquivo, _, saem in mudancas:
        print("%s:" % arquivo.name)
        for e in saem:
            print("  - " + e.replace("\n", " "))
    if not a.sim:
        if not sys.stdin.isatty() or input("Apagar essas anotações? [s/N] ").strip().lower() not in ("s", "sim"):
            print("Nada foi apagado.")
            return 1
    for arquivo, ficam, _ in mudancas:
        arquivo.write_text(SEPARADOR.join(ficam), encoding="utf-8")
    print("Pronto. A próxima pergunta ao Jarvis já usa a memória nova.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

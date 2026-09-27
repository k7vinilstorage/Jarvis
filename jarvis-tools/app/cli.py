"""Testa as ferramentas direto, sem o modelo.

    python -m app.cli                          # lista as ferramentas e os parâmetros
    python -m app.cli recursos                 # JSON com o que está configurado
    python -m app.cli clima "Londrina, PR" amanhã
    python -m app.cli moodle_prazos semana
    python -m app.cli moodle_conteudo tcc "data da defesa"
    python -m app.cli moodle_nomes             # nomes do Moodle -> nome falado e siglas
    python -m app.cli agenda hoje
    python -m app.cli emails recentes "" copel
    python -m app.cli ler_email "fatura da copel"
    python -m app.cli buscar "ubuntu 26.04"
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
import typing

from app import config, ferramentas

VERDADEIRO = {"true", "1", "sim", "s", "yes", "y", "verdadeiro"}
FALSO = {"false", "0", "nao", "não", "n", "no", "falso", ""}


def _tipo(parametro: inspect.Parameter):
    tipo = parametro.annotation
    if typing.get_origin(tipo) is typing.Annotated:
        tipo = typing.get_args(tipo)[0]
    return tipo


def converter(valor: str, parametro: inspect.Parameter):
    tipo = _tipo(parametro)
    if tipo is bool:
        texto = valor.strip().lower()
        if texto in VERDADEIRO:
            return True
        if texto in FALSO:
            return False
        raise ValueError("%s espera true ou false, não %r" % (parametro.name, valor))
    if tipo is int:
        try:
            return int(valor)
        except ValueError:
            raise ValueError("%s espera um número inteiro, não %r" % (parametro.name, valor)) from None
    return valor


def assinatura(ferramenta: ferramentas.Ferramenta) -> inspect.Signature:
    return inspect.signature(ferramenta.funcao, eval_str=True)


def listar() -> str:
    recursos = config.recursos()
    linhas = ["Ferramentas (python -m app.cli <ferramenta> [argumentos na ordem]):"]
    for ferramenta in ferramentas.FERRAMENTAS:
        partes = []
        for nome, parametro in assinatura(ferramenta).parameters.items():
            tipo = _tipo(parametro).__name__
            if parametro.default is inspect.Parameter.empty:
                partes.append("%s: %s" % (nome, tipo))
            else:
                partes.append("%s: %s = %r" % (nome, tipo, parametro.default))
        estado = "" if ferramentas.ativa(ferramenta, recursos) else "  [não configurada: fica fora do MCP]"
        linhas.append("  %s(%s)%s" % (ferramenta.nome, ", ".join(partes), estado))
        linhas.append("      %s" % ferramentas.descricao(ferramenta, recursos))
    linhas.append("  recursos  -> JSON com o que está configurado")
    linhas.append("  moodle_nomes  -> nomes das disciplinas no Moodle, o nome falado e as siglas")
    return "\n".join(linhas)


def main(argumentos: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argumentos is None else argumentos
    if not argumentos or argumentos[0] in ("-h", "--help", "ajuda"):
        print(listar())
        return 0
    nome, valores = argumentos[0], argumentos[1:]
    if nome == "recursos":
        print(json.dumps(config.recursos(), ensure_ascii=False))
        return 0
    if nome == "moodle_nomes":  # diagnóstico: nomes do Moodle -> nome falado e siglas
        from app import moodle
        print(asyncio.run(moodle.nomes_brutos()))
        return 0
    ferramenta = ferramentas.POR_NOME.get(nome)
    if ferramenta is None:
        print("Ferramenta desconhecida: %s\n\n%s" % (nome, listar()), file=sys.stderr)
        return 2
    parametros = list(assinatura(ferramenta).parameters.values())
    if len(valores) > len(parametros):
        print("%s aceita no máximo %d argumento(s)." % (nome, len(parametros)), file=sys.stderr)
        return 2
    try:
        chamada = {p.name: converter(v, p) for p, v in zip(parametros, valores)}
    except ValueError as erro:
        print(erro, file=sys.stderr)
        return 2
    faltando = [p.name for p in parametros[len(valores):] if p.default is inspect.Parameter.empty]
    if faltando:
        print("Falta: %s" % ", ".join(faltando), file=sys.stderr)
        return 2
    print(asyncio.run(ferramentas.com_registro(ferramenta)(**chamada)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Configuração lida do ambiente e da pasta de dados ($DADOS): o que está ligado e onde ficam os arquivos."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("jarvis-tools")

ROTULO_VALIDO = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")  # pessoal, faculdade, trabalho-2


def dados() -> Path:
    """Pasta persistente (volume do usuário); o único lugar gravável do contêiner."""
    return Path(os.environ.get("DADOS") or "/data")


def arquivo_moodle() -> Path:
    return dados() / "moodle.json"


def pasta_google() -> Path:
    return dados() / "google"


def arquivo_cliente_google() -> Path:
    return pasta_google() / "cliente.json"


def arquivo_conta_google(rotulo: str) -> Path:
    return pasta_google() / (rotulo + ".json")


def ler_json(caminho: Path) -> dict | None:
    """O JSON do arquivo, ou None se não existe, não dá para ler ou está estragado."""
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            conteudo = json.load(arquivo)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as erro:
        log.warning("não consegui ler %s: %s", caminho, type(erro).__name__)
        return None
    return conteudo if isinstance(conteudo, dict) else None


def salvar_json_privado(caminho: Path, conteudo: dict) -> None:
    """Grava com permissão 0600, trocando o arquivo de uma vez (nunca fica meio escrito)."""
    caminho.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporario = caminho.with_name("." + caminho.name + ".novo")
    descritor = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
            json.dump(conteudo, arquivo, ensure_ascii=False, indent=2)
        os.chmod(temporario, 0o600)
        os.replace(temporario, caminho)
    except BaseException:
        temporario.unlink(missing_ok=True)
        raise


def moodle_url() -> str:
    return (os.environ.get("MOODLE_URL") or "").strip().rstrip("/")


def credenciais_moodle() -> dict | None:
    conteudo = ler_json(arquivo_moodle())
    if conteudo and conteudo.get("token") and conteudo.get("url"):
        return conteudo
    return None


def moodle_configurado() -> bool:
    return credenciais_moodle() is not None


def contas_google() -> list[str]:
    """Rótulos das contas Google autorizadas, em ordem alfabética."""
    pasta = pasta_google()
    if not pasta.is_dir():
        return []
    rotulos = []
    for caminho in pasta.glob("*.json"):
        rotulo = caminho.stem
        if rotulo == "cliente" or not ROTULO_VALIDO.match(rotulo):
            continue
        conteudo = ler_json(caminho)
        if conteudo and conteudo.get("refresh_token"):
            rotulos.append(rotulo)
    return sorted(rotulos)


def conta_google_padrao() -> str:
    """GOOGLE_CONTA_PADRAO se existir; senão a primeira em ordem alfabética ('' se não há nenhuma)."""
    contas = contas_google()
    escolhida = (os.environ.get("GOOGLE_CONTA_PADRAO") or "").strip().lower()
    if escolhida in contas:
        return escolhida
    return contas[0] if contas else ""


def searxng_url() -> str:
    return (os.environ.get("SEARXNG_URL") or "").strip().rstrip("/")


def busca_configurada() -> bool:
    return bool(searxng_url())


def recursos() -> dict:
    return {"moodle": moodle_configurado(), "google": contas_google(), "busca": busca_configurada()}

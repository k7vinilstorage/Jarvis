"""Conecta o Jarvis ao Moodle: pede usuário e senha uma vez, guarda só o token em $DADOS/moodle.json.

Uso (no servidor):
    docker compose exec -it jarvis-tools python -m app.moodle_login
    docker compose exec -it jarvis-tools python -m app.moodle_login --remover
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app import config, moodle
from app.rede import ErroRede, postar_form

REINICIE = "Reinicie: docker compose restart jarvis-tools hermes"

ERROS_LOGIN = {
    "invalidlogin": "Usuário ou senha incorretos.",
    "usernotconfirmed": "Sua conta no Moodle ainda não foi confirmada.",
    "passwordisexpired": "Sua senha do Moodle expirou. Troque a senha pelo site e tente de novo.",
    "servicenotavailable": ("Este Moodle não permite o acesso pelo aplicativo móvel (serviço moodle_mobile_app "
                            "desativado). Só o administrador do Moodle pode ativar."),
    "enablewsdescription": "Este Moodle não permite o acesso pelo aplicativo móvel.",
    "sitemaintenance": "O Moodle está em manutenção. Tente mais tarde.",
    "restoredaccountresetpassword": "Sua conta foi restaurada; redefina a senha pelo site antes.",
    "noguest": "A conta de visitante não serve; use o seu usuário.",
}


def remover() -> int:
    caminho = config.arquivo_moodle()
    if not caminho.exists():
        print("Não há acesso ao Moodle salvo.")
        return 0
    caminho.unlink()
    print("Acesso ao Moodle removido deste servidor.")
    print("Para invalidar o token no próprio Moodle, apague a chave do aplicativo móvel em "
          "Preferências > Chaves de segurança.")
    print(REINICIE)
    return 0


async def conectar(url: str, usuario: str, senha: str) -> int:
    try:
        status, resposta = await postar_form(url + "/login/token.php",
                                             {"username": usuario, "password": senha,
                                              "service": "moodle_mobile_app"})
    except ErroRede as erro:
        print("Não consegui falar com o Moodle em %s: %s." % (url, erro))
        return 1
    if not isinstance(resposta, dict):
        print("O Moodle respondeu de um jeito inesperado (status %d). Confira MOODLE_URL (%s)." % (status, url))
        return 1
    if not resposta.get("token"):
        codigo = str(resposta.get("errorcode") or "")
        mensagem = ERROS_LOGIN.get(codigo) or "O Moodle recusou o login: %s" % (
            resposta.get("error") or codigo or "motivo desconhecido")
        print(mensagem)
        if codigo == "invalidlogin":
            print("Se a faculdade usa login único (SSO, gov.br etc.), este método pode não funcionar.")
        return 1

    credenciais = {"url": url, "token": resposta["token"]}
    try:
        info = await moodle.chamar("core_webservice_get_site_info", credenciais, usar_cache=False)
    except moodle.ErroMoodle as erro:
        print("O login funcionou, mas o teste falhou: %s." % erro)
        return 1
    nome = str(info.get("fullname") or usuario)
    try:
        config.salvar_json_privado(config.arquivo_moodle(), {
            "url": url, "token": resposta["token"], "userid": info.get("userid"), "nome": nome})
    except OSError as erro:
        print("Não consegui gravar %s (%s). Confira se a pasta de dados existe e se o usuário do contêiner "
              "pode escrever nela." % (config.arquivo_moodle(), erro.strerror or type(erro).__name__))
        return 1
    print("Conectado como %s." % nome)
    try:
        disciplinas = await moodle.lista_disciplinas(credenciais)
        print("Disciplinas em andamento: %d%s" % (len(disciplinas),
                                                  (" (" + ", ".join(disciplinas) + ")") if disciplinas else ""))
    except moodle.ErroMoodle as erro:
        print("Não consegui listar as disciplinas agora: %s." % erro)
    print("Acesso salvo em %s (a senha não foi guardada)." % config.arquivo_moodle())
    print(REINICIE)
    return 0


def main(argumentos: list[str] | None = None) -> int:
    leitor = argparse.ArgumentParser(prog="python -m app.moodle_login",
                                     description="Conecta o Jarvis ao Moodle da faculdade.")
    leitor.add_argument("--remover", action="store_true", help="apaga o acesso salvo")
    opcoes = leitor.parse_args(argumentos)
    if opcoes.remover:
        return remover()
    url = config.moodle_url()
    if not url:
        print("Defina MOODLE_URL no .env (por exemplo https://moodle.utfpr.edu.br) e recrie o contêiner.")
        return 1
    if not url.startswith("https://"):
        print("Aviso: MOODLE_URL não usa https; a senha iria sem criptografia.")
        if input("Continuar mesmo assim? (s/N) ").strip().lower() not in ("s", "sim"):
            return 1
    print("Moodle: %s" % url)
    try:
        usuario = input("Usuário do Moodle: ").strip()
        senha = getpass.getpass("Senha (não aparece enquanto digita; não será guardada): ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        return 1
    if not usuario or not senha:
        print("Usuário e senha são obrigatórios.")
        return 1
    return asyncio.run(conectar(url, usuario, senha))


if __name__ == "__main__":
    sys.exit(main())

"""Autoriza uma conta Google (Agenda e Gmail) no Jarvis, sem navegador no servidor.

Uso (no servidor):
    docker compose exec -it jarvis-tools python -m app.google_login pessoal
    docker compose exec -it jarvis-tools python -m app.google_login --listar
    docker compose exec -it jarvis-tools python -m app.google_login --remover pessoal

Antes: crie no Google Cloud um cliente OAuth do tipo "App para computador" (Desktop app), baixe o JSON e
salve como $DADOS/google/cliente.json.
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import urllib.parse

from app import config, google_auth
from app.rede import ErroRede, obter_com_cabecalhos, postar_form

REINICIE = "Reinicie: docker compose restart jarvis-tools hermes"
URL_TESTE_AGENDA = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
URL_TESTE_GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


def listar() -> int:
    contas = google_auth.todas_as_contas()
    if not contas:
        print("Nenhuma conta Google autorizada.")
        return 0
    padrao = config.conta_google_padrao()
    for conta in contas:
        print("%s: %s%s" % (conta["rotulo"], conta.get("email") or "e-mail desconhecido",
                            " (padrão para criar eventos)" if conta["rotulo"] == padrao else ""))
    return 0


def remover(rotulo: str) -> int:
    conta = google_auth.carregar_conta(rotulo)
    caminho = config.arquivo_conta_google(rotulo)
    if not conta and not caminho.exists():
        print("Não há conta '%s' salva." % rotulo)
        return 1
    if conta and asyncio.run(google_auth.revogar(conta["refresh_token"])):
        print("Acesso revogado no Google.")
    else:
        print("Não consegui revogar no Google; se quiser, remova o acesso em myaccount.google.com/permissions.")
    caminho.unlink(missing_ok=True)
    print("Conta '%s' removida deste servidor." % rotulo)
    print(REINICIE)
    return 0


def ler_resposta(texto: str, estado: str) -> str:
    """Código de autorização a partir do endereço colado (ou do próprio código). Levanta ValueError."""
    texto = texto.strip().strip('"').strip("'")
    if not texto:
        raise ValueError("Nada foi colado.")
    if "code=" in texto or "error=" in texto or texto.startswith("http"):
        consulta = urllib.parse.urlsplit(texto).query if "?" in texto else texto
        valores = urllib.parse.parse_qs(consulta)
        if "error" in valores:
            erro = valores["error"][0]
            if erro == "access_denied":
                raise ValueError("O acesso foi negado na tela do Google.")
            raise ValueError("O Google devolveu o erro %s." % erro)
        if "state" in valores and valores["state"][0] != estado:
            raise ValueError("Esse endereço é de outra tentativa (state diferente). Rode o comando de novo.")
        if "code" not in valores:
            raise ValueError("Não achei o código no endereço colado.")
        return valores["code"][0]
    return texto


def escopos_faltando(concedidos: str) -> list[str]:
    lista = set((concedidos or "").split())
    return [nome for escopo, nome in google_auth.NOMES_ESCOPOS.items()
            if escopo not in lista and escopo != "email"]


async def trocar_codigo(codigo: str, verificador: str) -> tuple[int, dict]:
    client_id, client_secret = google_auth.ler_cliente()
    campos = {"code": codigo, "client_id": client_id, "redirect_uri": google_auth.REDIRECIONAMENTO,
              "grant_type": "authorization_code", "code_verifier": verificador}
    if client_secret:
        campos["client_secret"] = client_secret
    status, resposta = await postar_form(google_auth.URL_TOKEN, campos)
    return status, resposta if isinstance(resposta, dict) else {}


async def testar(token: str) -> list[str]:
    """Consulta a lista de agendas e o perfil do Gmail; devolve os problemas encontrados."""
    problemas = []
    cabecalhos = {"Authorization": "Bearer " + token}
    conta_falsa = {"rotulo": "nova"}
    for nome, url, parametros in (("Agenda", URL_TESTE_AGENDA, {"maxResults": 1}),
                                  ("Gmail", URL_TESTE_GMAIL, None)):
        try:
            status, resposta = await obter_com_cabecalhos(url, parametros, cabecalhos)
        except ErroRede as erro:
            problemas.append("%s: %s" % (nome, erro))
            continue
        if not 200 <= status < 300:
            problemas.append("%s: %s" % (nome, google_auth.explicar_erro(conta_falsa, url, status, resposta)))
    return problemas


def autorizar(rotulo: str) -> int:
    try:
        client_id, _ = google_auth.ler_cliente()
    except google_auth.ErroGoogle as erro:
        print("Não dá para continuar: %s." % erro)
        print('No Google Cloud: APIs e serviços > Credenciais > Criar credenciais > ID do cliente OAuth > '
              '"App para computador". Baixe o JSON e salve como %s.' % config.arquivo_cliente_google())
        return 1
    verificador, desafio = google_auth.gerar_pkce()
    estado = secrets.token_urlsafe(24)
    print("Autorizando a conta '%s'.\n" % rotulo)
    print("1. Abra este endereço no navegador do seu computador:\n")
    print(google_auth.url_autorizacao(client_id, desafio, estado))
    print("\n2. Entre com a conta Google que você quer ligar a '%s' e aprove o acesso." % rotulo)
    print("   Se aparecer \"O Google não verificou este app\", clique em Avançado e continue (o app é seu).")
    print("3. Depois de aprovar, o navegador vai mostrar uma página de erro (\"não foi possível conectar\"")
    print("   ou \"127.0.0.1 recusou a conexão\"). Isso é esperado.")
    print("4. Copie o endereço completo da barra de endereços (começa com http://127.0.0.1:8765/) e cole aqui.\n")
    try:
        colado = input("Endereço (ou só o código): ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        return 1
    try:
        codigo = ler_resposta(colado, estado)
    except ValueError as erro:
        print(erro)
        return 1
    try:
        status, resposta = asyncio.run(trocar_codigo(codigo, verificador))
    except (ErroRede, google_auth.ErroGoogle) as erro:
        print("Não consegui falar com o Google: %s." % erro)
        return 1
    if status != 200 or not resposta.get("access_token"):
        erro = resposta.get("error") or "erro %d" % status
        if erro == "invalid_grant":
            print("O código expirou ou já foi usado. Rode o comando de novo e cole o endereço logo após aprovar.")
        elif erro == "redirect_uri_mismatch":
            print("O cliente OAuth não aceita o endereço http://127.0.0.1:8765/. Use um cliente do tipo "
                  "\"App para computador\".")
        elif erro in ("invalid_client", "unauthorized_client"):
            print("O Google recusou o cliente OAuth. Baixe de novo o cliente.json.")
        else:
            print("O Google recusou a troca do código (%s)." % erro)
        return 1
    if not resposta.get("refresh_token"):
        print("O Google não devolveu um token de renovação. Remova o acesso do app em "
              "myaccount.google.com/permissions e rode de novo.")
        return 1
    email = google_auth.email_do_id_token(str(resposta.get("id_token") or ""))
    concedidos = str(resposta.get("scope") or "")
    try:
        config.salvar_json_privado(config.arquivo_conta_google(rotulo), {
            "rotulo": rotulo, "email": email, "refresh_token": resposta["refresh_token"],
            "escopos": concedidos.split()})
    except OSError as erro:
        print("Não consegui gravar %s (%s). Confira se a pasta de dados existe e se o usuário do contêiner "
              "pode escrever nela." % (config.arquivo_conta_google(rotulo), erro.strerror or type(erro).__name__))
        return 1
    for outra in google_auth.todas_as_contas():
        if outra["rotulo"] != rotulo and email and outra.get("email") == email:
            print("Aviso: esse e-mail também está salvo como '%s'." % outra["rotulo"])

    faltando = escopos_faltando(concedidos)
    problemas = asyncio.run(testar(str(resposta["access_token"])))
    print("Conta '%s' (%s) autorizada." % (rotulo, email or "e-mail não informado"))
    if faltando:
        print("Permissões não concedidas: %s. Essas funções não vão funcionar; rode de novo e marque todas as "
              "caixas se quiser." % ", ".join(faltando))
    for problema in problemas:
        print("Teste com problema: %s." % problema)
    if problemas and any("não está ativada" in p for p in problemas):
        print("Ative as APIs em console.cloud.google.com > APIs e serviços > Biblioteca "
              "(Google Calendar API e Gmail API).")
    print(REINICIE)
    return 0


def main(argumentos: list[str] | None = None) -> int:
    leitor = argparse.ArgumentParser(prog="python -m app.google_login",
                                     description="Autoriza contas Google (Agenda e Gmail) no Jarvis.")
    leitor.add_argument("rotulo", nargs="?", help="nome curto da conta: pessoal, faculdade...")
    leitor.add_argument("--listar", action="store_true", help="mostra as contas autorizadas")
    leitor.add_argument("--remover", metavar="ROTULO", help="revoga e apaga a conta")
    opcoes = leitor.parse_args(argumentos)
    if opcoes.listar:
        return listar()
    if opcoes.remover:
        return remover(opcoes.remover.strip().lower())
    rotulo = (opcoes.rotulo or "").strip().lower()
    if not rotulo:
        leitor.print_help()
        return 1
    if rotulo == "cliente" or not config.ROTULO_VALIDO.match(rotulo):
        print("Rótulo inválido: use letras minúsculas, números e hífen (por exemplo pessoal ou faculdade).")
        return 1
    return autorizar(rotulo)


if __name__ == "__main__":
    sys.exit(main())

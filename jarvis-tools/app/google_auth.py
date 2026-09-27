"""Contas Google (uma ou várias): cliente OAuth, tokens de acesso em memória e chamadas às APIs.

Arquivos em $DADOS/google/: cliente.json (baixado do Google Cloud, tipo "App para computador") e
<rotulo>.json por conta, gravado por 'python -m app.google_login <rotulo>'.
Nunca registra nem devolve tokens em mensagens.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import urllib.parse

from app import config
from app.rede import ErroRede, obter_com_cabecalhos, postar_form, postar_json
from app.textos import normalizar

URL_AUTORIZACAO = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_REVOGAR = "https://oauth2.googleapis.com/revoke"
REDIRECIONAMENTO = "http://127.0.0.1:8765/"
ESCOPOS = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
NOMES_ESCOPOS = {
    "https://www.googleapis.com/auth/calendar.events": "ver e criar eventos da agenda",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly": "ver a lista de agendas",
    "https://www.googleapis.com/auth/gmail.readonly": "ler os e-mails",
    "email": "ver o endereço de e-mail",
}
MARGEM_EXPIRACAO = 60  # segundos: renova um pouco antes de vencer


class ErroGoogle(Exception):
    """Falha com uma conta Google; a mensagem já é uma frase em português (sem ponto final)."""


# ---------------------------------------------------------------- arquivos

def ler_cliente() -> tuple[str, str]:
    """(client_id, client_secret) do cliente.json, aceitando o formato 'installed' ou 'web'."""
    caminho = config.arquivo_cliente_google()
    conteudo = config.ler_json(caminho)
    if not conteudo:
        raise ErroGoogle("falta o arquivo %s, com o cliente OAuth do Google Cloud" % caminho)
    bloco = conteudo.get("installed") or conteudo.get("web") or conteudo
    client_id, client_secret = bloco.get("client_id"), bloco.get("client_secret")
    if not client_id:
        raise ErroGoogle("o arquivo %s não tem client_id; baixe de novo o JSON do cliente OAuth" % caminho)
    return str(client_id), str(client_secret or "")


def carregar_conta(rotulo: str) -> dict | None:
    if not config.ROTULO_VALIDO.match(rotulo or ""):
        return None
    conteudo = config.ler_json(config.arquivo_conta_google(rotulo))
    if not conteudo or not conteudo.get("refresh_token"):
        return None
    conteudo["rotulo"] = rotulo
    return conteudo


def todas_as_contas() -> list[dict]:
    return [c for c in (carregar_conta(r) for r in config.contas_google()) if c]


def descrever_contas(contas: list[dict]) -> str:
    return ", ".join("%s (%s)" % (c["rotulo"], c.get("email") or "e-mail desconhecido") for c in contas)


def escolher_contas(conta: str = "") -> list[dict]:
    """Conta pedida por rótulo ou e-mail; vazio = todas. Levanta ErroGoogle se não achar."""
    contas = todas_as_contas()
    if not contas:
        raise ErroGoogle("nenhuma conta Google autorizada; rode google_login com um rótulo, por exemplo pessoal")
    pedido = normalizar(conta)
    if not pedido or pedido in ("todas", "todas as contas", "tudo"):
        return contas
    for campo in ("rotulo", "email"):
        exatas = [c for c in contas if normalizar(str(c.get(campo) or "")) == pedido]
        if exatas:
            return exatas[:1]
    parecidas = [c for c in contas if pedido in normalizar(c["rotulo"]) or pedido in normalizar(c.get("email") or "")]
    if len(parecidas) == 1:
        return parecidas
    raise ErroGoogle('não conheço a conta "%s"; as contas são: %s' % (conta.strip(), descrever_contas(contas)))


def conta_para_criar(conta: str = "") -> dict:
    """A conta pedida, ou GOOGLE_CONTA_PADRAO, ou a primeira em ordem alfabética."""
    if (conta or "").strip():
        return escolher_contas(conta)[0]
    padrao = carregar_conta(config.conta_google_padrao())
    if not padrao:
        return escolher_contas("")[0]
    return padrao


# ---------------------------------------------------------------- OAuth

def gerar_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge S256)."""
    verificador = secrets.token_urlsafe(64)  # 86 caracteres, dentro dos 43 a 128 exigidos
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode("ascii")).digest()).rstrip(b"=")
    return verificador, desafio.decode("ascii")


def url_autorizacao(client_id: str, desafio: str, estado: str) -> str:
    return URL_AUTORIZACAO + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECIONAMENTO, "response_type": "code",
        "scope": " ".join(ESCOPOS), "code_challenge": desafio, "code_challenge_method": "S256",
        "state": estado, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
    }, quote_via=urllib.parse.quote)


def email_do_id_token(id_token: str) -> str:
    """E-mail do payload do id_token (JWT). Sem conferir assinatura: veio direto do Google, por TLS."""
    try:
        carga = id_token.split(".")[1]
        dados = json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)))
        return str(dados.get("email") or "")
    except (IndexError, ValueError, AttributeError):
        return ""


_tokens: dict[str, tuple[str, float]] = {}  # rótulo -> (access_token, validade em time.monotonic)
_travas: dict[str, asyncio.Lock] = {}


def esquecer_tokens() -> None:
    _tokens.clear()


async def _renovar(conta: dict) -> str:
    rotulo = conta["rotulo"]
    client_id, client_secret = ler_cliente()
    campos = {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": conta["refresh_token"]}
    if client_secret:
        campos["client_secret"] = client_secret
    try:
        status, resposta = await postar_form(URL_TOKEN, campos)
    except ErroRede as erro:
        raise ErroGoogle("não consegui falar com o Google (%s)" % erro) from None
    resposta = resposta if isinstance(resposta, dict) else {}
    if status == 200 and resposta.get("access_token"):
        validade = time.monotonic() + max(int(resposta.get("expires_in") or 3600) - MARGEM_EXPIRACAO, 30)
        _tokens[rotulo] = (str(resposta["access_token"]), validade)
        return _tokens[rotulo][0]
    codigo = str(resposta.get("error") or "")
    if codigo == "invalid_grant":
        raise ErroGoogle("a autorização da conta %s expirou ou foi revogada; rode google_login %s de novo"
                         % (rotulo, rotulo))
    if codigo in ("invalid_client", "unauthorized_client"):
        raise ErroGoogle("o Google recusou o cliente OAuth (cliente.json); confira se é o mesmo usado no login")
    raise ErroGoogle("o Google recusou renovar o acesso da conta %s (%s)" % (rotulo, codigo or "erro %d" % status))


def _trava(rotulo: str) -> asyncio.Lock:
    """Uma renovação por vez para cada conta (uma trava por loop: testes e CLI criam loops novos)."""
    chave = "%s:%d" % (rotulo, id(asyncio.get_running_loop()))
    if chave not in _travas:
        _travas[chave] = asyncio.Lock()
    return _travas[chave]


async def token_de_acesso(conta: dict, renovar: bool = False) -> str:
    rotulo = conta["rotulo"]
    async with _trava(rotulo):
        guardado = _tokens.get(rotulo)
        if guardado and not renovar and time.monotonic() < guardado[1]:
            return guardado[0]
        return await _renovar(conta)


def _motivos(resposta) -> set[str]:
    erro = resposta.get("error") if isinstance(resposta, dict) else None
    if not isinstance(erro, dict):
        return set()
    motivos = {str(e.get("reason")) for e in erro.get("errors") or [] if isinstance(e, dict)}
    motivos |= {str(d.get("reason")) for d in erro.get("details") or [] if isinstance(d, dict)}
    motivos.add(str(erro.get("status") or ""))
    return motivos


def explicar_erro(conta: dict, url: str, status: int, resposta) -> str:
    rotulo = conta["rotulo"]
    api = "Gmail" if "gmail" in url else "Google Calendar"
    motivos = _motivos(resposta)
    if status == 401:
        return "o Google recusou o acesso da conta %s; rode google_login %s de novo" % (rotulo, rotulo)
    if motivos & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return ("a conta %s não deu essa permissão; rode google_login %s de novo e marque todas as caixas"
                % (rotulo, rotulo))
    if motivos & {"accessNotConfigured", "SERVICE_DISABLED"}:
        return "a API do %s não está ativada no projeto do Google Cloud" % api
    if status == 429 or motivos & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded",
                                   "RESOURCE_EXHAUSTED"}:
        return "o Google limitou os pedidos por agora; tente de novo em um minuto"
    if status == 404:
        return "o Google não encontrou o que foi pedido na conta %s" % rotulo
    if status >= 500:
        return "o Google está com problemas agora (erro %d)" % status
    return "o Google recusou o pedido da conta %s (erro %d)" % (rotulo, status)


async def chamar(conta: dict, metodo: str, url: str, parametros=None, corpo=None):
    """Chama uma API do Google com o token da conta; renova e tenta de novo uma vez se vier 401."""
    status, resposta = 0, None
    for tentativa in range(2):
        token = await token_de_acesso(conta, renovar=tentativa > 0)
        cabecalhos = {"Authorization": "Bearer " + token}
        try:
            if metodo == "GET":
                status, resposta = await obter_com_cabecalhos(url, parametros, cabecalhos)
            else:
                status, resposta = await postar_json(url, corpo, parametros, cabecalhos)
        except ErroRede as erro:
            raise ErroGoogle("não consegui falar com o Google (%s)" % erro) from None
        if status != 401:
            break
        _tokens.pop(conta["rotulo"], None)
    if 200 <= status < 300:
        return resposta if resposta is not None else {}
    raise ErroGoogle(explicar_erro(conta, url, status, resposta))


async def revogar(token: str) -> bool:
    try:
        status, _ = await postar_form(URL_REVOGAR, {"token": token})
    except ErroRede:
        return False
    return status == 200

"""Google falso para os testes: endpoint de token, Calendar v3 e Gmail v1 no formato das APIs reais."""
import base64
import json
from datetime import datetime
from urllib.parse import unquote

from apoio import ServidorFalso

ESCOPOS_TODOS = ("openid https://www.googleapis.com/auth/userinfo.email "
                 "https://www.googleapis.com/auth/calendar.events "
                 "https://www.googleapis.com/auth/calendar.calendarlist.readonly "
                 "https://www.googleapis.com/auth/gmail.readonly")


def id_token(email):
    """JWT sem assinatura válida (o código só lê o payload)."""
    parte = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return "%s.%s.assinatura" % (parte({"alg": "RS256"}), parte({"iss": "https://accounts.google.com",
                                                                "email": email, "email_verified": True}))


def evento(id_, titulo, inicio, fim, **extras):
    """inicio/fim: '2026-09-24T09:00:00-03:00' (com hora) ou '2026-09-24' (dia todo)."""
    chave = "date" if len(inicio) == 10 else "dateTime"
    item = {"kind": "calendar#event", "id": id_, "status": "confirmed", "summary": titulo,
            "start": {chave: inicio}, "end": {chave: fim}, "iCalUID": id_ + "@google.com"}
    if chave == "dateTime":
        item["start"]["timeZone"] = item["end"]["timeZone"] = "America/Sao_Paulo"
    item.update(extras)
    return item


def _base64url(dados: bytes) -> str:
    """Como o Gmail manda o corpo das partes: base64 'url-safe', sem o '=' do fim."""
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def parte_texto(tipo, conteudo, charset="utf-8", id_parte="0"):
    """Parte text/plain ou text/html, com o corpo já no charset pedido (como o Gmail entrega)."""
    dados = conteudo.encode(charset)
    return {"partId": id_parte, "mimeType": tipo, "filename": "", "headers": [
        {"name": "Content-Type", "value": '%s; charset="%s"' % (tipo, charset.upper())},
        {"name": "Content-Transfer-Encoding", "value": "quoted-printable"}],
        "body": {"size": len(dados), "data": _base64url(dados)}}


def parte_anexo(nome, tipo="application/pdf", id_parte="1", inline=False):
    cabecalhos = [{"name": "Content-Type", "value": '%s; name="%s"' % (tipo, nome)},
                  {"name": "Content-Disposition", "value": '%s; filename="%s"' % (
                      "inline" if inline else "attachment", nome)},
                  {"name": "Content-Transfer-Encoding", "value": "base64"}]
    if inline:
        cabecalhos.append({"name": "Content-ID", "value": "<%s@x>" % nome})
    return {"partId": id_parte, "mimeType": tipo, "filename": nome, "headers": cabecalhos,
            "body": {"attachmentId": "anexo-" + nome, "size": 2048}}


def mensagem(id_, de, assunto, snippet, recebido: datetime, rotulos=("INBOX", "IMPORTANT", "CATEGORY_PERSONAL"),
             texto=None, html=None, charset="utf-8", anexos=()):
    """Mensagem no formato 'full' do Gmail. Sem texto nem html, o corpo é o próprio snippet em text/plain.
    Uma parte só vira o payload direto; texto e html viram multipart/alternative; anexos, multipart/mixed.
    anexos: nomes de arquivo, ou (nome, tipo, inline)."""
    if texto is None and html is None:
        texto = snippet
    corpo = []
    if texto is not None:
        corpo.append(parte_texto("text/plain", texto, charset, "0"))
    if html is not None:
        corpo.append(parte_texto("text/html", html, charset, "1" if texto is not None else "0"))
    raiz = corpo[0] if len(corpo) == 1 else {"partId": "0", "mimeType": "multipart/alternative", "filename": "",
                                             "headers": [], "body": {"size": 0}, "parts": corpo}
    if anexos:
        partes = [raiz]
        for n, anexo in enumerate(anexos):
            nome, tipo, inline = (anexo, "application/pdf", False) if isinstance(anexo, str) else anexo
            partes.append(parte_anexo(nome, tipo, str(n + 1), inline))
        raiz = {"partId": "", "mimeType": "multipart/mixed", "filename": "", "headers": [], "body": {"size": 0},
                "parts": partes}
    payload = json.loads(json.dumps(raiz))
    payload["headers"] = [
        {"name": "Delivered-To", "value": "eu@x.com"}, {"name": "From", "value": de},
        {"name": "Subject", "value": assunto}, {"name": "Date", "value": recebido.strftime("%a, %d %b %Y %H:%M:%S %z")},
        {"name": "To", "value": "eu@x.com"}] + [h for h in raiz.get("headers") or [] if h["name"] == "Content-Type"]
    return {"id": id_, "threadId": "t" + id_, "labelIds": list(rotulos), "snippet": snippet,
            "internalDate": str(int(recebido.timestamp() * 1000)), "sizeEstimate": 1234, "historyId": "99",
            "payload": payload}


def _instante(texto):
    return datetime.fromisoformat(texto if len(texto) > 10 else texto + "T00:00:00-03:00")


def _cabecalho(mensagem, nome):
    return next((h["value"] for h in mensagem["payload"]["headers"] if h["name"].lower() == nome), "")


def _bate_consulta(m, consulta):
    """O suficiente da busca do Gmail para os testes: in:inbox, in:anywhere, -in:spam/-in:trash, after:,
    is:important, -category:, from: e palavras soltas (em remetente, assunto ou snippet)."""
    rotulos = set(m["labelIds"])
    de = _cabecalho(m, "from").lower()
    texto = " ".join([de, _cabecalho(m, "subject"), m["snippet"]]).lower()
    termos = consulta.split()
    if "in:anywhere" not in termos and rotulos & {"SPAM", "TRASH"}:
        return False  # a API não traz spam nem lixeira sem includeSpamTrash
    for termo in termos:
        negado = termo.startswith("-")
        chave, _, valor = termo.lstrip("-").partition(":")
        if not valor:
            bate = chave.lower() in texto
        elif chave == "in":
            bate = valor == "anywhere" or valor.upper() in rotulos
        elif chave == "after":
            bate = int(m["internalDate"]) // 1000 >= int(valor)
        elif chave == "is":
            bate = valor.upper() in rotulos
        elif chave == "category":
            bate = "CATEGORY_" + valor.upper() in rotulos
        elif chave == "from":
            bate = valor.lower() in de
        else:
            bate = True
        if bate == negado:
            return False
    return True


class GoogleFalso:
    def __init__(self):
        self.contas = {}  # rotulo -> dados
        self.revogados = set()  # refresh tokens recusados com invalid_grant
        self.token_invalido_uma_vez = set()  # rótulos cujo próximo token vai dar 401 uma vez
        self.emitidos = []
        self.criados = []
        self.falhar_agenda = {}  # (rotulo, id da agenda) -> status
        self.sem_permissao_lista = set()  # rótulos sem o escopo calendar.calendarlist.readonly
        self.lista_fora_de_ordem = False  # True: a lista do Gmail vem da mais antiga para a mais nova
        self.login = {"email": "nova@gmail.com", "scope": ESCOPOS_TODOS, "verificador": None}
        self.servidor = ServidorFalso(self.responder)
        self.base = self.servidor.base
        self._tokens = {}  # access token -> rótulo
        self._invalidos = set()

    def conta(self, rotulo, email, agendas=None, eventos=None, mensagens=None):
        self.contas[rotulo] = {"refresh": "rt-" + rotulo, "email": email,
                               "agendas": agendas if agendas is not None else [
                                   {"kind": "calendar#calendarListEntry", "id": email, "summary": email,
                                    "primary": True, "selected": True, "accessRole": "owner"}],
                               "eventos": eventos or {}, "mensagens": mensagens or []}

    @property
    def pedidos(self):
        return self.servidor.pedidos

    def fechar(self):
        self.servidor.fechar()

    # ------------------------------------------------------------ rotas
    def responder(self, pedido):
        if pedido.caminho == "/token":
            return self._token(pedido.formulario())
        if pedido.caminho == "/revoke":
            return 200, {}
        cabecalho = pedido.cabecalhos.get("authorization", "")
        token = cabecalho[len("Bearer "):] if cabecalho.startswith("Bearer ") else ""
        rotulo = self._tokens.get(token)
        if token == "at-novo":
            rotulo = "__login__"
        if rotulo is None or token in self._invalidos:
            return 401, {"error": {"code": 401, "message": "Request had invalid authentication credentials.",
                                   "errors": [{"reason": "authError"}], "status": "UNAUTHENTICATED"}}
        if rotulo == "__login__":
            if pedido.caminho.endswith("/profile"):
                return 200, {"emailAddress": self.login["email"], "messagesTotal": 1, "threadsTotal": 1,
                             "historyId": "1"}
            return 200, {"kind": "calendar#calendarList", "items": []}
        dados = self.contas[rotulo]
        caminho = pedido.caminho
        if caminho == "/calendar/v3/users/me/calendarList":
            if rotulo in self.sem_permissao_lista:
                return 403, {"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                                       "errors": [{"reason": "insufficientPermissions"}],
                                       "status": "PERMISSION_DENIED",
                                       "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}
            return 200, {"kind": "calendar#calendarList", "etag": "x", "items": dados["agendas"]}
        if caminho.startswith("/calendar/v3/calendars/") and caminho.endswith("/events"):
            agenda = unquote(caminho[len("/calendar/v3/calendars/"):-len("/events")])
            if (rotulo, agenda) in self.falhar_agenda:
                return self.falhar_agenda[(rotulo, agenda)], {"error": {"code": 404, "message": "Not Found"}}
            if agenda == "primary":
                agenda = next((a["id"] for a in dados["agendas"] if a.get("primary")), agenda)
            if pedido.metodo == "POST":
                corpo = pedido.json()
                criado = dict(corpo, id="novo%d" % (len(self.criados) + 1), status="confirmed",
                              htmlLink="https://calendar.google.com/x")
                self.criados.append((rotulo, criado))
                dados["eventos"].setdefault(agenda, []).append(criado)
                return 200, criado
            de, ate = _instante(pedido.valor("timeMin")), _instante(pedido.valor("timeMax"))
            itens = [e for e in dados["eventos"].get(agenda, [])
                     if _instante(e["end"].get("dateTime") or e["end"]["date"]) > de
                     and _instante(e["start"].get("dateTime") or e["start"]["date"]) < ate]
            itens.sort(key=lambda e: _instante(e["start"].get("dateTime") or e["start"]["date"]))
            return 200, {"kind": "calendar#events", "summary": agenda, "timeZone": "America/Sao_Paulo",
                         "items": itens[:int(pedido.valor("maxResults", 250))]}
        if caminho == "/gmail/v1/users/me/messages":
            return 200, self._lista_gmail(dados, pedido)
        if caminho.startswith("/gmail/v1/users/me/messages/"):
            id_ = caminho.rsplit("/", 1)[1]
            original = next((m for m in dados["mensagens"] if m["id"] == id_), None)
            if original is None:
                return 404, {"error": {"code": 404, "message": "Requested entity was not found."}}
            copia = json.loads(json.dumps(original))
            formato = pedido.valor("format", "full")
            if formato == "metadata":
                pedidos = set(pedido.consulta.get("metadataHeaders") or [])
                copia["payload"] = {"mimeType": original["payload"]["mimeType"],
                                    "headers": [h for h in original["payload"]["headers"] if h["name"] in pedidos]}
            elif formato == "minimal":
                del copia["payload"]
            return 200, copia
        if caminho == "/gmail/v1/users/me/profile":
            return 200, {"emailAddress": dados["email"], "messagesTotal": 3, "threadsTotal": 3, "historyId": "9"}
        return 404, {"error": {"code": 404, "message": "Not Found"}}

    def _token(self, campos):
        if campos.get("grant_type") == "authorization_code":
            if campos.get("code") != "4/0codigo-bom":
                return 400, {"error": "invalid_grant", "error_description": "Bad Request"}
            self.login["verificador"] = campos.get("code_verifier")
            return 200, {"access_token": "at-novo", "expires_in": 3599, "refresh_token": "rt-novo",
                         "scope": self.login["scope"], "token_type": "Bearer",
                         "id_token": id_token(self.login["email"])}
        if campos.get("grant_type") != "refresh_token" or campos.get("client_id") != \
                "id-cliente.apps.googleusercontent.com":
            return 401, {"error": "invalid_client", "error_description": "Unauthorized"}
        refresh = campos.get("refresh_token")
        rotulo = next((r for r, d in self.contas.items() if d["refresh"] == refresh), None)
        if rotulo is None or refresh in self.revogados:
            return 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
        token = "at-%s-%d" % (rotulo, len(self.emitidos) + 1)
        self.emitidos.append(token)
        self._tokens[token] = rotulo
        if rotulo in self.token_invalido_uma_vez:
            self.token_invalido_uma_vez.discard(rotulo)
            self._invalidos.add(token)
        return 200, {"access_token": token, "expires_in": 3599, "scope": ESCOPOS_TODOS, "token_type": "Bearer"}

    def _lista_gmail(self, dados, pedido):
        consulta = pedido.valor("q", "")
        achadas = sorted((m for m in dados["mensagens"] if _bate_consulta(m, consulta)),
                         key=lambda m: int(m["internalDate"]), reverse=not self.lista_fora_de_ordem)
        itens = [{"id": m["id"], "threadId": m["threadId"]} for m in achadas]
        limite = int(pedido.valor("maxResults", 100))
        resposta = {"messages": itens[:limite], "resultSizeEstimate": len(itens)}
        if len(itens) > limite:
            resposta["nextPageToken"] = "pagina2"
        if not itens:
            del resposta["messages"]  # a API real omite a lista quando não há nada
        return resposta

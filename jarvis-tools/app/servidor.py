"""Servidor MCP do Jarvis. Só escuta na rede interna do Docker e exige o token JARVIS_TOOLS_TOKEN.

Endpoint MCP: http://jarvis-tools:8000/mcp    Saúde (sem token): http://jarvis-tools:8000/saude

hora e clima estão sempre ligadas; Moodle, Google e busca só entram quando estão configurados
(ver config.recursos()), para o modelo não ver ferramentas que não funcionariam.
"""
from __future__ import annotations

import hmac
import json
import logging
import os

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from app import clima, config, ferramentas

log = logging.getLogger("jarvis-tools")

INSTRUCOES = "Ferramentas do Jarvis. Respostas em português, prontas para ler em voz alta."


def registrar(servidor_mcp: MCPServer, ferramenta: ferramentas.Ferramenta, recursos: dict | None = None) -> None:
    servidor_mcp.add_tool(
        ferramentas.com_registro(ferramenta),
        name=ferramenta.nome,
        description=ferramentas.descricao(ferramenta, recursos or {}),
        annotations=ferramenta.anotacoes,
        structured_output=False,
    )
    _tirar_titulos(servidor_mcp, ferramenta.nome)


def _tirar_titulos(servidor_mcp: MCPServer, nome: str) -> None:
    """Tira os 'title' que o pydantic põe no esquema ('climaArguments', 'Cidade'...): só gastam contexto do
    modelo. A validação dos argumentos usa o modelo pydantic, não este dicionário."""
    try:
        esquema = servidor_mcp._tool_manager.get_tool(nome).parameters
    except AttributeError:  # outra versão do SDK: fica como está
        return
    esquema.pop("title", None)
    for propriedade in (esquema.get("properties") or {}).values():
        if isinstance(propriedade, dict):
            propriedade.pop("title", None)


def novo_servidor() -> MCPServer:
    """Servidor com as ferramentas que sempre funcionam (hora e clima)."""
    servidor_mcp = MCPServer(name="jarvis", instructions=INSTRUCOES)
    for ferramenta in ferramentas.FERRAMENTAS:
        if not ferramenta.recurso:
            registrar(servidor_mcp, ferramenta)
    return servidor_mcp


def registrar_opcionais(servidor_mcp: MCPServer, recursos: dict | None = None) -> list[str]:
    """Registra Moodle, Google e busca conforme o que está configurado; devolve os nomes ativos.
    Pode ser chamada de novo: tira antes as opcionais registradas numa chamada anterior."""
    recursos = config.recursos() if recursos is None else recursos
    ativas = []
    for ferramenta in ferramentas.FERRAMENTAS:
        if not ferramenta.recurso:
            ativas.append(ferramenta.nome)
            continue
        try:
            servidor_mcp.remove_tool(ferramenta.nome)
        except Exception:  # ainda não estava registrada
            pass
        if ferramentas.ativa(ferramenta, recursos):
            registrar(servidor_mcp, ferramenta, recursos)
            ativas.append(ferramenta.nome)
    return ativas


servidor = novo_servidor()


class ExigeToken:
    """Middleware ASGI: /saude responde sem token; todo o resto exige 'Authorization: Bearer <token>'."""

    def __init__(self, app, token: str):
        self.app = app
        self.esperado = ("Bearer " + token).encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("path") == "/saude":
            return await self._responder(send, 200, {"status": "ok"})
        recebido = dict(scope.get("headers") or []).get(b"authorization", b"")
        if not hmac.compare_digest(recebido, self.esperado):
            return await self._responder(send, 401, {"error": "token ausente ou inválido"})
        return await self.app(scope, receive, send)

    @staticmethod
    async def _responder(send, status: int, corpo: dict):
        dados = json.dumps(corpo).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(dados)).encode())]})
        await send({"type": "http.response.body", "body": dados})


def criar_app(token: str, servidor_mcp: MCPServer | None = None, recursos: dict | None = None):
    servidor_mcp = servidor_mcp or servidor
    recursos = config.recursos() if recursos is None else recursos
    ativas = registrar_opcionais(servidor_mcp, recursos)
    log.info("recursos: moodle=%s google=%s busca=%s", "sim" if recursos["moodle"] else "não",
             ",".join(recursos["google"]) or "nenhuma", "sim" if recursos["busca"] else "não")
    log.info("ferramentas: %s", ", ".join(ativas))
    seguranca = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["jarvis-tools:*", "127.0.0.1:*", "localhost:*"],
        allowed_origins=[],
    )
    app = servidor_mcp.streamable_http_app(stateless_http=True, json_response=True,
                                           transport_security=seguranca, host="0.0.0.0")
    return ExigeToken(app, token)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    for ruidoso in ("mcp", "httpx2"):  # uma linha por requisição no modo sem sessão
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    token = os.environ.get("JARVIS_TOOLS_TOKEN", "")
    if len(token) < 16:
        raise SystemExit("JARVIS_TOOLS_TOKEN ausente ou curto demais (mínimo 16 caracteres).")
    log.info("cidade padrão: %s", clima.cidade_padrao())
    # Sem log de acesso: o healthcheck geraria uma linha a cada 30 s. Cada ferramenta registra a própria chamada.
    uvicorn.run(criar_app(token), host="0.0.0.0", port=int(os.environ.get("PORTA", "8000")),
                log_level="info", access_log=False, proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()

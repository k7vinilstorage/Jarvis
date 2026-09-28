"""Apoio aos testes: servidores HTTP falsos locais e uma pasta $DADOS temporária. Nada sai para a internet."""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class Pedido:
    def __init__(self, metodo, caminho, consulta, cabecalhos, corpo):
        self.metodo = metodo
        self.caminho = caminho
        self.consulta = consulta  # dict: nome -> lista de valores
        self.cabecalhos = cabecalhos
        self.corpo = corpo

    def json(self):
        return json.loads(self.corpo.decode("utf-8"))

    def formulario(self):
        return {k: v[0] for k, v in parse_qs(self.corpo.decode("utf-8")).items()}

    def valor(self, nome, padrao=None):
        """Parâmetro da URL ou, num POST de formulário, do corpo."""
        if nome in self.consulta:
            return self.consulta[nome][0]
        if "x-www-form-urlencoded" in self.cabecalhos.get("content-type", ""):
            return self.formulario().get(nome, padrao)
        return padrao

    def lista(self, prefixo):
        """Valores de parâmetros em lista do Moodle (prefixo[0], prefixo[1]...), da URL ou do corpo."""
        todos = dict((k, v[0]) for k, v in self.consulta.items())
        if "x-www-form-urlencoded" in self.cabecalhos.get("content-type", ""):
            todos.update(self.formulario())
        return [todos[k] for k in sorted((k for k in todos if k.startswith(prefixo + "[")),
                                         key=lambda k: int(k[len(prefixo) + 1:-1]))]


class ServidorFalso:
    """Chama responder(pedido) -> (status, corpo) ou (status, corpo, cabecalhos). Corpo dict/list vira JSON."""

    def __init__(self, responder):
        self.pedidos = []
        self.responder = responder
        dono = self

        class Tratador(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _tratar(self):
                url = urlparse(self.path)
                tamanho = int(self.headers.get("Content-Length") or 0)
                corpo = self.rfile.read(tamanho) if tamanho else b""
                pedido = Pedido(self.command, url.path, parse_qs(url.query, keep_blank_values=True),
                                {k.lower(): v for k, v in self.headers.items()}, corpo)
                dono.pedidos.append(pedido)
                resposta = dono.responder(pedido)
                status, conteudo = resposta[0], resposta[1]
                extras = resposta[2] if len(resposta) > 2 else {}
                if isinstance(conteudo, (dict, list)):
                    conteudo = json.dumps(conteudo).encode()
                    extras = {"Content-Type": "application/json", **extras}
                elif isinstance(conteudo, str):
                    conteudo = conteudo.encode("utf-8")
                self.send_response(status)
                for nome, valor in extras.items():
                    self.send_header(nome, valor)
                self.send_header("Content-Length", str(len(conteudo)))
                self.end_headers()
                self.wfile.write(conteudo)

            do_GET = do_POST = _tratar

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Tratador)
        self.porta = self.http.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.porta
        threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def fechar(self):
        self.http.shutdown()
        self.http.server_close()


class PastaDados:
    """$DADOS temporário: grava moodle.json e contas Google conforme pedido."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="jarvis-dados-")
        self.caminho = Path(self._tmp.name)

    def moodle(self, url, token="tok-moodle", nome="Fulano de Tal"):
        self._gravar("moodle.json", {"url": url, "token": token, "userid": 42, "nome": nome})

    def cliente_google(self, tipo="installed"):
        self._gravar("google/cliente.json", {tipo: {"client_id": "id-cliente.apps.googleusercontent.com",
                                                     "client_secret": "segredo-cliente"}})

    def conta_google(self, rotulo, email, refresh_token=None):
        self._gravar("google/%s.json" % rotulo, {"rotulo": rotulo, "email": email,
                                                   "refresh_token": refresh_token or "rt-" + rotulo,
                                                   "escopos": []})

    def _gravar(self, relativo, conteudo):
        destino = self.caminho / relativo
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(conteudo), encoding="utf-8")

    def ambiente(self, **extras):
        valores = {"DADOS": str(self.caminho), "TZ": "America/Sao_Paulo"}
        valores.update(extras)
        return valores

    def apagar(self):
        self._tmp.cleanup()


def limpar_ambiente():
    """Variáveis que mudam o comportamento e não devem vazar do ambiente de quem roda os testes."""
    return {k: "" for k in ("SEARXNG_URL", "GOOGLE_CONTA_PADRAO", "MOODLE_URL", "JARVIS_TERMOS_PRIVADOS")
            if k in os.environ}

"""Testes das contas Google: cliente OAuth, escolha de conta, tokens (cache, 401, invalid_grant) e login."""
import asyncio
import base64
import contextlib
import hashlib
import io
import os
import stat
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from apoio import PastaDados, limpar_ambiente
from apoio_google import GoogleFalso

from app import config, google_auth, google_login


class BaseGoogle(unittest.TestCase):
    def setUp(self):
        self.google = GoogleFalso()
        self.google.conta("pessoal", "ana@gmail.com")
        self.google.conta("faculdade", "ana@alunos.utfpr.edu.br")
        self.dados = PastaDados()
        self.dados.cliente_google()
        self.dados.conta_google("pessoal", "ana@gmail.com")
        self.dados.conta_google("faculdade", "ana@alunos.utfpr.edu.br")
        self.remendos = [
            mock.patch.dict(os.environ, {**limpar_ambiente(), **self.dados.ambiente()}),
            mock.patch.object(google_auth, "URL_TOKEN", self.google.base + "/token"),
            mock.patch.object(google_auth, "URL_REVOGAR", self.google.base + "/revoke"),
        ]
        for remendo in self.remendos:
            remendo.start()
        google_auth.esquecer_tokens()

    def tearDown(self):
        for remendo in reversed(self.remendos):
            remendo.stop()
        self.google.fechar()
        self.dados.apagar()
        google_auth.esquecer_tokens()


class TesteContas(BaseGoogle):
    def test_recursos_e_cliente(self):
        (config.pasta_google() / "Invalido.json").write_text('{"refresh_token": "x"}')
        (config.pasta_google() / "sem-token.json").write_text('{"email": "x@y"}')
        self.assertEqual(config.contas_google(), ["faculdade", "pessoal"])
        self.assertEqual(config.recursos(), {"moodle": False, "google": ["faculdade", "pessoal"], "busca": False})
        self.assertEqual(google_auth.ler_cliente(), ("id-cliente.apps.googleusercontent.com", "segredo-cliente"))
        self.dados.cliente_google(tipo="web")
        self.assertEqual(google_auth.ler_cliente()[0], "id-cliente.apps.googleusercontent.com")
        config.arquivo_cliente_google().unlink()
        with self.assertRaises(google_auth.ErroGoogle) as erro:
            google_auth.ler_cliente()
        self.assertIn("cliente.json", str(erro.exception))

    def test_escolher_contas(self):
        rotulos = lambda contas: [c["rotulo"] for c in contas]  # noqa: E731
        self.assertEqual(rotulos(google_auth.escolher_contas("")), ["faculdade", "pessoal"])
        self.assertEqual(rotulos(google_auth.escolher_contas("todas")), ["faculdade", "pessoal"])
        self.assertEqual(rotulos(google_auth.escolher_contas("Pessoal")), ["pessoal"])
        self.assertEqual(rotulos(google_auth.escolher_contas("ana@alunos.utfpr.edu.br")), ["faculdade"])
        self.assertEqual(rotulos(google_auth.escolher_contas("utfpr")), ["faculdade"])
        with self.assertRaises(google_auth.ErroGoogle) as erro:
            google_auth.escolher_contas("trabalho")
        self.assertEqual(str(erro.exception), 'não conheço a conta "trabalho"; as contas são: '
                                              'faculdade (ana@alunos.utfpr.edu.br), pessoal (ana@gmail.com)')
        with self.assertRaises(google_auth.ErroGoogle):
            google_auth.escolher_contas("ana")  # ambíguo

    def test_conta_para_criar(self):
        self.assertEqual(google_auth.conta_para_criar("")["rotulo"], "faculdade")  # primeira em ordem alfabética
        with mock.patch.dict(os.environ, {"GOOGLE_CONTA_PADRAO": "pessoal"}):
            self.assertEqual(google_auth.conta_para_criar("")["rotulo"], "pessoal")
            self.assertEqual(google_auth.conta_para_criar("faculdade")["rotulo"], "faculdade")
        with mock.patch.dict(os.environ, {"GOOGLE_CONTA_PADRAO": "inexistente"}):
            self.assertEqual(google_auth.conta_para_criar("")["rotulo"], "faculdade")


class TesteTokens(BaseGoogle):
    def chamar(self, rotulo="pessoal"):
        conta = google_auth.carregar_conta(rotulo)
        return asyncio.run(google_auth.chamar(conta, "GET", self.google.base + "/gmail/v1/users/me/profile"))

    def test_cache_do_token(self):
        self.assertEqual(self.chamar()["emailAddress"], "ana@gmail.com")
        self.assertEqual(self.chamar()["emailAddress"], "ana@gmail.com")
        self.assertEqual(len(self.google.emitidos), 1)
        pedido_token = [p for p in self.google.pedidos if p.caminho == "/token"][0]
        self.assertEqual(pedido_token.formulario(), {
            "client_id": "id-cliente.apps.googleusercontent.com", "client_secret": "segredo-cliente",
            "grant_type": "refresh_token", "refresh_token": "rt-pessoal"})
        api = [p for p in self.google.pedidos if p.caminho.endswith("/profile")][0]
        self.assertEqual(api.cabecalhos["authorization"], "Bearer at-pessoal-1")

    def test_401_renova_uma_vez(self):
        self.google.token_invalido_uma_vez.add("pessoal")
        self.assertEqual(self.chamar()["emailAddress"], "ana@gmail.com")
        self.assertEqual(len(self.google.emitidos), 2)

    def test_autorizacao_revogada(self):
        self.google.revogados.add("rt-faculdade")
        with self.assertRaises(google_auth.ErroGoogle) as erro:
            self.chamar("faculdade")
        self.assertEqual(str(erro.exception), "a autorização da conta faculdade expirou ou foi revogada; "
                                              "rode google_login faculdade de novo")

    def test_erros_sem_token(self):
        conta = google_auth.carregar_conta("pessoal")
        with self.assertRaises(google_auth.ErroGoogle) as erro:
            asyncio.run(google_auth.chamar(conta, "GET", self.google.base + "/calendar/v3/nada"))
        self.assertNotIn("at-pessoal", str(erro.exception))
        self.assertNotIn("Bearer", str(erro.exception))
        motivo = {"error": {"code": 403, "errors": [{"reason": "insufficientPermissions"}],
                            "status": "PERMISSION_DENIED"}}
        self.assertIn("não deu essa permissão", google_auth.explicar_erro(conta, "x/gmail/", 403, motivo))
        desligada = {"error": {"code": 403, "status": "PERMISSION_DENIED",
                               "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                            "reason": "SERVICE_DISABLED"}]}}
        self.assertEqual(google_auth.explicar_erro(conta, "https://gmail.googleapis.com/x", 403, desligada),
                         "a API do Gmail não está ativada no projeto do Google Cloud")


class TesteOAuth(unittest.TestCase):
    def test_pkce_e_url(self):
        verificador, desafio = google_auth.gerar_pkce()
        self.assertTrue(43 <= len(verificador) <= 128)
        esperado = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(desafio, esperado)
        url = google_auth.url_autorizacao("meu-id", desafio, "estado-1")
        partes = urlsplit(url)
        self.assertEqual("%s://%s%s" % (partes.scheme, partes.netloc, partes.path),
                         "https://accounts.google.com/o/oauth2/v2/auth")
        q = {k: v[0] for k, v in parse_qs(partes.query).items()}
        self.assertEqual(q, {
            "client_id": "meu-id", "redirect_uri": "http://127.0.0.1:8765/", "response_type": "code",
            "scope": "openid email https://www.googleapis.com/auth/calendar.events "
                     "https://www.googleapis.com/auth/calendar.calendarlist.readonly "
                     "https://www.googleapis.com/auth/gmail.readonly",
            "code_challenge": desafio, "code_challenge_method": "S256", "state": "estado-1",
            "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"})
        self.assertNotIn("+", partes.query)  # espaços como %20

    def test_email_do_id_token(self):
        from apoio_google import id_token
        self.assertEqual(google_auth.email_do_id_token(id_token("ana@gmail.com")), "ana@gmail.com")
        self.assertEqual(google_auth.email_do_id_token("lixo"), "")

    def test_ler_resposta(self):
        url = "http://127.0.0.1:8765/?state=abc&code=4/0AbCd-EfG&scope=email%20openid&authuser=0&prompt=consent"
        self.assertEqual(google_login.ler_resposta(url, "abc"), "4/0AbCd-EfG")
        self.assertEqual(google_login.ler_resposta("  4/0AbCd-EfG \n", "abc"), "4/0AbCd-EfG")
        self.assertEqual(google_login.ler_resposta("127.0.0.1:8765/?code=4%2F0Xy&state=abc", "abc"), "4/0Xy")
        with self.assertRaisesRegex(ValueError, "outra tentativa"):
            google_login.ler_resposta(url, "outro")
        with self.assertRaisesRegex(ValueError, "negado"):
            google_login.ler_resposta("http://127.0.0.1:8765/?error=access_denied&state=abc", "abc")
        with self.assertRaisesRegex(ValueError, "Nada foi colado"):
            google_login.ler_resposta("   ", "abc")


class TesteLogin(BaseGoogle):
    def setUp(self):
        super().setUp()
        self.remendos_login = [
            mock.patch.object(google_login, "URL_TESTE_AGENDA", self.google.base + "/calendar/v3/users/me/calendarList"),
            mock.patch.object(google_login, "URL_TESTE_GMAIL", self.google.base + "/gmail/v1/users/me/profile"),
            mock.patch("secrets.token_urlsafe", lambda n=32: "e" * n),
        ]
        for remendo in self.remendos_login:
            remendo.start()

    def tearDown(self):
        for remendo in self.remendos_login:
            remendo.stop()
        super().tearDown()

    def rodar(self, argumentos, colado=""):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), mock.patch("builtins.input", lambda *_: colado):
            codigo = google_login.main(argumentos)
        return codigo, saida.getvalue()

    def test_autoriza_e_salva(self):
        colado = "http://127.0.0.1:8765/?state=%s&code=4/0codigo-bom&scope=email" % ("e" * 24)
        codigo, saida = self.rodar(["trabalho"], colado)
        self.assertEqual(codigo, 0, saida)
        self.assertIn("https://accounts.google.com/o/oauth2/v2/auth?", saida)
        self.assertIn("não foi possível conectar", saida)
        self.assertIn("Conta 'trabalho' (nova@gmail.com) autorizada.", saida)
        self.assertIn("Reinicie: docker compose restart jarvis-tools hermes", saida)
        self.assertNotIn("rt-novo", saida)
        self.assertNotIn("at-novo", saida)
        self.assertNotIn("Teste com problema", saida)
        self.assertEqual(self.google.login["verificador"], "e" * 64)
        caminho = config.arquivo_conta_google("trabalho")
        self.assertEqual(stat.S_IMODE(os.stat(caminho).st_mode), 0o600)
        salvo = config.ler_json(caminho)
        self.assertEqual((salvo["rotulo"], salvo["email"], salvo["refresh_token"]), ("trabalho", "nova@gmail.com", "rt-novo"))
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", salvo["escopos"])
        self.assertEqual(config.contas_google(), ["faculdade", "pessoal", "trabalho"])

    def test_escopos_parciais_e_codigo_ruim(self):
        self.google.login["scope"] = "openid https://www.googleapis.com/auth/calendar.events"
        codigo, saida = self.rodar(["parcial"], "4/0codigo-bom")
        self.assertEqual(codigo, 0)
        self.assertIn("Permissões não concedidas: ver a lista de agendas, ler os e-mails.", saida)
        codigo, saida = self.rodar(["outra"], "4/0codigo-velho")
        self.assertEqual(codigo, 1)
        self.assertIn("O código expirou ou já foi usado", saida)
        self.assertFalse(config.arquivo_conta_google("outra").exists())

    def test_rotulo_invalido_listar_remover(self):
        self.assertEqual(self.rodar(["Pessoal Nova"])[0], 1)
        self.assertEqual(self.rodar(["cliente"])[0], 1)
        codigo, saida = self.rodar(["--listar"])
        self.assertEqual(saida, "faculdade: ana@alunos.utfpr.edu.br (padrão para criar eventos)\n"
                                "pessoal: ana@gmail.com\n")
        codigo, saida = self.rodar(["--remover", "pessoal"])
        self.assertEqual(codigo, 0)
        self.assertIn("Acesso revogado no Google.", saida)
        self.assertEqual([p.formulario() for p in self.google.pedidos if p.caminho == "/revoke"],
                         [{"token": "rt-pessoal"}])
        self.assertEqual(config.contas_google(), ["faculdade"])


if __name__ == "__main__":
    unittest.main()

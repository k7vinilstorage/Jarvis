"""Testes do registro condicional das ferramentas, do log de chamadas e da linha de comando."""
import asyncio
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from apoio import PastaDados, limpar_ambiente

from app import cli, config, ferramentas, servidor

MOODLE = ["moodle_prazos", "moodle_provas", "moodle_disciplinas", "moodle_conteudo", "moodle_atividade"]
ESCRITA = ["agenda_criar", "agenda_alterar", "agenda_apagar", "agenda_desfazer", "agenda_confirmar"]
TODAS = ["hora", "clima", "buscar", "ler_pagina", *MOODLE, "agenda", *ESCRITA, "emails", "ler_email"]


def nomes(servidor_mcp):
    return [f.name for f in asyncio.run(servidor_mcp.list_tools())]


class TesteRegistro(unittest.TestCase):
    def setUp(self):
        self.dados = PastaDados()
        self.ambiente = mock.patch.dict(os.environ, {**limpar_ambiente(), **self.dados.ambiente()})
        self.ambiente.start()

    def tearDown(self):
        self.ambiente.stop()
        self.dados.apagar()

    def test_so_o_basico_sem_configuracao(self):
        self.assertEqual(config.recursos(), {"moodle": False, "google": [], "busca": False})
        s = servidor.novo_servidor()
        self.assertEqual(servidor.registrar_opcionais(s), ["hora", "clima"])
        self.assertEqual(nomes(s), ["hora", "clima"])

    def test_tudo_configurado(self):
        self.dados.moodle("https://moodle.exemplo.edu.br")
        self.dados.cliente_google()
        self.dados.conta_google("pessoal", "a@gmail.com")
        self.dados.conta_google("faculdade", "a@alunos.utfpr.edu.br")
        with mock.patch.dict(os.environ, {"SEARXNG_URL": "http://searxng:8080"}):
            recursos = config.recursos()
            self.assertEqual(recursos, {"moodle": True, "google": ["faculdade", "pessoal"], "busca": True})
            s = servidor.novo_servidor()
            self.assertEqual(servidor.registrar_opcionais(s), TODAS)
        ferramentas_mcp = {f.name: f for f in asyncio.run(s.list_tools())}
        self.assertEqual(list(ferramentas_mcp), TODAS)
        criar = ferramentas_mcp["agenda_criar"]
        self.assertIn("Não cria nada: devolve a pergunta", criar.description)
        self.assertIn("Contas: faculdade, pessoal.", criar.description)
        self.assertFalse(criar.annotations.read_only_hint)
        self.assertFalse(criar.annotations.destructive_hint)
        self.assertEqual(criar.input_schema["required"], ["titulo", "data"])
        self.assertEqual(criar.input_schema["properties"]["duracao_minutos"],
                         {"default": 60, "description": "Duração em minutos.", "type": "integer"})
        self.assertNotIn("title", criar.input_schema)
        emails = ferramentas_mcp["emails"].input_schema["properties"]
        self.assertEqual((emails["incluir_promocoes"]["type"], emails["incluir_promocoes"]["default"]),
                         ("boolean", False))
        self.assertEqual(emails["quando"]["default"], "recentes")
        self.assertEqual(ferramentas_mcp["ler_email"].input_schema["required"], ["email"])
        self.assertEqual(ferramentas_mcp["moodle_conteudo"].input_schema["required"], ["disciplina"])
        self.assertEqual(ferramentas_mcp["moodle_prazos"].input_schema["properties"]["disciplina"]["default"], "")
        for nome, ferramenta in ferramentas_mcp.items():
            if nome in ESCRITA:
                self.assertFalse(ferramenta.annotations.read_only_hint, nome)
            else:
                self.assertTrue(ferramenta.annotations.read_only_hint, nome)
        self.assertTrue(ferramentas_mcp["agenda_apagar"].annotations.destructive_hint)

    def test_registro_de_novo_tira_as_que_sairam(self):
        s = servidor.novo_servidor()
        servidor.registrar_opcionais(s, {"moodle": True, "google": ["pessoal"], "busca": True})
        self.assertEqual(nomes(s), TODAS)
        servidor.registrar_opcionais(s, {"moodle": True, "google": [], "busca": False})
        self.assertEqual(nomes(s), ["hora", "clima", *MOODLE])

    def test_log_de_chamada_e_erro_inesperado(self):
        async def quebra(consulta):
            raise RuntimeError("http://x/?wstoken=segredo")

        falsa = ferramentas.Ferramenta("buscar", quebra, "x", ferramentas.CONSULTA, "busca")
        with self.assertLogs("jarvis-tools", "INFO") as registro:
            texto = asyncio.run(ferramentas.com_registro(falsa)(consulta="clima em Londrina"))
        self.assertEqual(texto, "Tive um problema inesperado na ferramenta buscar. Tente de novo daqui a pouco.")
        self.assertEqual(registro.output, ["INFO:jarvis-tools:buscar consulta='clima em Londrina'",
                                           "ERROR:jarvis-tools:buscar falhou: RuntimeError"])
        with self.assertLogs("jarvis-tools", "INFO") as registro:
            asyncio.run(ferramentas.com_registro(ferramentas.POR_NOME["emails"])(quando="hoje"))
        self.assertEqual(registro.output[0], "INFO:jarvis-tools:emails quando='hoje' conta='' filtro='' "
                                               "incluir_promocoes=False")


class TesteCli(unittest.TestCase):
    def rodar(self, *argumentos):
        saida, erros = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(erros), \
                mock.patch.object(ferramentas.log, "disabled", True):
            codigo = cli.main(list(argumentos))
        return codigo, saida.getvalue(), erros.getvalue()

    def test_recursos_lista_e_chamada(self):
        with mock.patch.dict(os.environ, {**limpar_ambiente(), "DADOS": "/caminho/que/nao/existe"}):
            codigo, saida, _ = self.rodar("recursos")
            self.assertEqual((codigo, json.loads(saida)), (0, {"moodle": False, "google": [], "busca": False}))
            codigo, saida, _ = self.rodar()
            self.assertIn("agenda_criar(titulo: str, data: str, hora: str = '', duracao_minutos: int = 60, "
                          "conta: str = '', repetir: str = '')", saida)
            self.assertIn("[não configurada", saida)
            codigo, saida, _ = self.rodar("moodle_prazos", "semana")
            self.assertIn("O Moodle não está configurado", saida)
            codigo, saida, _ = self.rodar("hora")
            self.assertTrue(saida.startswith("Agora são "))
        chamadas = []

        async def falsa(quando, conta, filtro, incluir_promocoes):
            chamadas.append((quando, conta, filtro, incluir_promocoes))
            return "ok"

        with mock.patch.object(ferramentas.gmail, "emails", falsa):
            self.assertEqual(self.rodar("emails", "hoje", "", "copel", "true")[1], "ok\n")
            self.assertEqual(self.rodar("emails")[1], "ok\n")
        self.assertEqual(chamadas, [("hoje", "", "copel", True), ("recentes", "", "", False)])
        self.assertEqual(self.rodar("emails", "hoje", "", "", "talvez")[0], 2)
        self.assertEqual(self.rodar("agenda_criar", "Dentista")[2].strip(), "Falta: data")
        self.assertEqual(self.rodar("nao_existe")[0], 2)


if __name__ == "__main__":
    unittest.main()

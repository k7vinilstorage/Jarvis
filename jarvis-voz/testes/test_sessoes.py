"""Testes do histórico por sessão, sem rede."""
import unittest

from app import hermes
from app.sessoes import Sessoes, sessao_valida


class Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


class TesteSessoes(unittest.TestCase):
    def setUp(self):
        self.relogio = Relogio()
        self.sessoes = Sessoes(relogio=self.relogio)

    def test_guarda_pergunta_e_resposta(self):
        self.sessoes.registrar("esp32", "Oi", "Olá, senhor.")
        self.assertEqual(self.sessoes.historico("esp32"), [
            {"role": "user", "content": "Oi"}, {"role": "assistant", "content": "Olá, senhor."}])
        self.assertEqual(self.sessoes.historico("outra"), [])

    def test_so_as_ultimas_8_mensagens(self):
        for i in range(6):
            self.sessoes.registrar("esp32", "pergunta %d" % i, "resposta %d" % i)
        historico = self.sessoes.historico("esp32")
        self.assertEqual(len(historico), 8)
        self.assertEqual(historico[0], {"role": "user", "content": "pergunta 2"})
        self.assertEqual(historico[-1], {"role": "assistant", "content": "resposta 5"})

    def test_expira_depois_de_5_minutos_parada(self):
        self.sessoes.registrar("esp32", "Oi", "Olá.")
        self.relogio.agora += 299
        self.assertEqual(len(self.sessoes.historico("esp32")), 2)
        self.sessoes.registrar("esp32", "E aí", "Tudo certo.")  # renova
        self.relogio.agora += 299
        self.assertEqual(len(self.sessoes.historico("esp32")), 4)
        self.relogio.agora += 302
        self.assertEqual(self.sessoes.historico("esp32"), [])
        self.assertEqual(len(self.sessoes), 0)

    def test_limpar(self):
        self.sessoes.registrar("esp32", "Oi", "Olá.")
        self.assertTrue(self.sessoes.limpar("esp32"))
        self.assertFalse(self.sessoes.limpar("esp32"))
        self.assertEqual(self.sessoes.historico("esp32"), [])

    def test_historico_e_copia(self):
        self.sessoes.registrar("esp32", "Oi", "Olá.")
        self.sessoes.historico("esp32")[0]["content"] = "mudado"
        self.assertEqual(self.sessoes.historico("esp32")[0]["content"], "Oi")

    def test_numero_maximo_de_sessoes(self):
        sessoes = Sessoes(max_sessoes=3, relogio=self.relogio)
        for nome in ("a", "b", "c", "d"):
            sessoes.registrar(nome, "Oi", "Olá.")
        self.assertEqual(sessoes.historico("a"), [])
        self.assertEqual(len(sessoes), 3)

    def test_nomes_validos(self):
        for nome in ("esp32", "sala-1", "pc_quarto", "a.b"):
            self.assertTrue(sessao_valida(nome), nome)
        for nome in ("", "a b", "x" * 65, "../etc", "sessão"):
            self.assertFalse(sessao_valida(nome), nome)

    def test_mensagens_para_o_hermes(self):
        self.sessoes.registrar("esp32", "Oi", "Olá.")
        mensagens = hermes.montar_mensagens(self.sessoes.historico("esp32"), "Que horas são?")
        self.assertEqual(mensagens[0], {"role": "system", "content": hermes.SISTEMA})
        self.assertEqual([m["role"] for m in mensagens], ["system", "user", "assistant", "user"])
        self.assertEqual(mensagens[-1]["content"], "Que horas são?")


if __name__ == "__main__":
    unittest.main()

"""Testes das conferências do scripts/testar-jarvis.py, com respostas de verdade dos relatórios (nomes trocados).

    python3 -m unittest discover -s testes     (na raiz do projeto; só a biblioteca padrão)
"""
import importlib.util
import json
import sys
import unittest
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("testar_jarvis", RAIZ / "scripts" / "testar-jarvis.py")
tj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tj)
jc = tj.jc

DOMINGO = date(2026, 9, 27)  # o dia do relatório de 27/09


def norm(texto):
    return jc.normalizar(texto)


class TesteDatas(unittest.TestCase):
    def test_fim_de_semana_inventado_do_relatorio(self):
        resposta = ("Para o fim de semana em Cornélio Procópio: dia 30 de setembro, sábado, será nublado, com máxima "
                    "de 27 e mínima de 14 graus. E domingo, 1º de outubro, parcialmente nublado.")
        erradas = tj.datas_incoerentes(norm(resposta), DOMINGO)
        self.assertEqual(len(erradas), 2, erradas)
        self.assertIn("30/09/2026 é quarta-feira", erradas[0])
        self.assertIn("01/10/2026 é quinta-feira", erradas[1])

    def test_datas_certas_passam(self):
        for resposta in ("Hoje, domingo, 27 de setembro, você tem duas entregas.",
                         "Sábado, 3 de outubro, e domingo, 4 de outubro, com sol.",
                         "No sábado, 3 de outubro, domingo, 4, chove.",  # "domingo, 4" é outro dia
                         "O último fecha sábado, 17 de outubro.",
                         "Entrega na quinta-feira, 1º de outubro às 23h59.",
                         "Prazo até segunda, primeiro de fevereiro de 2027.",
                         "Hoje é domingo, 27 de setembro de 2026, às 23h."):
            self.assertEqual(tj.datas_incoerentes(norm(resposta), DOMINGO), [], resposta)

    def test_data_que_nao_existe_e_ano_dito(self):
        self.assertIn("não existe", tj.datas_incoerentes(norm("quinta, 31 de setembro"), DOMINGO)[0])
        # 2 de janeiro de 2027 é sábado; o ano dito vale
        self.assertEqual(tj.datas_incoerentes(norm("sábado, 2 de janeiro de 2027"), DOMINGO), [])
        self.assertEqual(len(tj.datas_incoerentes(norm("domingo, 2 de janeiro de 2027"), DOMINGO)), 1)

    def test_proximo_fim_de_semana(self):
        self.assertEqual(tj.proximo_fim_de_semana(DOMINGO), (date(2026, 10, 3), date(2026, 10, 4)))
        self.assertEqual(tj.proximo_fim_de_semana(date(2026, 10, 3)), (date(2026, 10, 3), date(2026, 10, 4)))
        self.assertEqual(tj.proximo_fim_de_semana(date(2026, 9, 24)), (date(2026, 9, 26), date(2026, 9, 27)))
        self.assertTrue(tj.conferir_fim_de_semana(norm("No sábado, 3 de outubro, sol."), DOMINGO))
        self.assertTrue(tj.conferir_fim_de_semana(norm("Domingo, 04/10, chuva."), DOMINGO))
        self.assertFalse(tj.conferir_fim_de_semana(norm("dia 30 de setembro, sábado, nublado"), DOMINGO))
        self.assertFalse(tj.conferir_fim_de_semana(norm("sábado e domingo com sol"), DOMINGO))


class TestePromessas(unittest.TestCase):
    def test_do_relatorio(self):
        for resposta in ("Quer que eu te avise ainda hoje?",
                         "Você concorda com a criação dessa skill?",
                         "Posso te avisar amanhã cedo.",
                         "Vou te lembrar às 8h.",
                         "Quer que eu crie essa skill para você?"):
            self.assertTrue(tj.promessas(norm(resposta)), resposta)

    def test_o_que_pode(self):
        for resposta in ("Pronto, vou lembrar que seu time favorito é o Coritiba.",
                         "Quer que eu crie o evento na agenda?",  # agenda_criar existe
                         "Não consigo te avisar: ainda não tenho lembretes.",
                         "O Moodle avisa por e-mail."):
            self.assertEqual(tj.promessas(norm(resposta)), [], resposta)


class TesteMarkdown(unittest.TestCase):
    def test_do_relatorio(self):
        self.assertTrue(jc.detectar_markdown("O lançamento mais recente é o **Ubuntu 26.04 LTS**."))
        self.assertTrue(jc.detectar_markdown("Aqui estão:\n\n1.  Estude com intervalo.\n2.  Ensine."))
        self.assertFalse(jc.detectar_markdown("Primeiro, estude com intervalo; depois, ensine em voz alta."))


class TesteCasos(unittest.TestCase):
    def test_arquivo_de_casos_valido(self):
        dados = json.loads((RAIZ / "testes" / "jarvis-casos.json").read_text(encoding="utf-8"))
        self.assertEqual(tj.validar(dados["casos"]), [])

    def test_max_chamadas_invalido(self):
        casos = [{"id": "x", "turnos": [{"pergunta": "oi", "max_chamadas": "2"}]}]
        self.assertIn("max_chamadas", tj.validar(casos)[0])


if __name__ == "__main__":
    unittest.main()

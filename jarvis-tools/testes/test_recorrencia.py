"""Testes da repetição de eventos: o pedido em português vira a regra do Google, e a regra vira texto."""
import unittest
from datetime import date, datetime

from app import recorrencia, textos
from app.recorrencia import RepeticaoInvalida, interpretar_repeticao

FUSO = textos.fuso()
SEGUNDA = date(2026, 9, 28)


class TesteInterpretar(unittest.TestCase):
    def r(self, texto, inicio=SEGUNDA, dia_todo=False):
        return interpretar_repeticao(texto, inicio, dia_todo)

    def test_semanal_com_dias(self):
        r = self.r("toda segunda e quarta até 18/12")
        self.assertEqual(r.regra, "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261219T025959Z")  # 23h59 de Brasília
        self.assertEqual(r.descricao, "às segundas e quartas, até sexta-feira, 18 de dezembro")
        self.assertEqual(r.primeiro, SEGUNDA)
        self.assertEqual(self.r("todas as terças e quintas").regra, "RRULE:FREQ=WEEKLY;BYDAY=TU,TH")
        self.assertEqual(self.r("toda terça-feira").descricao, "toda terça, sem data para acabar")

    def test_comeca_no_primeiro_dia_que_bate(self):
        # Pedido numa segunda para "toda quinta": o Google contaria a segunda como 1ª ocorrência
        r = self.r("toda quinta, 10 vezes")
        self.assertEqual(r.primeiro, date(2026, 10, 1))
        self.assertEqual(r.regra, "RRULE:FREQ=WEEKLY;BYDAY=TH;COUNT=10")
        self.assertEqual(r.descricao, "toda quinta, 10 vezes")

    def test_outras_frequencias(self):
        self.assertEqual(self.r("todo dia").regra, "RRULE:FREQ=DAILY")
        self.assertEqual(self.r("diariamente, cinco vezes").regra, "RRULE:FREQ=DAILY;COUNT=5")
        self.assertEqual(self.r("dias úteis").regra, "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")
        self.assertEqual(self.r("de segunda a sexta").descricao, "de segunda a sexta, sem data para acabar")
        self.assertEqual(self.r("a cada 2 semanas").regra, "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO")
        self.assertEqual(self.r("quinzenal").descricao, "a cada 2 semanas, na segunda, sem data para acabar")
        self.assertEqual(self.r("todo mês").descricao, "todo mês, no dia 28, sem data para acabar")
        self.assertEqual(self.r("anual").regra, "RRULE:FREQ=YEARLY")
        self.assertEqual(self.r("todo fim de semana").descricao, "aos sábados e domingos, sem data para acabar")
        self.assertEqual(self.r("toda semana").regra, "RRULE:FREQ=WEEKLY;BYDAY=MO")
        misto = self.r("toda sexta e sábado")
        self.assertEqual(misto.descricao, "às sextas e aos sábados, sem data para acabar")

    def test_dia_todo_usa_data_no_fim(self):
        self.assertEqual(self.r("todo dia até 5/10", dia_todo=True).regra, "RRULE:FREQ=DAILY;UNTIL=20261005")

    def test_invalidos(self):
        for texto, trecho in (("sempre", "Não entendi a repetição"), ("todo dia, 1 vez", "entre 2 e"),
                              ("toda sexta até 01/10/2026", "vem antes do primeiro dia"),
                              ("toda sexta até dezembro", "Não entendi até quando"), ("", "Use toda semana")):
            with self.assertRaises(RepeticaoInvalida) as erro:
                self.r(texto)
            self.assertIn(trecho, str(erro.exception), texto)


class TesteRegrasExistentes(unittest.TestCase):
    def test_descrever(self):
        hoje = SEGUNDA
        d = recorrencia.descrever_regra(["RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20261216T025959Z"], date(2026, 8, 4), hoje)
        self.assertEqual(d, "toda terça, até terça-feira, 15 de dezembro")
        self.assertEqual(recorrencia.descrever_regra(["EXDATE:20261006", "RRULE:FREQ=DAILY;COUNT=3"], hoje, hoje),
                         "todo dia, 3 vezes")
        self.assertEqual(recorrencia.descrever_regra(["RRULE:FREQ=MONTHLY;BYDAY=1MO"], hoje, hoje),
                         "todo mês, no dia 28, sem data para acabar")
        self.assertEqual(recorrencia.descrever_regra(["RRULE:FREQ=SECONDLY"], hoje, hoje), "que se repete")
        self.assertEqual(recorrencia.descrever_regra([], hoje, hoje), "que se repete")

    def test_cortar_e_contar(self):
        linhas = ["EXDATE;TZID=America/Sao_Paulo:20261006T130000", "RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=15"]
        corte = datetime(2026, 10, 13, 12, 59, 59, tzinfo=FUSO)
        self.assertEqual(recorrencia.cortar_regra(linhas, corte, False),
                         [linhas[0], "RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20261013T155959Z"])
        self.assertEqual(recorrencia.cortar_regra(["RRULE:FREQ=DAILY;UNTIL=20261231"], date(2026, 10, 12), True),
                         ["RRULE:FREQ=DAILY;UNTIL=20261012"])
        self.assertEqual(recorrencia.com_vezes(["RRULE:FREQ=WEEKLY;UNTIL=20261231T000000Z;BYDAY=TU"], 4),
                         ["RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=4"])


if __name__ == "__main__":
    unittest.main()

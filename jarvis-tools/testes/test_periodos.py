"""Testes dos períodos falados ('hoje', 'semana', 'sexta', '26/09'...)."""
import unittest
from datetime import date, datetime, timedelta

from app import periodos, textos
from app.periodos import PeriodoInvalido, interpretar, interpretar_recente

FUSO = textos.fuso()
AGORA = datetime(2026, 9, 24, 19, 0, tzinfo=FUSO)  # quinta-feira, 19h


def dias(periodo):
    return periodo.inicio.date(), periodo.fim.date()


class TesteInterpretar(unittest.TestCase):
    def p(self, texto, **extras):
        return interpretar(texto, AGORA, **extras)

    def test_hoje_amanha_depois(self):
        hoje = self.p("hoje")
        self.assertEqual((hoje.inicio, hoje.fim), (datetime(2026, 9, 24, tzinfo=FUSO), datetime(2026, 9, 25, tzinfo=FUSO)))
        self.assertEqual(hoje.alcance, "para hoje, quinta-feira, 24 de setembro")
        self.assertTrue(hoje.um_dia)
        self.assertEqual(self.p("").rotulo, hoje.rotulo)
        self.assertEqual(self.p("Hoje à noite").rotulo, hoje.rotulo)
        self.assertEqual(self.p("amanhã").rotulo, "amanhã, sexta-feira, 25 de setembro")
        self.assertEqual(self.p("AMANHA").dia, date(2026, 9, 25))
        self.assertEqual(self.p("depois de amanhã").rotulo, "depois de amanhã, sábado, 26 de setembro")
        self.assertEqual(self.p("ontem").dia, date(2026, 9, 23))

    def test_semana_e_mes_comecam_agora(self):
        semana = self.p("semana")
        self.assertEqual(semana.inicio, AGORA)
        # vai até o fim do dia citado no rótulo (antes parava às 19h e um prazo às 23h59 sumia)
        self.assertEqual(semana.fim, datetime(2026, 10, 2, 0, 0, tzinfo=FUSO))
        self.assertEqual(semana.alcance, "até quinta-feira, 1º de outubro")
        self.assertFalse(semana.um_dia)
        for variante in ("esta semana", "próximos 7 dias", "sete dias", "Nesta Semana"):
            self.assertEqual(self.p(variante).fim, semana.fim, variante)
        mes = self.p("mês")
        self.assertEqual(mes.fim, datetime(2026, 10, 24, 19, 0, tzinfo=FUSO))
        self.assertEqual(mes.alcance, "nos próximos 30 dias")
        self.assertEqual(self.p("este mes").fim, mes.fim)

    def test_dias_da_semana(self):
        self.assertEqual(self.p("sexta").dia, date(2026, 9, 25))
        self.assertEqual(self.p("sexta-feira").rotulo, "amanhã, sexta-feira, 25 de setembro")
        self.assertEqual(self.p("quinta").dia, date(2026, 9, 24))  # hoje conta
        self.assertEqual(self.p("próxima quinta").dia, date(2026, 10, 1))
        self.assertEqual(self.p("segunda-feira").rotulo, "segunda-feira, 28 de setembro")
        self.assertEqual(self.p("sábado").dia, date(2026, 9, 26))
        self.assertEqual(self.p("domingo").dia, date(2026, 9, 27))

    def test_datas(self):
        self.assertEqual(self.p("26/09").dia, date(2026, 9, 26))
        self.assertEqual(self.p("26/09/2026").dia, date(2026, 9, 26))
        self.assertEqual(self.p("5/10/26").dia, date(2026, 10, 5))
        self.assertEqual(self.p("2026-10-05").dia, date(2026, 10, 5))
        self.assertEqual(self.p("dia 30").dia, date(2026, 9, 30))
        self.assertEqual(self.p("dia 10").dia, date(2026, 10, 10))  # já passou neste mês: o próximo
        self.assertEqual(self.p("dia 31").dia, date(2026, 10, 31))  # setembro não tem 31
        self.assertEqual(self.p("5 de outubro").dia, date(2026, 10, 5))
        self.assertEqual(self.p("10/09").dia, date(2027, 9, 10))  # sem ano: a próxima vez
        self.assertEqual(self.p("10/09/2026").rotulo, "quinta-feira, 10 de setembro")
        self.assertEqual(self.p("02/01/2027").rotulo, "sábado, 2 de janeiro de 2027")
        self.assertEqual(self.p("1/10").rotulo, "quinta-feira, 1º de outubro")

    def test_fim_de_semana(self):
        fds = self.p("fim de semana")
        self.assertEqual(dias(fds), (date(2026, 9, 26), date(2026, 9, 28)))
        self.assertEqual(fds.alcance, "no fim de semana, 26 e 27 de setembro")
        self.assertEqual(dias(self.p("final de semana")), dias(fds))
        # No domingo, o fim de semana é o próximo (antes vinha só o domingo de hoje)
        domingo = interpretar("fim de semana", datetime(2026, 9, 27, 22, 59, tzinfo=FUSO))
        self.assertEqual(dias(domingo), (date(2026, 10, 3), date(2026, 10, 5)))
        self.assertEqual(domingo.alcance, "no fim de semana, 3 e 4 de outubro")
        sabado = interpretar("fim de semana", datetime(2026, 10, 3, 10, tzinfo=FUSO))
        self.assertEqual(dias(sabado), (date(2026, 10, 3), date(2026, 10, 5)))
        virada = interpretar("fim de semana", datetime(2026, 10, 29, 10, tzinfo=FUSO))
        self.assertEqual(virada.rotulo, "no fim de semana, 31 de outubro e 1º de novembro")

    def test_proximas_semanas(self):
        # "próximas semanas" dava erro, e o modelo caía para "semana" (e perdia a entrega de 10/10)
        agora = datetime(2026, 9, 27, 22, 59, tzinfo=FUSO)
        for texto in ("próximas semanas", "nas próximas semanas", "semanas"):
            p = interpretar(texto, agora)
            self.assertEqual(p.rotulo, "nos próximos 30 dias", texto)
            self.assertEqual(p.fim, agora + timedelta(days=30))
        duas = interpretar("próximas 2 semanas", agora)
        self.assertEqual(duas.rotulo, "até domingo, 11 de outubro")
        self.assertEqual(dias(interpretar("duas semanas", agora)), dias(duas))
        self.assertEqual(interpretar("próximas 8 semanas", agora).rotulo, "nos próximos 30 dias")
        self.assertEqual(interpretar("semana", agora).rotulo, "até domingo, 4 de outubro")  # não mudou

    def test_semana_que_vem(self):
        p = self.p("semana que vem")
        self.assertEqual(dias(p), (date(2026, 9, 28), date(2026, 10, 5)))
        self.assertEqual(p.rotulo, "na semana que vem, de 28 de setembro a 4 de outubro")

    def test_erros_com_ajuda(self):
        with self.assertRaises(PeriodoInvalido) as erro:
            self.p("xyz")
        self.assertIn('Não entendi o período "xyz"', str(erro.exception))
        self.assertIn("hoje, amanhã, depois de amanhã", str(erro.exception))
        with self.assertRaises(PeriodoInvalido) as erro:
            self.p("31/02")
        self.assertIn("A data 31/02 não existe", str(erro.exception))
        with self.assertRaises(PeriodoInvalido) as erro:
            self.p("semana", um_dia=True)
        self.assertEqual(str(erro.exception), periodos.AJUDA_UM_DIA)
        self.assertEqual(self.p("sexta", um_dia=True).dia, date(2026, 9, 25))
        for passado in ("semana passada", "mês passado", "sexta passada", "anteontem"):
            with self.assertRaises(PeriodoInvalido) as erro:
                self.p(passado)
            self.assertIn("10/09/2026", str(erro.exception), passado)


class TesteRecente(unittest.TestCase):
    def test_periodos_para_tras(self):
        hoje = interpretar_recente("hoje", AGORA)
        self.assertEqual((hoje.inicio, hoje.fim, hoje.rotulo), (datetime(2026, 9, 24, tzinfo=FUSO), AGORA, "hoje"))
        for texto in ("ontem", "2 dias", "dois dias", "desde ontem"):
            p = interpretar_recente(texto, AGORA)
            self.assertEqual((p.inicio.date(), p.rotulo), (date(2026, 9, 23), "desde ontem"), texto)
        semana = interpretar_recente("semana", AGORA)
        self.assertEqual((semana.inicio.date(), semana.rotulo), (date(2026, 9, 18), "nos últimos 7 dias"))
        self.assertEqual(interpretar_recente("3 dias", AGORA).rotulo, "nos últimos 3 dias")
        self.assertEqual(interpretar_recente("", AGORA).rotulo, "hoje")

    def test_erros(self):
        with self.assertRaises(PeriodoInvalido) as erro:
            interpretar_recente("ano passado", AGORA)
        self.assertIn("Use hoje, ontem, 2 dias ou semana.", str(erro.exception))
        with self.assertRaises(PeriodoInvalido):
            interpretar_recente("90 dias", AGORA)


class TesteTextosFalados(unittest.TestCase):
    def test_horas_com_artigo(self):
        m = lambda h, mi=0: datetime(2026, 9, 24, h, mi, tzinfo=FUSO)  # noqa: E731
        self.assertEqual(textos.as_hora(m(15, 30)), "às 15h30")
        self.assertEqual(textos.as_hora(m(1)), "à 1h")
        self.assertEqual(textos.as_hora(m(0)), "à meia-noite")
        self.assertEqual(textos.as_hora(m(12)), "ao meio-dia")
        self.assertEqual(textos.faixa_horas(m(9), m(10)), "das 9h às 10h")
        self.assertEqual(textos.faixa_horas(m(12), m(13)), "do meio-dia às 13h")
        self.assertEqual(textos.faixa_horas(m(19), m(19)), "às 19h")
        self.assertEqual(textos.data_falada(date(2026, 10, 1)), "quinta-feira, 1º de outubro")
        self.assertEqual(textos.dia_curto(date(2026, 9, 26), date(2026, 9, 24)), "sábado")
        self.assertEqual(textos.dia_curto(date(2026, 10, 5), date(2026, 9, 24)), "segunda, 5 de outubro")
        self.assertEqual(textos.cortar("uma frase bem comprida demais", 15), "uma frase bem…")
        self.assertEqual(textos.juntar_com_e(["A", "B", "C"]), "A, B e C")


if __name__ == "__main__":
    unittest.main()

"""Testes de alterar, apagar e desfazer eventos (Google Agenda falso), inclusive séries que se repetem."""
import asyncio
import unittest
from unittest import mock

from apoio_google import evento
from test_agenda import AGORA, BaseAgenda

from app import agenda, agenda_mudancas as mud

REGRA = "RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20261216T025959Z"  # toda terça até 15/12, 23h59 de Brasília


def ocorrencia(dia):
    """Uma ocorrência da série "Inglês com Jean" (terças, 13h às 14h30), como a API devolve com singleEvents."""
    return evento("ing_%sT160000Z" % dia.replace("-", ""), "Inglês com Jean", dia + "T13:00:00-03:00",
                  dia + "T14:30:00-03:00", recurringEventId="ing",
                  originalStartTime={"dateTime": dia + "T13:00:00-03:00"})


class BaseMudancas(BaseAgenda):
    def setUp(self):
        super().setUp()
        pessoal = self.google.contas["pessoal"]
        self.mestre = evento("ing", "Inglês com Jean", "2026-09-01T13:00:00-03:00", "2026-09-01T14:30:00-03:00",
                             recurrence=[REGRA])
        pessoal["mestres"]["ana@gmail.com"] = {"ing": self.mestre}
        terças = ["2026-09-%02d" % d for d in (1, 8, 15, 22, 29)] + ["2026-10-%02d" % d for d in (6, 13, 20)]
        pessoal["eventos"]["ana@gmail.com"] += [ocorrencia(d) for d in terças] + [
            evento("conv", "Reunião do grupo", "2026-09-30T10:00:00-03:00", "2026-09-30T11:00:00-03:00",
                   organizer={"email": "prof@exemplo.com"})]
        agenda._pendentes.clear()
        self.segundos = 1000.0
        remendo = mock.patch.object(agenda, "relogio", lambda: self.segundos)
        remendo.start()
        self.addCleanup(remendo.stop)

    def rodar(self, funcao, *args, **kwargs):
        return asyncio.run(funcao(*args, agora=AGORA, **kwargs))

    def confirmado(self, funcao, *args, **kwargs):
        """Como no uso: a 1ª chamada pergunta; o usuário responde (20 s depois) e a mesma chamada faz."""
        primeira = self.rodar(funcao, *args, **kwargs)
        self.assertTrue(primeira.startswith("Ainda não"), primeira)
        self.assertEqual(self.google.mudancas, [])  # nada mudou antes do sim
        self.segundos += 20
        return self.rodar(funcao, *args, resposta_do_usuario="sim, pode", **kwargs)

    def desfazer(self):
        primeira = asyncio.run(mud.desfazer())
        self.assertTrue(primeira.startswith("Ainda não desfiz"), primeira)
        self.segundos += 20
        return asyncio.run(mud.desfazer("sim"))


class TesteApagar(BaseMudancas):
    def test_avulso_em_dois_passos_e_desfazer(self):
        primeira = self.rodar(mud.apagar, "dentista")
        self.assertEqual(primeira, 'Ainda não apaguei. Pergunte ao usuário, com estas palavras: "Posso apagar '
                                   '"Dentista", sexta-feira, 25 de setembro, das 15h às 16h, na conta pessoal?" Se ele '
                                   "disser que sim, chame agenda_apagar de novo com os mesmos dados e com a resposta "
                                   "dele em resposta_do_usuario.")
        self.segundos += 1.5  # encadeou no mesmo turno
        self.assertIn("falta a resposta do usuário", self.rodar(mud.apagar, "dentista"))
        self.segundos += 20
        self.assertEqual(self.rodar(mud.apagar, "dentista", resposta_do_usuario="pode apagar"),
                         'Apagado na conta pessoal: "Dentista", sexta-feira, 25 de setembro, das 15h às 16h. Dá para '
                         "desfazer em até 24 horas.")
        self.assertEqual(self.google.mudancas, [("DELETE", "ana@gmail.com", "e5", None)])
        self.assertTrue(self.desfazer().startswith('Desfeito: Apagou "Dentista"'))
        recriado = self.google.criados[-1][1]
        self.assertEqual((recriado["summary"], recriado["start"]["dateTime"]), ("Dentista", "2026-09-25T15:00:00-03:00"))
        self.assertIn("Não há nenhuma mudança", asyncio.run(mud.desfazer()))  # já desfeita

    def test_serie_pergunta_o_alcance(self):
        texto = self.rodar(mud.apagar, "inglês")
        self.assertEqual(texto, '"Inglês com Jean" se repete (toda terça, até terça-feira, 15 de dezembro). Pergunte '
                                "ao usuário se é para apagar só a de terça-feira, 29 de setembro, esta e as próximas, "
                                "ou todas; depois chame de novo com alcance esta, proximas ou todas.")
        self.assertEqual(self.google.mudancas, [])

    def test_so_esta(self):
        texto = self.confirmado(mud.apagar, "inglês", "terça", "só esta")
        self.assertIn("só a de terça-feira, 29 de setembro, das 13h às 14h30", texto)
        self.assertEqual(self.google.mudancas, [("DELETE", "ana@gmail.com", "ing_20260929T160000Z", None)])
        self.assertIn("volta como evento avulso", self.desfazer())

    def test_esta_e_as_proximas_corta_a_serie(self):
        texto = self.confirmado(mud.apagar, "aula de inglês", "6/10", "esta e as próximas")
        self.assertIn("esta e as próximas, a partir de terça-feira, 6 de outubro", texto)
        self.assertEqual(self.google.mudancas, [
            ("PATCH", "ana@gmail.com", "ing", {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20261006T155959Z"]})])
        self.desfazer()
        self.assertEqual(self.google.mudancas[-1], ("PATCH", "ana@gmail.com", "ing", {"recurrence": [REGRA]}))

    def test_todas_e_desfazer_recria_a_serie(self):
        self.confirmado(mud.apagar, "inglês", alcance="todas")
        self.assertEqual(self.google.mudancas, [("DELETE", "ana@gmail.com", "ing", None)])
        self.desfazer()
        self.assertEqual(self.google.criados[-1][1]["recurrence"], [REGRA])

    def test_na_primeira_ocorrencia_proximas_vira_todas(self):
        self.google.contas["pessoal"]["mestres"]["ana@gmail.com"]["ing"]["start"]["dateTime"] = \
            "2026-09-29T13:00:00-03:00"
        self.confirmado(mud.apagar, "inglês", "terça", "proximas")
        self.assertEqual(self.google.mudancas, [("DELETE", "ana@gmail.com", "ing", None)])

    def test_convite_nao_mexe(self):
        texto = self.rodar(mud.apagar, "reunião do grupo")
        self.assertEqual(texto, '"Reunião do grupo" é um convite de prof@exemplo.com: não mexo em eventos de outras '
                                "pessoas. O usuário pode recusar pelo Google Agenda.")

    def test_quando_invalido_procura_nos_proximos_dias(self):
        # 28/09: o modelo mandou quando="25//11"; o título basta
        self.assertIn('"Inglês com Jean" se repete', self.rodar(mud.apagar, "inglês", "25//11"))

    def test_varios_ou_nenhum(self):
        self.assertIn('Achei mais de um evento com "academia"', self.rodar(mud.apagar, "academia"))  # nas 2 contas
        self.assertIn("Não achei nenhum evento", self.rodar(mud.apagar, "pilates"))
        self.assertIn('Não entendi o alcance "às vezes"', self.rodar(mud.apagar, "inglês", alcance="às vezes"))
        self.assertEqual(self.google.mudancas, [])


class TesteAlterar(BaseMudancas):
    def test_avulso_dia_e_hora(self):
        texto = self.confirmado(mud.alterar, "dentista", nova_data="sábado", nova_hora="10h")
        self.assertIn('"Dentista", sexta-feira, 25 de setembro, das 15h às 16h: sábado, 26 de setembro, das 10h às 11h',
                      texto)
        metodo, agenda_id, id_, corpo = self.google.mudancas[0]
        self.assertEqual((metodo, id_), ("PATCH", "e5"))
        self.assertEqual(corpo["start"]["dateTime"], "2026-09-26T10:00:00-03:00")
        self.assertEqual(corpo["end"]["dateTime"], "2026-09-26T11:00:00-03:00")
        self.desfazer()
        self.assertEqual(self.google.mudancas[-1][3]["start"]["dateTime"], "2026-09-25T15:00:00-03:00")

    def test_so_o_titulo_nao_mexe_no_horario(self):
        self.confirmado(mud.alterar, "dentista", novo_titulo="Dentista (retorno)")
        self.assertEqual(self.google.mudancas[0][3], {"summary": "Dentista (retorno)"})

    def test_hora_de_todas(self):
        texto = self.confirmado(mud.alterar, "inglês", alcance="todas", nova_hora="14h")
        self.assertIn("todas as ocorrências: das 14h às 15h30", texto)
        corpo = self.google.mudancas[0][3]
        self.assertEqual(self.google.mudancas[0][2], "ing")
        self.assertEqual(corpo["start"]["dateTime"], "2026-09-01T14:00:00-03:00")  # a série toda, desde o início
        self.assertEqual(corpo["end"]["dateTime"], "2026-09-01T15:30:00-03:00")

    def test_esta_e_as_proximas_divide_a_serie(self):
        texto = self.confirmado(mud.alterar, "inglês", "13/10", "proximas", nova_hora="15h")
        self.assertIn("esta e as próximas, a partir de terça-feira, 13 de outubro", texto)
        self.assertEqual(self.google.mudancas[0], (
            "PATCH", "ana@gmail.com", "ing", {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL=20261013T155959Z"]}))
        nova = self.google.criados[-1][1]
        self.assertEqual(nova["recurrence"], [REGRA])
        self.assertEqual(nova["start"]["dateTime"], "2026-10-13T15:00:00-03:00")
        self.assertEqual(nova["end"]["dateTime"], "2026-10-13T16:30:00-03:00")
        self.desfazer()
        self.assertEqual(self.google.mudancas[-2:], [("PATCH", "ana@gmail.com", "ing", {"recurrence": [REGRA]}),
                                                     ("DELETE", "ana@gmail.com", nova["id"], None)])

    def test_serie_com_quantidade_conta_as_que_faltam(self):
        self.mestre["recurrence"] = ["RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=15"]
        self.confirmado(mud.alterar, "inglês", "13/10", "proximas", nova_hora="15h")
        # Antes de 13/10 já foram 6 (1º/9 a 6/10): a nova série fica com 9
        self.assertEqual(self.google.criados[-1][1]["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=9"])

    def test_dia_de_uma_serie_nao(self):
        texto = self.rodar(mud.alterar, "inglês", alcance="todas", nova_data="quarta")
        self.assertIn("Para mudar o dia de uma série", texto)
        self.assertIn("Diga o que mudar", self.rodar(mud.alterar, "inglês"))
        self.assertEqual(self.google.mudancas, [])


class TesteCriarRecorrente(BaseMudancas):
    def test_toda_terca(self):
        texto = self.confirmado(agenda.criar, "Inglês", "terça", "13h", 90, repetir="toda terça até 15/12")
        self.assertIn("Inglês, terça-feira, 29 de setembro, das 13h às 14h30, repetindo toda terça, até terça-feira, "
                      "15 de dezembro", texto)
        criado = self.google.criados[-1][1]
        self.assertEqual(criado["recurrence"], [REGRA])
        self.assertEqual(criado["start"]["dateTime"], "2026-09-29T13:00:00-03:00")

    def test_repeticao_invalida(self):
        self.assertIn("Não entendi a repetição", self.rodar(agenda.criar, "X", "terça", "13h", repetir="sempre"))


if __name__ == "__main__":
    unittest.main()

"""Testes da Agenda (Google Calendar falso local): leitura de várias contas e criação de eventos."""
import asyncio
import os
import unittest
from datetime import datetime
from unittest import mock

from apoio import PastaDados, limpar_ambiente
from apoio_google import GoogleFalso, evento

from app import agenda, google_auth, textos

FUSO = textos.fuso()
AGORA = datetime(2026, 9, 24, 8, 0, tzinfo=FUSO)  # quinta-feira, 8h

AGENDAS_PESSOAL = [
    {"id": "ana@gmail.com", "summary": "ana@gmail.com", "primary": True, "selected": True, "accessRole": "owner"},
    {"id": "familia#contacts@group.v.calendar.google.com", "summary": "Aniversários", "selected": True,
     "accessRole": "reader"},
    {"id": "feriados@group.v.calendar.google.com", "summary": "Feriados", "accessRole": "reader"},  # não selecionada
]


class BaseAgenda(unittest.TestCase):
    def setUp(self):
        self.google = GoogleFalso()
        self.google.conta("pessoal", "ana@gmail.com", agendas=AGENDAS_PESSOAL, eventos={
            "ana@gmail.com": [
                evento("e1", "Reunião do projeto", "2026-09-24T09:00:00-03:00", "2026-09-24T10:00:00-03:00"),
                evento("e2", "Academia", "2026-09-24T19:00:00-03:00", "2026-09-24T20:00:00-03:00"),
                evento("e3", "Cancelado", "2026-09-24T11:00:00-03:00", "2026-09-24T12:00:00-03:00",
                       status="cancelled"),
                evento("e4", "Recusei", "2026-09-24T13:00:00-03:00", "2026-09-24T14:00:00-03:00",
                       attendees=[{"email": "ana@gmail.com", "self": True, "responseStatus": "declined"}]),
                evento("e5", "Dentista", "2026-09-25T15:00:00-03:00", "2026-09-25T16:00:00-03:00"),
                evento("e6", "Viagem", "2026-09-26T22:00:00-03:00", "2026-09-28T18:00:00-03:00"),
            ],
            "familia#contacts@group.v.calendar.google.com": [
                evento("a1", "Aniversário da Ana", "2026-09-24", "2026-09-25"),
                evento("e1", "Reunião do projeto", "2026-09-24T09:00:00-03:00", "2026-09-24T10:00:00-03:00"),
            ],
            "feriados@group.v.calendar.google.com": [evento("f1", "Não aparece", "2026-09-24", "2026-09-25")],
        })
        self.google.conta("faculdade", "ana@alunos.utfpr.edu.br", eventos={"ana@alunos.utfpr.edu.br": [
            evento("u1", "Aula de Cálculo", "2026-09-24T13:00:00-03:00", "2026-09-24T14:40:00-03:00"),
            evento("u2", "Academia", "2026-09-24T19:00:00-03:00", "2026-09-24T20:00:00-03:00"),  # mesmo da pessoal
            evento("u3", "Semana acadêmica", "2026-09-28", "2026-10-01"),
        ]})
        self.dados = PastaDados()
        self.dados.cliente_google()
        self.dados.conta_google("pessoal", "ana@gmail.com")
        self.dados.conta_google("faculdade", "ana@alunos.utfpr.edu.br")
        self.remendos = [
            mock.patch.dict(os.environ, {**limpar_ambiente(), **self.dados.ambiente()}),
            mock.patch.object(google_auth, "URL_TOKEN", self.google.base + "/token"),
            mock.patch.object(agenda, "URL_CALENDARIO", self.google.base + "/calendar/v3"),
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

    def ler(self, quando="hoje", conta=""):
        texto = asyncio.run(agenda.agenda(quando, conta, agora=AGORA))
        corpo, _, ultima = texto.rpartition("\n")
        self.orientacao = ultima if ultima.startswith("Ao responder:") else ""
        return corpo if self.orientacao else texto


class TesteLeitura(BaseAgenda):
    def test_hoje_todas_as_contas(self):
        self.assertEqual(self.ler("hoje"),
                         "Hoje, quinta-feira, 24 de setembro: das 9h às 10h, Reunião do projeto (pessoal); "
                         "das 13h às 14h40, Aula de Cálculo (faculdade); das 19h às 20h, Academia (faculdade). "
                         "Dia todo: Aniversário da Ana (pessoal).")
        eventos = [p for p in self.google.pedidos if p.caminho.endswith("/events")]
        self.assertEqual(len(eventos), 3)  # 2 agendas da pessoal (a não selecionada fica fora) + 1 da faculdade
        pedido = eventos[0]
        self.assertEqual({k: v[0] for k, v in pedido.consulta.items()}, {
            "timeMin": "2026-09-24T00:00:00-03:00", "timeMax": "2026-09-25T00:00:00-03:00", "singleEvents": "true",
            "orderBy": "startTime", "maxResults": "50"})
        lista = [p for p in self.google.pedidos if p.caminho.endswith("/calendarList")][0]
        self.assertEqual(lista.valor("minAccessRole"), "reader")
        self.assertTrue(any("familia%23contacts%40group" in p.caminho or "familia#contacts" in p.caminho
                            for p in eventos))

    def test_uma_conta_sem_rotulo(self):
        self.assertEqual(self.ler("hoje", "pessoal"),
                         "Hoje, quinta-feira, 24 de setembro: das 9h às 10h, Reunião do projeto; das 19h às 20h, "
                         "Academia. Dia todo: Aniversário da Ana.")
        self.assertEqual(self.ler("amanhã", "ana@gmail.com"),
                         "Amanhã, sexta-feira, 25 de setembro: das 15h às 16h, Dentista.")

    def test_semana_agrupada(self):
        texto = self.ler("semana", "pessoal")
        self.assertEqual(self.orientacao, "Ao responder: em frases corridas, sem lista, diga que são 5 e cite só os 3 "
                                          "primeiros; os outros, só se o usuário pedir.")
        self.assertEqual(texto, "Agenda até quinta-feira, 1º de outubro, com 5 eventos. "
                                "Hoje, quinta-feira, 24 de setembro: das 9h às 10h, Reunião do projeto; das 19h às 20h, "
                                "Academia. Dia todo: Aniversário da Ana. "
                                "Amanhã, sexta-feira, 25 de setembro: das 15h às 16h, Dentista. "
                                "Sábado, 26 de setembro: das 22h até segunda às 18h, Viagem.")
        texto = self.ler("fim de semana", "faculdade")
        self.assertEqual(texto, "Nada na agenda no fim de semana, 26 e 27 de setembro.")
        texto = self.ler("semana que vem", "faculdade")
        self.assertEqual(texto, "Agenda na semana que vem, de 28 de setembro a 4 de outubro, com 1 evento. "
                                "Segunda-feira, 28 de setembro: dia todo, Semana acadêmica (até quarta).")

    def test_vazio_limite_e_erros(self):
        self.assertEqual(self.ler("domingo", "pessoal"),
                         "Domingo, 27 de setembro: desde sábado às 22h até segunda às 18h, Viagem.")
        self.assertEqual(self.ler("30/09", "pessoal"), "Nada na agenda para quarta-feira, 30 de setembro.")
        self.google.contas["pessoal"]["eventos"]["ana@gmail.com"] = [
            evento("n%d" % i, "Evento %d" % i, "2026-09-25T%02d:00:00-03:00" % (6 + i), "2026-09-25T%02d:30:00-03:00" % (6 + i))
            for i in range(15)]
        texto = self.ler("amanhã", "pessoal")
        self.assertTrue(texto.endswith("das 17h às 17h30, Evento 11. E mais 3 eventos."), texto)
        self.assertIn("Só olho de hoje em diante", self.ler("mês passado"))
        self.assertIn("Não entendi o período", self.ler("qualquer hora"))
        self.assertIn('não conheço a conta "trabalho"', self.ler("hoje", "trabalho").lower())

    def test_sem_permissao_da_lista_usa_a_principal(self):
        self.google.sem_permissao_lista.add("pessoal")
        self.assertEqual(self.ler("hoje", "pessoal"),
                         "Hoje, quinta-feira, 24 de setembro: das 9h às 10h, Reunião do projeto; das 19h às 20h, "
                         "Academia.")
        eventos = [p.caminho for p in self.google.pedidos if p.caminho.endswith("/events")]
        self.assertEqual(eventos, ["/calendar/v3/calendars/primary/events"])

    def test_uma_conta_revogada(self):
        self.google.revogados.add("rt-faculdade")
        texto = self.ler("hoje")
        self.assertTrue(texto.startswith("Hoje, quinta-feira, 24 de setembro: das 9h às 10h, Reunião do projeto "
                                         "(pessoal)"), texto)
        self.assertTrue(texto.endswith("Atenção: A autorização da conta faculdade expirou ou foi revogada; rode "
                                       "google_login faculdade de novo."), texto)
        self.google.revogados.add("rt-pessoal")
        google_auth.esquecer_tokens()
        self.assertTrue(self.ler("hoje").startswith("Não consegui ler a agenda. A autorização da conta"))


class TesteCriacao(BaseAgenda):
    def setUp(self):
        super().setUp()
        agenda._pendentes.clear()
        self.segundos = 1000.0
        remendo = mock.patch.object(agenda, "relogio", lambda: self.segundos)
        remendo.start()
        self.addCleanup(remendo.stop)

    def pedir(self, *args, **kwargs):
        return asyncio.run(agenda.criar(*args, agora=AGORA, **kwargs))

    def criar(self, *args, **kwargs):
        """Como no uso de verdade: pede, o usuário confirma (alguns segundos depois) e pede de novo."""
        primeira = self.pedir(*args, **kwargs)
        if not primeira.startswith("Ainda não criei"):
            return primeira
        self.segundos += 20
        return self.pedir(*args, **kwargs)

    def test_so_cria_depois_da_confirmacao(self):
        # Em 27/09 o modelo criou um evento sem perguntar: agora a 1ª chamada só guarda o pedido
        with mock.patch.dict(os.environ, {"GOOGLE_CONTA_PADRAO": "pessoal"}):
            texto = self.pedir("Estudar", "amanhã", "8h")
            self.assertEqual(texto, "Ainda não criei. Pergunte ao usuário: posso criar Estudar, sexta-feira, 25 de "
                                    "setembro, das 8h às 9h, na conta pessoal? Só chame agenda_criar de novo, com os "
                                    "mesmos dados, depois que ele disser que sim.")
            self.segundos += 1.5  # encadeou no mesmo turno, sem o usuário responder
            self.assertIn("o usuário não confirmou", self.pedir("Estudar", "amanhã", "8h"))
            self.assertEqual(self.google.criados, [])
            self.segundos += 5  # o usuário respondeu "sim"
            self.assertEqual(self.pedir("estudar", "25/09", "08:00"),
                             "Evento criado na conta pessoal: estudar, sexta-feira, 25 de setembro, das 8h às 9h.")
            self.assertEqual(len(self.google.criados), 1)

    def test_outro_evento_ou_pedido_velho_pergunta_de_novo(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CONTA_PADRAO": "pessoal"}):
            self.pedir("Estudar", "amanhã", "8h")
            self.segundos += 20
            self.assertTrue(self.pedir("Estudar", "amanhã", "9h").startswith("Ainda não criei"))  # outra hora
            self.segundos += 601
            self.assertTrue(self.pedir("Estudar", "amanhã", "8h").startswith("Ainda não criei"))  # passou de 10 min
            self.assertEqual(self.google.criados, [])

    def test_evento_com_hora(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CONTA_PADRAO": "pessoal"}):
            texto = self.criar("Dentista", "sábado", "15h")
        self.assertEqual(texto, "Evento criado na conta pessoal: Dentista, sábado, 26 de setembro, das 15h às 16h.")
        rotulo, criado = self.google.criados[0]
        self.assertEqual(rotulo, "pessoal")
        self.assertEqual(criado["summary"], "Dentista")
        self.assertEqual(criado["start"], {"dateTime": "2026-09-26T15:00:00-03:00", "timeZone": "America/Sao_Paulo"})
        self.assertEqual(criado["end"], {"dateTime": "2026-09-26T16:00:00-03:00", "timeZone": "America/Sao_Paulo"})
        post = [p for p in self.google.pedidos if p.metodo == "POST" and p.caminho.endswith("/events")][0]
        self.assertEqual(post.caminho, "/calendar/v3/calendars/primary/events")
        # A mesma coisa de novo não duplica
        with mock.patch.dict(os.environ, {"GOOGLE_CONTA_PADRAO": "pessoal"}):
            self.assertEqual(self.criar("dentista", "26/09", "15:00"),
                             "Esse evento já existe na conta pessoal: dentista, sábado, 26 de setembro, das 15h às 16h. "
                             "Não criei outro.")
        self.assertEqual(len(self.google.criados), 1)

    def test_dia_todo_e_conta_escolhida(self):
        texto = self.criar("Prova de Cálculo", "dia 30", "", 60, "ana@alunos.utfpr.edu.br")
        self.assertEqual(texto, "Evento criado na conta faculdade: Prova de Cálculo, quarta-feira, 30 de setembro, "
                                "dia todo.")
        criado = self.google.criados[0][1]
        self.assertEqual((criado["start"], criado["end"]), ({"date": "2026-09-30"}, {"date": "2026-10-01"}))
        texto = self.criar("Estudo", "amanhã", "9 da noite", 90)
        self.assertEqual(texto, "Evento criado na conta faculdade: Estudo, sexta-feira, 25 de setembro, "
                                "das 21h às 22h30.")

    def test_validacoes(self):
        self.assertEqual(self.criar("", "amanhã"), "Falta o título do evento.")
        self.assertIn("Diga um dia só", self.criar("X", "semana"))
        self.assertIn('Não entendi a hora "tarde"', self.criar("X", "amanhã", "tarde"))
        self.assertIn('Não entendi a hora "25h"', self.criar("X", "amanhã", "25h"))
        self.assertIn("entre 5 minutos e 24 horas", self.criar("X", "amanhã", "10h", 0))
        self.assertEqual(self.criar("X", "hoje", "7h"), "Hoje às 7h já passou. Confirme outro dia ou horário.")
        self.assertIn("já passou", self.criar("X", "20/09/2026"))
        self.assertIn('não conheço a conta "trabalho"', self.criar("X", "amanhã", "10h", 60, "trabalho").lower())
        self.assertEqual(self.google.criados, [])

    def test_interpretar_hora(self):
        casos = {"15h": (15, 0), "15h30": (15, 30), "15:30": (15, 30), "15": (15, 0), "às 9h": (9, 0),
                 "9h05min": (9, 5), "15 horas": (15, 0), "3 da tarde": (15, 0), "10 da noite": (22, 0),
                 "meio-dia": (12, 0), "meia-noite": (0, 0), "8 da manhã": (8, 0), "15.45": (15, 45)}
        for texto, esperado in casos.items():
            self.assertEqual(agenda.interpretar_hora(texto), esperado, texto)
        for ruim in ("24h", "15h75", "tarde", "amanhã"):
            self.assertIsNone(agenda.interpretar_hora(ruim), ruim)


if __name__ == "__main__":
    unittest.main()

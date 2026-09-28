"""Testes da ferramenta de clima, sem internet. Rodar na pasta jarvis-tools:
    python -m unittest discover -s testes -v
"""
import asyncio
import unittest
from datetime import date, timedelta
from unittest import mock

from app import clima

HOJE = date(2026, 9, 24)  # quinta-feira


def previsao_falsa(hora_atual=19, chance_tarde=70):
    dias = [HOJE + timedelta(days=i) for i in range(7)]
    horarios, chances = [], []
    for d in dias:
        for h in range(24):
            horarios.append("%sT%02d:00" % (d.isoformat(), h))
            chances.append(chance_tarde if 12 <= h < 18 else 10)
    return {
        "timezone": "America/Sao_Paulo",
        "current": {"time": "%sT%02d:15" % (HOJE.isoformat(), hora_atual), "temperature_2m": 23.4,
                    "apparent_temperature": 24.6, "relative_humidity_2m": 25, "precipitation": 0.0,
                    "weather_code": 2, "wind_speed_10m": 12.0},
        "hourly": {"time": horarios, "precipitation_probability": chances},
        "daily": {"time": [d.isoformat() for d in dias], "weather_code": [2, 63, 3, 0, 1, 80, 95],
                  "temperature_2m_max": [29.2, 25.1, 27, 30, 31, 28, 26],
                  "temperature_2m_min": [16.4, 17.0, 15, 14, 16, 18, 17],
                  "precipitation_probability_max": [chance_tarde, 85, 30, 0, 5, 60, 90],
                  "precipitation_sum": [0.4, 12.3, 1.0, 0, 0, 3.2, 20.0],
                  "wind_speed_10m_max": [18, 35, 20, 10, 12, 25, 45]},
    }


BUSCA_CORNELIO = {"results": [
    {"name": "Cornélio Procópio", "latitude": -23.18111, "longitude": -50.64667, "feature_code": "PPL",
     "country_code": "BR", "country": "Brasil", "admin1": "Paraná", "population": 45206},
    {"name": "Aeroporto de Cornélio Procópio", "latitude": -23.1525, "longitude": -50.6025, "feature_code": "AIRP",
     "country_code": "BR", "country": "Brasil", "admin1": "Paraná"},
]}

BUSCA_PARIS = {"results": [
    {"name": "Paris", "latitude": 48.85, "longitude": 2.35, "feature_code": "PPLC", "country_code": "FR",
     "country": "França", "admin1": "Île-de-France", "population": 2138551},
    {"name": "Paris", "latitude": -7.0, "longitude": -40.0, "feature_code": "PPL", "country_code": "BR",
     "country": "Brasil", "admin1": "Piauí", "population": 800},
]}

BUSCA_SANTA_MARIA = {"results": [
    {"name": "Santa María", "latitude": 14.0, "longitude": -88.0, "feature_code": "PPL", "country_code": "HN",
     "country": "Honduras", "population": 300000},
    {"name": "Santa Maria", "latitude": -29.68, "longitude": -53.8, "feature_code": "PPLA2", "country_code": "BR",
     "country": "Brasil", "admin1": "Rio Grande do Sul", "population": 276108},
]}


class TesteEscolhaDeCidade(unittest.TestCase):
    def test_prefere_cidade_a_aeroporto(self):
        self.assertEqual(clima.escolher_resultado(BUSCA_CORNELIO["results"])["feature_code"], "PPL")

    def test_paris_e_a_da_franca(self):
        self.assertEqual(clima.escolher_resultado(BUSCA_PARIS["results"])["country_code"], "FR")

    def test_prefere_brasil_quando_comparavel(self):
        self.assertEqual(clima.escolher_resultado(BUSCA_SANTA_MARIA["results"])["country_code"], "BR")

    def test_qualificador_por_uf_e_pais(self):
        r = clima.escolher_resultado(BUSCA_PARIS["results"], "pi")
        self.assertEqual(r["admin1"], "Piauí")
        r = clima.escolher_resultado(BUSCA_PARIS["results"], "franca")
        self.assertEqual(r["country_code"], "FR")
        self.assertIsNone(clima.escolher_resultado(BUSCA_PARIS["results"], "sp"))

    def test_nome_falado(self):
        self.assertEqual(clima.nome_falado(BUSCA_SANTA_MARIA["results"][1]), "Santa Maria, Rio Grande do Sul")
        self.assertEqual(clima.nome_falado(BUSCA_PARIS["results"][0]), "Paris, França")
        self.assertEqual(clima.nome_falado(BUSCA_CORNELIO["results"][0], curto=True), "Cornélio Procópio")


class TesteQuando(unittest.TestCase):
    datas = [HOJE + timedelta(days=i) for i in range(7)]  # qui, sex, sáb, dom, seg, ter, qua

    def q(self, texto):
        return clima.interpretar_quando(texto, self.datas)

    def test_relativos(self):
        self.assertEqual(self.q(""), ("hoje", [0]))
        self.assertEqual(self.q("Hoje à tarde"), ("hoje", [0]))
        self.assertEqual(self.q("à noite"), ("hoje", [0]))
        self.assertEqual(self.q("amanhã à tarde"), ("dia", [1]))
        self.assertEqual(self.q("sexta de manhã"), ("dia", [1]))
        self.assertEqual(self.q("hoje e amanhã"), ("dia", [0, 1]))
        self.assertEqual(self.q("agora"), ("agora", [0]))
        self.assertEqual(self.q("amanhã de manhã"), ("dia", [1]))
        self.assertEqual(self.q("depois de amanhã"), ("dia", [2]))

    def test_semana_e_fim_de_semana(self):
        self.assertEqual(self.q("semana"), ("semana", list(range(7))))
        self.assertEqual(self.q("fim de semana"), ("dia", [2, 3]))
        # No domingo: o próximo sábado e domingo, nunca hoje misturado com o sábado seguinte
        domingo = [date(2026, 9, 27) + timedelta(days=i) for i in range(8)]
        self.assertEqual(clima.interpretar_quando("fim de semana", domingo), ("dia", [6, 7]))
        sabado = [date(2026, 9, 26) + timedelta(days=i) for i in range(8)]
        self.assertEqual(clima.interpretar_quando("fim de semana", sabado), ("dia", [0, 1]))
        self.assertEqual(clima.interpretar_quando("semana", domingo), ("semana", list(range(7))))

    def test_dias_da_semana_e_datas(self):
        self.assertEqual(self.q("sexta"), ("dia", [1]))
        self.assertEqual(self.q("segunda-feira"), ("dia", [4]))
        self.assertEqual(self.q("quinta"), ("dia", [0]))
        self.assertEqual(self.q("2026-09-27"), ("dia", [3]))
        self.assertEqual(self.q("26/09"), ("dia", [2]))
        self.assertEqual(self.q("dia 30"), ("dia", [6]))
        self.assertEqual(self.q("15/10"), ("dia", []))
        self.assertIsNone(self.q("mês que vem"))


class TesteTextos(unittest.TestCase):
    local = clima.Local("Cornélio Procópio", -23.18, -50.65)

    def test_hoje_a_noite_so_mostra_periodos_futuros(self):
        texto = clima.montar_texto(self.local, previsao_falsa(hora_atual=19), "hoje")
        self.assertIn("Em Cornélio Procópio, agora faz 23 graus, com sensação de 25, parcialmente nublado.", texto)
        self.assertIn("Umidade do ar baixa, 25%.", texto)
        self.assertIn("Hoje, quinta-feira, 24 de setembro: parcialmente nublado, máxima de 29 e mínima de 16 graus.", texto)
        self.assertIn("Chance de chuva de 70%.", texto)  # às 19h só resta a noite: sem lista de períodos
        self.assertNotIn("tarde", texto)

    def test_hoje_de_manha_mostra_periodos(self):
        texto = clima.montar_texto(self.local, previsao_falsa(hora_atual=8), "hoje")
        self.assertIn("por período: manhã 10%, tarde 70%, noite 10%", texto)

    def test_amanha(self):
        texto = clima.montar_texto(self.local, previsao_falsa(), "amanhã")
        self.assertEqual(texto, "Em Cornélio Procópio, amanhã, sexta-feira, 25 de setembro: chuva moderada, "
                                "máxima de 25 e mínima de 17 graus. Chance de chuva de 85%, cerca de 12 milímetros; "
                                "por período: manhã 10%, tarde 70%, noite 10%. Ventos de até 35 quilômetros por hora.")

    def test_chuva_baixa(self):
        texto = clima.montar_texto(self.local, previsao_falsa(), "domingo")
        self.assertIn("domingo, 27 de setembro: céu limpo", texto)
        self.assertIn("Chance de chuva baixa, 0%.", texto)

    def test_agora(self):
        texto = clima.montar_texto(self.local, previsao_falsa(hora_atual=10), "agora")
        self.assertIn("Hoje a máxima é de 29 e a mínima de 16 graus.", texto)
        self.assertIn("Chance de chuva nas próximas 6 horas: 70%.", texto)

    def test_semana(self):
        texto = clima.montar_texto(self.local, previsao_falsa(), "semana")
        self.assertTrue(texto.startswith("Previsão para Cornélio Procópio nos próximos dias: hoje (24): "
                                         "parcialmente nublado, 16 a 29 graus, chuva 70%."))
        self.assertIn("quarta (30): trovoadas, 17 a 26 graus, chuva 90%.", texto)

    def test_fim_de_semana(self):
        texto = clima.montar_texto(self.local, previsao_falsa(), "fim de semana")
        self.assertIn("sábado, 26 de setembro", texto)
        self.assertIn("Domingo, 27 de setembro", texto)

    def test_erros(self):
        self.assertIn("Não entendi o dia pedido", clima.montar_texto(self.local, previsao_falsa(), "mês que vem"))
        self.assertEqual(clima.montar_texto(self.local, previsao_falsa(), "15/10"),
                         "Só tenho previsão para os próximos 7 dias.")


class TesteFerramenta(unittest.TestCase):
    def setUp(self):
        clima._locais.clear()
        clima._previsoes.clear()

    def test_cidade_padrao_e_cache(self):
        chamadas = []

        async def falso(url, parametros=None, tempo_limite=10.0):
            chamadas.append(url)
            return BUSCA_CORNELIO if url == clima.URL_BUSCA else previsao_falsa()

        with mock.patch.object(clima, "obter_json", falso), \
                mock.patch.dict("os.environ", {"JARVIS_CIDADE": "Cornélio Procópio, PR"}):
            primeira = asyncio.run(clima.previsao("", "amanhã"))
            segunda = asyncio.run(clima.previsao("cornelio procopio, pr", "hoje"))
        self.assertTrue(primeira.startswith("Em Cornélio Procópio, amanhã"))
        self.assertTrue(segunda.startswith("Em Cornélio Procópio, agora"))
        self.assertEqual(chamadas, [clima.URL_BUSCA, clima.URL_PREVISAO])  # o resto veio do cache

    def test_cidade_nao_encontrada_e_erro_de_rede(self):
        async def vazio(url, parametros=None, tempo_limite=10.0):
            return {"generationtime_ms": 0.1}

        async def falha(url, parametros=None, tempo_limite=10.0):
            raise clima.ErroRede("sem conexão com o serviço")

        with mock.patch.object(clima, "obter_json", vazio):
            self.assertIn("Não encontrei a cidade Xyzabc", asyncio.run(clima.previsao("Xyzabc", "hoje")))
        with mock.patch.object(clima, "obter_json", falha):
            self.assertEqual(asyncio.run(clima.previsao("Londrina", "hoje")),
                             "Não consegui consultar a previsão agora: sem conexão com o serviço.")


if __name__ == "__main__":
    unittest.main()

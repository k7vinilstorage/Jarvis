"""Testes da limpeza de textos, sem rede. Rodar na pasta jarvis-voz:
    python -m unittest discover -s testes -v
"""
import unittest

from app import textos


class TesteLimpezaParaFala(unittest.TestCase):
    def test_tira_markdown(self):
        bruto = "## Previsão\n**Amanhã** vai _chover_ em `Londrina`."
        self.assertEqual(textos.limpar_para_fala(bruto), "Previsão. Amanhã vai chover em Londrina.")

    def test_lista_vira_frases(self):
        bruto = "Tarefas:\n- estudar cálculo\n- entregar o relatório\n1. revisar a prova"
        self.assertEqual(textos.limpar_para_fala(bruto),
                         "Tarefas: estudar cálculo. entregar o relatório. revisar a prova.")

    def test_links_e_urls(self):
        bruto = "Veja [a previsão](https://open-meteo.com/x) ou acesse https://exemplo.com/a?b=1 agora."
        self.assertEqual(textos.limpar_para_fala(bruto), "Veja a previsão ou acesse agora.")

    def test_url_no_fim_da_frase(self):
        self.assertEqual(textos.limpar_para_fala("Mais em www.utfpr.edu.br."), "Mais em")

    def test_emojis(self):
        self.assertEqual(textos.limpar_para_fala("Bom dia! ☀️ Hoje faz 25 °C 😀👍🏽"),
                         "Bom dia! Hoje faz 25 °C")

    def test_mantem_acentos_e_numeros(self):
        texto = "São 15h30 de quinta-feira, 24 de setembro; a mínima é de 12,5 graus."
        self.assertEqual(textos.limpar_para_fala(texto), texto)

    def test_sublinhado_dentro_de_palavra_fica(self):
        self.assertEqual(textos.limpar_para_fala("O arquivo config_final está pronto."),
                         "O arquivo config_final está pronto.")

    def test_bloco_de_pensamento(self):
        self.assertEqual(textos.limpar_para_fala("<think>vou pensar</think>Oi, senhor."), "Oi, senhor.")

    def test_junta_espacos(self):
        self.assertEqual(textos.limpar_para_fala("  Oi,\t\tsenhor .  "), "Oi, senhor.")

    def test_so_simbolos_fica_vazio(self):
        self.assertEqual(textos.limpar_para_fala("👍 🎉\n---\n"), "")

    def test_corta_no_fim_da_frase(self):
        frase = "Esta é uma frase de teste com umas sessenta letras no total. "
        texto = frase * 20  # ~1200 caracteres
        cortado = textos.limpar_para_fala(texto)
        self.assertLessEqual(len(cortado), 600)
        self.assertGreater(len(cortado), 500)
        self.assertTrue(cortado.endswith("total."))

    def test_corta_sem_pontuacao(self):
        cortado = textos.cortar("palavra " * 200, 600)
        self.assertLessEqual(len(cortado), 601)
        self.assertTrue(cortado.endswith("palavra."))

    def test_curto_fica_igual(self):
        self.assertEqual(textos.cortar("Oi.", 600), "Oi.")


class TesteTranscricao(unittest.TestCase):
    def test_vazios(self):
        for bruto in ("", "   ", "[BLANK_AUDIO]", " [Música] ", "(risos)", "...", "*tosse*",
                      "Legendas pela comunidade Amara.org", "Obrigado por assistir!"):
            self.assertEqual(textos.limpar_transcricao(bruto), "", bruto)

    def test_texto_normal(self):
        self.assertEqual(textos.limpar_transcricao("  Que horas são?  "), "Que horas são?")

    def test_tira_anotacao_no_meio(self):
        self.assertEqual(textos.limpar_transcricao("Que horas [música] são?"), "Que horas são?")

    def test_obrigado_em_outra_frase_fica(self):
        self.assertEqual(textos.limpar_transcricao("Obrigado por assistir a aula comigo"),
                         "Obrigado por assistir a aula comigo")


class TesteNumerosParaFala(unittest.TestCase):
    def falar(self, texto):
        return textos.para_fala(texto)

    def test_horas(self):
        self.assertEqual(self.falar("Entrega às 23h59, prova às 19h e aula às 7h05."),
                         "Entrega às 23 e 59, prova às 19 horas e aula às 7 e 5.")
        self.assertEqual(self.falar("Às 0h e às 12h. Começa 10:30."), "À meia-noite e ao meio-dia. Começa 10 e 30.")
        self.assertEqual(self.falar("Dura 1h."), "Dura 1 hora.")

    def test_datas_e_ordinais(self):
        self.assertEqual(self.falar("Dia 25/09/2026 e 01/10."), "Dia 25 de setembro de 2026 e primeiro de outubro.")
        self.assertEqual(self.falar("A 1ª prova e o 2º trabalho."), "A primeira prova e o segundo trabalho.")
        self.assertEqual(self.falar("Faltam 3/4 da turma."), "Faltam 3/4 da turma.")  # fração fica

    def test_simbolos_e_dinheiro(self):
        self.assertEqual(self.falar("31°C, 2% de chuva, R$ 1.250,50 e R$ 3."),
                         "31 graus, 2 por cento de chuva, 1250 reais e 50 centavos e 3 reais.")
        self.assertEqual(self.falar("Versão 26.04, 1.000 downloads."), "Versão 26.04, 1000 downloads.")

    def test_abreviacoes(self):
        self.assertEqual(self.falar("A Profa. Ana e o Prof. Carlos."), "A Professora Ana e o Professor Carlos.")
        self.assertEqual(self.falar("Chegou no dia 5."), "Chegou no dia 5.")


class TesteDivisorDeFrases(unittest.TestCase):
    def dividir(self, pedacos, minimo=60):
        divisor = textos.DivisorDeFrases(minimo)
        saida = []
        for pedaco in pedacos:
            saida += divisor.adicionar(pedaco)
        return saida + divisor.terminar()

    def test_primeira_frase_sai_sozinha_e_as_outras_juntas(self):
        self.assertEqual(self.dividir(["Hoje em Corné", "lio faz 28 graus. Não chove. Venta pouco. ",
                                       "Amanhã esquenta bastante, com máxima de 31."]),
                         ["Hoje em Cornélio faz 28 graus.", "Não chove. Venta pouco. Amanhã esquenta bastante, com "
                          "máxima de 31."])

    def test_abreviacao_e_decimal_nao_cortam(self):
        self.assertEqual(self.dividir(["A Prof. Ana disse 26.04 hoje. Fim."], minimo=0),
                         ["A Prof. Ana disse 26.04 hoje.", "Fim."])

    def test_quebra_de_linha_e_texto_sem_pontuacao(self):
        self.assertEqual(self.dividir(["Itens:\n- um\n- dois"], minimo=0), ["Itens:", "- um", "- dois"])
        longo = "palavra " * 80
        partes = self.dividir([longo])
        self.assertGreater(len(partes), 1)
        self.assertEqual(" ".join(partes).split(), longo.split())

    def test_vazio(self):
        self.assertEqual(self.dividir(["", "  ", "\n"]), [])


class TesteCasosDaRevisao(unittest.TestCase):
    def test_notas_nao_viram_datas(self):
        self.assertEqual(textos.para_fala("Nota 8/10 e 7,5/10; prova 25/09."), "Nota 8/10 e 7,5/10; prova 25 de setembro.")

    def test_grau_e_ordinal(self):
        self.assertEqual(textos.para_fala("5ºC, 31°C, 1° semestre e 2º lugar."),
                         "5 graus, 31 graus, primeiro semestre e segundo lugar.")

    def test_lista_numerada_nao_corta_no_numero(self):
        divisor = textos.DivisorDeFrases(0)
        frases = divisor.adicionar("1. Primeiro item. 2. Segundo item.\n3. Terceiro.") + divisor.terminar()
        self.assertEqual(frases, ["1. Primeiro item.", "2. Segundo item.", "3. Terceiro."])
        self.assertEqual([textos.para_fala(f) for f in frases], ["Primeiro item.", "Segundo item.", "Terceiro."])

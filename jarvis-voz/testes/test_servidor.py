"""A ponte inteira por WebSocket, com Whisper, Piper e Hermes falsos (servidores locais de verdade)."""
import asyncio
import json
import unittest

import websockets

from apoio import SEGUNDOS_POR_CARACTERE, TAXA_PIPER, TOKEN, HermesFalso, PonteRodando, WyomingFalso, configuracao, fala

from app import textos


async def conversar(url, *mensagens, esperar_fins=1, tempo=10, cabecalhos=None):
    """Manda as mensagens (dict = JSON, bytes = áudio) e junta tudo o que chega até o 'fim'."""
    eventos, audio = [], bytearray()
    async with websockets.connect(url, additional_headers=cabecalhos, max_size=None) as ws:
        eventos.append(json.loads(await asyncio.wait_for(ws.recv(), tempo)))  # pronto
        for mensagem in mensagens:
            await ws.send(mensagem if isinstance(mensagem, (bytes, str)) else json.dumps(mensagem))
        fins = 0
        while fins < esperar_fins:
            dado = await asyncio.wait_for(ws.recv(), tempo)
            if isinstance(dado, bytes):
                audio += dado
                continue
            evento = json.loads(dado)
            eventos.append(evento)
            if evento["tipo"] == "fim":
                fins += 1
    return eventos, bytes(audio)


def do_tipo(eventos, tipo):
    return [e for e in eventos if e["tipo"] == tipo]


class BaseVoz(unittest.TestCase):
    roteiro = None

    def setUp(self):
        self.wyoming = WyomingFalso()
        self.hermes = HermesFalso(self.roteiro)
        self.ponte = PonteRodando(configuracao(self.hermes.url, self.wyoming.uri))
        self.url = self.ponte.url + "?token=%s&sala=teste" % TOKEN

    def tearDown(self):
        self.ponte.fechar()
        self.hermes.fechar()
        self.wyoming.fechar()

    def rodar(self, *mensagens, **opcoes):
        return asyncio.run(conversar(self.url, *mensagens, **opcoes))


class TesteConversa(BaseVoz):
    def test_pergunta_digitada_frase_a_frase(self):
        eventos, audio = self.rodar({"tipo": "texto", "texto": "Que horas são?"})
        self.assertEqual(eventos[0]["tipo"], "pronto")
        self.assertEqual(do_tipo(eventos, "transcricao")[0]["texto"], "Que horas são?")
        frases = [e["texto"] for e in do_tipo(eventos, "frase")]
        self.assertEqual(frases, ["São 15 e 30.", "Algo mais?"])  # hora como se fala
        self.assertEqual(self.wyoming.textos_falados, frases)
        inicio = do_tipo(eventos, "audio_inicio")
        self.assertEqual((len(inicio), inicio[0]["taxa"], inicio[0]["formato"]), (1, TAXA_PIPER, "s16le"))
        self.assertTrue(audio)
        self.assertEqual(len(do_tipo(eventos, "audio_fim")), 1)
        fim = do_tipo(eventos, "fim")[0]
        self.assertEqual(fim["resposta"], "São 15h30. Algo mais?")
        self.assertEqual(fim["erro"], "")
        for nome in ("primeira_palavra", "primeiro_audio", "total"):
            self.assertIn(nome, fim["tempos"])
        pedido = self.hermes.pedidos[0]
        self.assertEqual(pedido["cabecalhos"]["authorization"], "Bearer chave-hermes")
        self.assertTrue(pedido["corpo"]["stream"])
        self.assertEqual(pedido["corpo"]["messages"][0]["role"], "system")
        self.assertEqual(pedido["corpo"]["messages"][-1], {"role": "user", "content": "Que horas são?"})
        self.assertNotIn("x-hermes-session-key", pedido["cabecalhos"])

    def test_fala_passa_pelo_whisper(self):
        eventos, _ = self.rodar({"tipo": "inicio"}, fala(0.5)[:8000], fala(0.5)[8000:], {"tipo": "fim"})
        estados = [e["estado"] for e in do_tipo(eventos, "estado")]
        self.assertEqual(estados, ["ouvindo", "transcrevendo", "pensando", "falando", "pronto"])
        self.assertEqual(len(self.wyoming.audios_recebidos[0]), 16000)  # 0,5 s a 16 kHz, 16 bits
        fim = do_tipo(eventos, "fim")[0]
        self.assertEqual(fim["pergunta"], "Que horas são?")
        self.assertIn("stt", fim["tempos"])

    def test_silencio_nao_vai_ao_hermes(self):
        eventos, audio = self.rodar({"tipo": "inicio"}, bytes(16000), {"tipo": "fim"})
        self.assertEqual(self.wyoming.textos_falados, [textos.NAO_OUVI])
        self.assertEqual(self.hermes.pedidos, [])
        self.assertTrue(audio)
        self.assertIn("sem fala", do_tipo(eventos, "fim")[0]["erro"])

    def test_transcricao_vazia(self):
        self.wyoming.transcricao = " [BLANK_AUDIO] "
        eventos, _ = self.rodar({"tipo": "inicio"}, fala(0.5), {"tipo": "fim"})
        self.assertEqual(self.wyoming.textos_falados, [textos.NAO_OUVI])
        self.assertEqual(self.hermes.pedidos, [])

    def test_saida_para_o_dac_do_esp32(self):
        eventos, audio = self.rodar({"tipo": "config", "saida_taxa": 16000, "saida_formato": "u8"},
                                    {"tipo": "texto", "texto": "oi"})
        inicio = do_tipo(eventos, "audio_inicio")[0]
        self.assertEqual((inicio["taxa"], inicio["formato"]), (16000, "u8"))
        falado = sum(len(t) for t in self.wyoming.textos_falados)
        esperado = sum(int(max(0.05, len(t) * SEGUNDOS_POR_CARACTERE) * TAXA_PIPER) * 16000 // TAXA_PIPER
                       for t in self.wyoming.textos_falados)
        self.assertTrue(falado and abs(len(audio) - esperado) <= 4, (len(audio), esperado))

    def test_config_invalida(self):
        eventos, _ = self.rodar({"tipo": "config", "saida_taxa": 3}, {"tipo": "config", "saida_formato": "mp3"},
                                {"tipo": "texto", "texto": "oi"})
        erros = [e["mensagem"] for e in do_tipo(eventos, "erro")]
        self.assertEqual(len(erros), 2)
        self.assertEqual(do_tipo(eventos, "audio_inicio")[0]["taxa"], TAXA_PIPER)

    def test_historico_da_sala_e_nova(self):
        async def cenario():
            await conversar(self.url, {"tipo": "texto", "texto": "Primeira?"})
            await conversar(self.url, {"tipo": "texto", "texto": "Segunda?"})
            eventos, _ = await conversar(self.url, {"tipo": "nova"}, {"tipo": "texto", "texto": "Terceira?"})
            return eventos
        eventos = asyncio.run(cenario())
        self.assertEqual(do_tipo(eventos, "nova")[0]["sala"], "teste")
        segunda = self.hermes.pedidos[1]["corpo"]["messages"]
        self.assertEqual([m["role"] for m in segunda], ["system", "user", "assistant", "user"])
        self.assertEqual(segunda[1]["content"], "Primeira?")
        terceira = self.hermes.pedidos[2]["corpo"]["messages"]
        self.assertEqual([m["role"] for m in terceira], ["system", "user"])

    def test_so_falar(self):
        eventos, audio = self.rodar({"tipo": "falar", "texto": "Teste do alto-falante às 23h59."})
        self.assertEqual(self.wyoming.textos_falados, ["Teste do alto-falante às 23 e 59."])
        self.assertTrue(audio)
        self.assertEqual(self.hermes.pedidos, [])

    def test_mensagens_invalidas(self):
        eventos, _ = self.rodar("não é json", {"tipo": "xyz"}, {"tipo": "texto", "texto": "oi"})
        erros = [e["mensagem"] for e in do_tipo(eventos, "erro")]
        self.assertIn("mensagem inválida", erros[0])
        self.assertIn("tipo desconhecido", erros[1])

    def test_token_errado_ou_ausente(self):
        async def tentar(url, cabecalhos=None):
            with self.assertRaises(websockets.exceptions.InvalidStatus) as erro:
                async with websockets.connect(url, additional_headers=cabecalhos):
                    pass
            return erro.exception.response.status_code
        self.assertEqual(asyncio.run(tentar(self.ponte.url + "?token=errado")), 401)
        self.assertEqual(asyncio.run(tentar(self.ponte.url)), 401)
        self.assertEqual(asyncio.run(tentar(self.ponte.url + "?token=%s&sala=%s" % (TOKEN, "x" * 80))), 400)
        # Pelo cabeçalho também vale
        eventos, _ = asyncio.run(conversar(self.ponte.url, {"tipo": "texto", "texto": "oi"},
                                           cabecalhos={"Authorization": "Bearer " + TOKEN}))
        self.assertEqual(do_tipo(eventos, "fim")[0]["erro"], "")


class TesteFerramenta(BaseVoz):
    roteiro = [("ferramenta", "clima"), ("pausa", 0.2), ("texto", "Amanhã faz 31°C "), ("texto", "com 2% de chuva.")]

    def test_um_momento_antes_da_ferramenta(self):
        eventos, _ = self.rodar({"tipo": "texto", "texto": "Vai chover amanhã?"})
        self.assertEqual(do_tipo(eventos, "ferramenta")[0]["nome"], "clima")
        self.assertEqual(self.wyoming.textos_falados, ["Um momento.", "Amanhã faz 31 graus com 2 por cento de chuva."])
        self.assertEqual(do_tipo(eventos, "fim")[0]["ferramentas"], ["clima"])
        # Na segunda vez, o "Um momento." vem do cache: o Piper não é chamado de novo para ele
        self.rodar({"tipo": "texto", "texto": "E depois?"})
        self.assertEqual(self.wyoming.textos_falados.count("Um momento."), 1)


class TesteTextoAntesDaFerramenta(BaseVoz):
    roteiro = [("texto", "Vou verificar"), ("ferramenta", "moodle_prazos"), ("texto", "Nada para amanhã.")]

    def test_sem_um_momento_quando_ja_disse_algo(self):
        self.rodar({"tipo": "texto", "texto": "Tenho algo amanhã?"})
        self.assertEqual(self.wyoming.textos_falados, ["Vou verificar", "Nada para amanhã."])


class TesteErros(BaseVoz):
    def test_hermes_fora_do_ar(self):
        self.hermes.roteiro = [("erro_http", 502)]
        eventos, audio = self.rodar({"tipo": "texto", "texto": "oi"})
        self.assertEqual(self.wyoming.textos_falados, [textos.FALHA_HERMES])
        self.assertIn("502", do_tipo(eventos, "fim")[0]["erro"])
        self.assertTrue(audio)

    def test_piper_com_erro(self):
        self.wyoming.falhar_tts = True
        eventos, audio = self.rodar({"tipo": "texto", "texto": "oi"})
        self.assertEqual(audio, b"")
        self.assertIn("piper", do_tipo(eventos, "fim")[0]["erro"])
        self.assertTrue(do_tipo(eventos, "erro"))
        self.assertEqual(do_tipo(eventos, "fim")[0]["resposta"], "São 15h30. Algo mais?")

    def test_whisper_fora_do_ar(self):
        self.wyoming.fechar()
        self.ponte.fechar()
        wyoming_vivo = WyomingFalso()
        try:
            config = configuracao(self.hermes.url, "tcp://127.0.0.1:1")
            config = config.__class__(**{**config.__dict__, "piper_uri": wyoming_vivo.uri})
            self.ponte = PonteRodando(config)
            url = self.ponte.url + "?token=%s" % TOKEN
            eventos, _ = asyncio.run(conversar(url, {"tipo": "inicio"}, fala(0.5), {"tipo": "fim"}))
            self.assertIn("whisper", do_tipo(eventos, "fim")[0]["erro"])
            self.assertEqual(wyoming_vivo.textos_falados, [textos.FALHA_WHISPER])
        finally:
            wyoming_vivo.fechar()
        self.wyoming = WyomingFalso()  # o tearDown fecha este


class TesteInterromper(BaseVoz):
    roteiro = [("texto", "Primeira frase longa da resposta. "), ("pausa", 1.5), ("texto", "Frase que não sai.")]

    def test_falar_por_cima_corta_a_resposta(self):
        async def cenario():
            eventos = []
            async with websockets.connect(self.url, max_size=None) as ws:
                await ws.recv()
                await ws.send(json.dumps({"tipo": "texto", "texto": "Conte algo."}))
                while True:  # espera a primeira frase começar a sair
                    dado = await asyncio.wait_for(ws.recv(), 5)
                    if isinstance(dado, str):
                        eventos.append(json.loads(dado))
                        if eventos[-1]["tipo"] == "frase":
                            break
                self.hermes.roteiro = [("texto", "Resposta nova.")]
                await ws.send(json.dumps({"tipo": "texto", "texto": "Outra coisa."}))
                fins = 0
                while fins < 2:
                    dado = await asyncio.wait_for(ws.recv(), 5)
                    if isinstance(dado, bytes):
                        continue
                    evento = json.loads(dado)
                    eventos.append(evento)
                    fins += evento["tipo"] == "fim"
                return eventos
        eventos = asyncio.run(cenario())
        self.assertNotIn("Frase que não sai.", self.wyoming.textos_falados)
        primeira, segunda = do_tipo(eventos, "fim")
        self.assertEqual(primeira["erro"], "interrompido")  # toda vez termina com um "fim"
        self.assertEqual(segunda["resposta"], "Resposta nova.")
        # audio_inicio e audio_fim sempre em pares, nunca um audio_fim solto
        marcas = [e["tipo"] for e in eventos if e["tipo"] in ("audio_inicio", "audio_fim")]
        self.assertEqual(marcas, ["audio_inicio", "audio_fim"] * (len(marcas) // 2))
        self.assertTrue(marcas)


if __name__ == "__main__":
    unittest.main()

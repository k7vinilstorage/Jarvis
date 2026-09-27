"""Testes da leitura do áudio de entrada (PCM do ESP32 e WAV), sem rede."""
import io
import math
import struct
import unittest
import wave

from app import audio


def seno(segundos=1.0, taxa=16000, amplitude=8000, canais=1, freq=440.0):
    quadros = []
    for i in range(int(segundos * taxa)):
        v = int(amplitude * math.sin(2 * math.pi * freq * i / taxa))
        quadros.append(struct.pack("<" + "h" * canais, *([v] * canais)))
    return b"".join(quadros)


def wav(pcm, taxa=16000, canais=1, largura=2):
    saida = io.BytesIO()
    with wave.open(saida, "wb") as w:
        w.setnchannels(canais)
        w.setsampwidth(largura)
        w.setframerate(taxa)
        w.writeframes(pcm)
    return saida.getvalue()


L16 = "audio/L16; rate=16000; channels=1"


class TesteEntradaPCM(unittest.TestCase):
    def test_l16_do_esp32(self):
        pcm = seno(1.0)
        self.assertEqual(audio.ler_entrada(L16, pcm), pcm)

    def test_l16_sem_parametros(self):
        pcm = seno(0.5)
        self.assertEqual(audio.ler_entrada("audio/l16", pcm), pcm)

    def test_byte_sobrando_e_descartado(self):
        pcm = seno(0.5)
        self.assertEqual(audio.ler_entrada(L16, pcm + b"\x01"), pcm)

    def test_taxa_nao_aceita(self):
        with self.assertRaises(audio.FormatoInvalido):
            audio.ler_entrada("audio/L16; rate=44100; channels=1", seno(0.5, taxa=44100))

    def test_parametro_invalido(self):
        with self.assertRaises(audio.FormatoInvalido):
            audio.ler_entrada("audio/L16; rate=abc", seno(0.5))

    def test_tipo_desconhecido(self):
        with self.assertRaises(audio.FormatoInvalido):
            audio.ler_entrada("audio/mpeg", b"ID3" + b"\x00" * 100)

    def test_mais_de_30_segundos(self):
        with self.assertRaises(audio.AudioLongo):
            audio.ler_entrada(L16, b"\x00\x00" * 16000 * 31)

    def test_limite_do_corpo(self):
        self.assertEqual(audio.limite_do_corpo(L16), 30 * 32000)
        self.assertGreater(audio.limite_do_corpo("audio/wav"), 30 * 48000 * 4)


class TesteEntradaWAV(unittest.TestCase):
    def test_wav_16k_mono(self):
        pcm = seno(1.0)
        self.assertEqual(audio.ler_entrada("audio/wav", wav(pcm)), pcm)

    def test_wav_reconhecido_pelo_conteudo(self):
        pcm = seno(0.5)
        self.assertEqual(audio.ler_entrada("application/octet-stream", wav(pcm)), pcm)

    def test_wav_estereo_48k_vira_16k_mono(self):
        entrada = wav(seno(1.0, taxa=48000, canais=2), taxa=48000, canais=2)
        pcm = audio.ler_entrada("audio/wav", entrada)
        self.assertEqual(len(pcm), 16000 * 2)
        self.assertGreater(audio.pico(pcm), 7000)  # o tom de 440 Hz sobrevive à média de 3 amostras

    def test_wav_32k(self):
        pcm = audio.ler_entrada("audio/x-wav", wav(seno(1.0, taxa=32000), taxa=32000))
        self.assertEqual(len(pcm), 16000 * 2)

    def test_wav_44k_recusado_com_dica(self):
        with self.assertRaises(audio.FormatoInvalido) as erro:
            audio.ler_entrada("audio/wav", wav(seno(0.5, taxa=44100), taxa=44100))
        self.assertIn("16 kHz", str(erro.exception))
        self.assertIn("ffmpeg", str(erro.exception))

    def test_wav_8_bits_recusado(self):
        with self.assertRaises(audio.FormatoInvalido):
            audio.ler_entrada("audio/wav", wav(b"\x80" * 16000, largura=1))

    def test_wav_quebrado(self):
        with self.assertRaises(audio.FormatoInvalido):
            audio.ler_entrada("audio/wav", b"RIFF\x00\x00\x00\x00WAVEjunk")


class TesteNivel(unittest.TestCase):
    def test_curto(self):
        self.assertEqual(audio.motivo_sem_fala(seno(0.2)), "curto")

    def test_mudo(self):
        self.assertEqual(audio.motivo_sem_fala(seno(1.0, amplitude=100)), "mudo")

    def test_com_som(self):
        self.assertIsNone(audio.motivo_sem_fala(seno(1.0)))

    def test_pico_negativo_extremo(self):
        self.assertEqual(audio.pico(struct.pack("<hh", -32768, 5)), 32768)

    def test_wav_de_saida(self):
        pcm = seno(0.5, taxa=22050)
        dados = audio.wav(pcm, 22050)
        self.assertEqual(dados[:4], b"RIFF")
        self.assertEqual(len(dados), 44 + len(pcm))
        with wave.open(io.BytesIO(dados)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (22050, 1, 2))


if __name__ == "__main__":
    unittest.main()

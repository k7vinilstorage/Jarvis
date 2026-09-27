"""Leitura do streaming (SSE) do Hermes."""
import threading
import unittest

from app import hermes


def ler(linhas):
    eventos = []
    hermes.ler_sse([l.encode() for l in linhas], lambda t, v: eventos.append((t, v)), threading.Event())
    return eventos


class TesteSSE(unittest.TestCase):
    def test_texto_ferramenta_e_fim(self):
        eventos = ler([
            ": keepalive", "",
            'data: {"choices": [{"delta": {"content": "Vou "}}]}', "",
            "event: hermes.tool.progress", 'data: {"tool": "mcp__jarvis__clima", "toolCallId": "a", "status": "running"}', "",
            "event: hermes.tool.progress", 'data: {"tool": "mcp__jarvis__clima", "toolCallId": "a", "status": "completed"}', "",
            'data: {"choices": [{"delta": {"content": "ver."}}]}', "",
            "data: [DONE]", "",
            'data: {"choices": [{"delta": {"content": "depois do fim"}}]}',
        ])
        self.assertEqual(eventos, [("texto", "Vou "), ("ferramenta", "clima"), ("texto", "ver.")])

    def test_erro_e_linhas_estranhas(self):
        eventos = ler(["data: não é json", 'data: {"error": {"message": "modelo não carregou"}}',
                       'data: {"choices": [{"delta": {"tool_calls": [{"function": {"name": "mcp__jarvis__hora"}}]}}]}'])
        self.assertEqual(eventos, [("erro", "o Hermes devolveu um erro: modelo não carregou"), ("ferramenta", "hora")])

    def test_parar(self):
        parar = threading.Event()
        parar.set()
        eventos = []
        hermes.ler_sse([b'data: {"choices": [{"delta": {"content": "x"}}]}'], lambda t, v: eventos.append(t), parar)
        self.assertEqual(eventos, [])

    def test_mensagens(self):
        mensagens = hermes.montar_mensagens([{"role": "user", "content": "a"}], "b")
        self.assertEqual([m["role"] for m in mensagens], ["system", "user", "user"])
        self.assertIn("voz", mensagens[0]["content"])


if __name__ == "__main__":
    unittest.main()

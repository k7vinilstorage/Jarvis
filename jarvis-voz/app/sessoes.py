"""Histórico curto de conversa por sessão (em memória; some ao reiniciar o contêiner)."""
from __future__ import annotations

import re
import time

SESSAO_PADRAO = "esp32"
_NOME_VALIDO = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def sessao_valida(nome: str) -> bool:
    return bool(_NOME_VALIDO.match(nome or ""))


class Sessoes:
    """Guarda as últimas mensagens (pergunta e resposta) de cada sessão.

    Uma sessão parada por mais de `validade` segundos é esquecida: a próxima pergunta começa do zero.
    """

    def __init__(self, max_mensagens: int = 8, validade: float = 300.0, max_sessoes: int = 50,
                 relogio=time.monotonic):
        self.max_mensagens = max_mensagens
        self.validade = validade
        self.max_sessoes = max_sessoes
        self._relogio = relogio
        self._dados: dict[str, tuple[float, list[dict]]] = {}

    def _expirar(self) -> None:
        agora = self._relogio()
        for nome in [n for n, (uso, _) in self._dados.items() if agora - uso > self.validade]:
            del self._dados[nome]

    def historico(self, sessao: str) -> list[dict]:
        self._expirar()
        _, mensagens = self._dados.get(sessao, (0.0, []))
        return [dict(m) for m in mensagens]

    def registrar(self, sessao: str, pergunta: str, resposta: str) -> None:
        self._expirar()
        _, mensagens = self._dados.pop(sessao, (0.0, []))
        mensagens = mensagens + [{"role": "user", "content": pergunta},
                                 {"role": "assistant", "content": resposta}]
        self._dados[sessao] = (self._relogio(), mensagens[-self.max_mensagens:])
        while len(self._dados) > self.max_sessoes:  # a mais antiga sai (o dict mantém a ordem de uso)
            del self._dados[next(iter(self._dados))]

    def limpar(self, sessao: str) -> bool:
        return self._dados.pop(sessao, None) is not None

    def __len__(self) -> int:
        self._expirar()
        return len(self._dados)

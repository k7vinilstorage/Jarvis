"""Configuração da ponte de voz, lida do ambiente (docker-compose.voz.yml)."""
from __future__ import annotations

import os
from dataclasses import dataclass

PORTA = 10800


@dataclass(frozen=True)
class Config:
    token: str
    hermes_url: str
    hermes_chave: str
    whisper_uri: str
    piper_uri: str
    piper_voz: str
    idioma: str = "pt"
    sessao_hermes: bool = False  # manda X-Hermes-Session-Key por cômodo (para o Honcho, mais tarde)
    max_conexoes: int = 4

    @staticmethod
    def do_ambiente(ambiente=None) -> "Config":
        ambiente = os.environ if ambiente is None else ambiente
        token = ambiente.get("JARVIS_VOZ_TOKEN", "")
        if len(token) < 16:
            raise SystemExit("JARVIS_VOZ_TOKEN ausente ou curto demais (mínimo 16 caracteres).")
        chave = ambiente.get("HERMES_API_KEY", "")
        if not chave:
            raise SystemExit("HERMES_API_KEY ausente.")
        return Config(
            token=token,
            hermes_url=ambiente.get("HERMES_URL") or "http://hermes:8642",
            hermes_chave=chave,
            whisper_uri=ambiente.get("WHISPER_URI") or "tcp://whisper:10300",
            piper_uri=ambiente.get("PIPER_URI") or "tcp://piper:10200",
            piper_voz=ambiente.get("PIPER_VOICE") or "pt_BR-faber-medium",
            idioma=ambiente.get("WHISPER_IDIOMA") or "pt",
            sessao_hermes=(ambiente.get("VOZ_SESSAO_HERMES") or "").lower() in ("1", "true", "sim"),
        )

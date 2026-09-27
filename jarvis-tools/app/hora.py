"""Ferramenta de hora: data e hora atuais no fuso configurado (TZ)."""
from __future__ import annotations

from datetime import datetime

from app.textos import agora, data_falada, hora_falada


def texto_hora(momento: datetime | None = None) -> str:
    momento = momento or agora()
    return "Agora são %s de %s." % (hora_falada(momento), data_falada(momento.date(), com_ano=True))

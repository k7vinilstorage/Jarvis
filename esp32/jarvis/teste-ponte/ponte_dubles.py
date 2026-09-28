"""Sobe a ponte jarvis-voz de verdade com Whisper, Piper e Hermes falsos (os dublês dos testes dela) e imprime
"PORTA TOKEN". Usado pelo teste-ponte/rodar.sh; nada sai para a internet."""
import sys
import time
from pathlib import Path

VOZ = Path(__file__).resolve().parents[3] / "jarvis-voz"
sys.path[:0] = [str(VOZ), str(VOZ / "testes")]
import apoio  # noqa: E402

whisper_e_piper = apoio.WyomingFalso(transcricao="Que horas são?")
hermes = apoio.HermesFalso([("ferramenta", "hora"), ("pausa", 0.3), ("texto", "São 15h30. "),
                            ("texto", "Quer saber mais alguma coisa sobre o dia de hoje?")])
ponte = apoio.PonteRodando(apoio.configuracao(hermes.url, whisper_e_piper.uri))
print(ponte.porta, apoio.TOKEN, flush=True)
time.sleep(600)  # o rodar.sh encerra antes

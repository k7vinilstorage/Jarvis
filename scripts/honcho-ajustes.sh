#!/usr/bin/env bash
# Ajusta o Honcho do Hermes para o modelo pequeno e para respostas por voz.
# Rode depois de "hermes memory setup honcho". Uso: ./scripts/honcho-ajustes.sh
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose exec -T -u hermes hermes python3 - <<'PY'
import json
from pathlib import Path

candidatos = [Path("/opt/data/honcho.json"), Path("/opt/data/home/.honcho/config.json")]
arquivo = next((p for p in candidatos if p.exists()), None)
if arquivo is None:
    raise SystemExit("honcho.json não encontrado. Rode antes: docker compose exec -it hermes hermes memory setup honcho")

ajustes = {
    "recallMode": "context",             # injeta o contexto sem ferramentas extras (prompt menor)
    "contextTokens": 800,                # limite do contexto do Honcho por turno
    "dialecticCadence": 5,               # consulta dialética (chamada de LLM) só a cada 5 turnos
    "dialecticDepth": 1,
    "dialecticReasoningLevel": "minimal",
    "reasoningHeuristic": False,         # não sobe o nível por causa do tamanho da pergunta
    "timeout": 10,                       # se o Honcho cair, o Jarvis não fica esperando
}

cfg = json.loads(arquivo.read_text())
cfg.update(ajustes)
for host in cfg.get("hosts", {}).values():
    if isinstance(host, dict):
        host.update(ajustes)
arquivo.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
print(f"Ajustes gravados em {arquivo}:")
print(json.dumps(ajustes, indent=2))
PY

docker compose restart hermes
echo "Hermes reiniciado. Confira com: docker compose exec hermes hermes honcho status"

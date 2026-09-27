#!/usr/bin/env bash
# Envia uma pergunta ao Hermes pela API e mostra a resposta, os tokens e o tempo total.
# Uso: ./scripts/pergunta.sh "sua pergunta"
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

PERGUNTA="${1:?uso: $0 \"sua pergunta\"}"
CORPO=$(python3 -c 'import json, sys; print(json.dumps({"model": "hermes-agent", "messages": [{"role": "user", "content": sys.argv[1]}]}))' "$PERGUNTA")

INICIO=$(date +%s%N)
RESPOSTA=$(curl -sS --max-time 600 http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer $HERMES_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$CORPO")
FIM=$(date +%s%N)

echo "$RESPOSTA" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if "choices" not in d:
    print("ERRO:", json.dumps(d, ensure_ascii=False))
    sys.exit(1)
print(d["choices"][0]["message"]["content"])
u = d.get("usage") or {}
if u:
    print("\n[tokens] entrada=%s saída=%s" % (u.get("prompt_tokens"), u.get("completion_tokens")))
'

MS=$(( (FIM - INICIO) / 1000000 ))
printf '[tempo] %d.%d s\n' $((MS / 1000)) $(((MS % 1000) / 100))

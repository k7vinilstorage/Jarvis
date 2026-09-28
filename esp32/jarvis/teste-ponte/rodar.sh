#!/usr/bin/env bash
# Testa o código de rede do firmware (lib/logica: WebSocket e mensagens) contra a ponte jarvis-voz de verdade,
# com Whisper, Piper e Hermes falsos. Roda no PC, sem placa e sem internet. Não testa o áudio nem o WiFi.
#
#   ./teste-ponte/rodar.sh
#   PYTHON=~/venv/bin/python ./teste-ponte/rodar.sh   um Python com o jarvis-voz/requirements.txt instalado
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}
"$PYTHON" -c "import wyoming, starlette, uvicorn" 2> /dev/null || {
  echo "Falta um Python com as dependências do jarvis-voz: pip install -r ../../jarvis-voz/requirements.txt" >&2
  echo "e rode com PYTHON=caminho/do/python." >&2
  exit 1
}

pio pkg install -e nativo > /dev/null  # traz o ArduinoJson
TMP=$(mktemp -d)
PID=""
limpar() {
  if [ -n "$PID" ]; then kill "$PID" 2> /dev/null || true; fi
  rm -rf "$TMP"
}
trap limpar EXIT

g++ -std=gnu++17 -Wall -Wextra -O1 -Ilib/logica/src -I.pio/libdeps/nativo/ArduinoJson/src \
  teste-ponte/cliente.cpp lib/logica/src/websocket.cpp lib/logica/src/mensagens.cpp -o "$TMP/cliente"

"$PYTHON" teste-ponte/ponte_dubles.py > "$TMP/ponte" 2> "$TMP/ponte.log" &
PID=$!
for _ in $(seq 1 50); do
  [ -s "$TMP/ponte" ] && break
  sleep 0.2
done
if ! [ -s "$TMP/ponte" ]; then
  echo "A ponte de teste não subiu:" >&2
  cat "$TMP/ponte.log" >&2
  exit 1
fi
read -r PORTA TOKEN < "$TMP/ponte"
"$TMP/cliente" "$PORTA" "$TOKEN"

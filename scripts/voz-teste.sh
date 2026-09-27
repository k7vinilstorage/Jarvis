#!/usr/bin/env bash
# Teste de voz de ponta a ponta, sem microfone: o Piper fala a pergunta, ela passa pela ponte como se fosse
# você (Whisper, Hermes, Piper) e os tempos vão para medicoes/voz-AAAAMMDD-HHMM.md.
#
#   ./scripts/voz-teste.sh
#   ./scripts/voz-teste.sh --pergunta "Que horas são?" --pergunta "Tenho prova esta semana?"
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p medicoes
SAIDA="medicoes/voz-$(date +%Y%m%d-%H%M).md"
if docker compose exec -T jarvis-voz python -m app.teste "$@" > "$SAIDA.tmp"; then
  mv "$SAIDA.tmp" "$SAIDA"
  cat "$SAIDA"
  echo
  echo "Relatório: $SAIDA"
else
  rm -f "$SAIDA.tmp"
  exit 1
fi

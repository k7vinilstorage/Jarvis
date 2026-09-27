#!/usr/bin/env bash
# Fase 2: sobe a ponte de voz (jarvis-voz) e faz um teste de ponta a ponta, sem microfone.
# Pode rodar mais de uma vez: rode de novo depois de atualizar o projeto.
#
#   ./scripts/fase2-voz.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ENV=.env
[ -f "$ENV" ] || { echo "Não achei o .env. Rode na pasta do projeto." >&2; exit 1; }

acrescentar_compose() {  # acrescentar_compose arquivo.yml
  if grep -q '^COMPOSE_FILE=' "$ENV"; then
    grep -q "^COMPOSE_FILE=.*$1" "$ENV" || sed -i "s/^COMPOSE_FILE=.*/&:$1/" "$ENV"
  else
    echo "COMPOSE_FILE=docker-compose.yml:$1" >> "$ENV"
  fi
}

echo "== .env"
[ -z "$(tail -c 1 "$ENV")" ] || echo >> "$ENV"
if ! grep -q '^JARVIS_VOZ_TOKEN=..*' "$ENV"; then
  sed -i '/^JARVIS_VOZ_TOKEN=/d' "$ENV"
  printf '\n# ---- Ponte de voz (jarvis-voz) ----\nJARVIS_VOZ_TOKEN=%s\n' "$(openssl rand -hex 24)" >> "$ENV"
  echo "   token novo em JARVIS_VOZ_TOKEN (veja com: grep JARVIS_VOZ_TOKEN .env)"
fi
acrescentar_compose docker-compose.voz.yml
docker compose config -q

echo "== Whisper e Piper"
docker compose up -d whisper piper

echo "== Compilando e subindo o jarvis-voz"
docker compose build jarvis-voz
docker compose up -d jarvis-voz
for _ in $(seq 1 60); do
  [ "$(docker inspect --format '{{.State.Health.Status}}' jarvis-voz 2>/dev/null)" = healthy ] && break
  sleep 2
done
[ "$(docker inspect --format '{{.State.Health.Status}}' jarvis-voz)" = healthy ] \
  || { echo "O jarvis-voz não ficou saudável. Veja: docker compose logs --tail 50 jarvis-voz" >&2; exit 1; }

echo "== Teste de ponta a ponta (a primeira vez o Whisper e o Piper carregam os modelos; pode demorar)"
./scripts/voz-teste.sh || echo "   aviso: o teste falhou; veja docker compose logs --tail 50 jarvis-voz whisper piper"

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Pronto. Para falar com o Jarvis pelo PC (detalhes em docs/fase2.md):"
echo "  1. No PC: pip install websockets sounddevice   e copie o scripts/voz-pc.py"
echo "  2. python voz-pc.py --servidor ${IP:-IP-DO-SERVIDOR} --token <o JARVIS_VOZ_TOKEN do .env>"
echo "  Para a porta escutar só na rede de casa, ponha JARVIS_VOZ_IP=${IP:-IP-DO-SERVIDOR} no .env e rode"
echo "  docker compose up -d jarvis-voz. Nunca redirecione a porta 10800 no roteador."

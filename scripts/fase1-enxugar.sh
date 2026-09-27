#!/usr/bin/env bash
# Fase 1, passo 1: enxuga o prompt que o Hermes manda ao modelo nas perguntas pela API,
# desliga o raciocínio e atualiza o SOUL.md. Pode rodar mais de uma vez.
#
#   ./scripts/fase1-enxugar.sh             aplica
#   ./scripts/fase1-enxugar.sh --desfazer  volta o config.yaml e o SOUL.md de antes da primeira execução
#
# Só mexe na plataforma da API (api_server). O Hermes pelo terminal (hermes chat) continua como estava.
set -euo pipefail
cd "$(dirname "$0")/.."

CFG=data/hermes/config.yaml
SOUL=data/hermes/SOUL.md
H=(docker compose exec -T hermes hermes)

# Ficam só terminal (para a hora, até a Fase 1 ganhar ferramentas próprias) e memory.
DESLIGAR=(web browser file code_execution vision image_gen skills todo session_search connections delegation cronjob)
DICA="Você responde por uma API e a resposta costuma ser lida em voz alta. Só texto simples, sem markdown, breve e natural."

esperar_hermes() {
  for _ in $(seq 1 90); do
    curl -fsS http://127.0.0.1:8642/health >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "O Hermes não respondeu em 90 s. Veja: docker compose logs --tail 50 hermes" >&2
  return 1
}

[ -f "$CFG" ] || { echo "Não achei $CFG. Rode na pasta do projeto, com o Hermes configurado." >&2; exit 1; }

if [ "${1:-}" = "--desfazer" ]; then
  [ -f "$CFG.antes-fase1" ] || { echo "Não há cópia $CFG.antes-fase1 para restaurar." >&2; exit 1; }
  cp -p "$CFG.antes-fase1" "$CFG"
  [ -f "$SOUL.antes-fase1" ] && cp -p "$SOUL.antes-fase1" "$SOUL"
  docker compose restart hermes
  esperar_hermes
  echo "Configuração de antes da Fase 1 restaurada."
  exit 0
fi

mkdir -p medicoes
if [ ! -f "$CFG.antes-fase1" ]; then
  cp -p "$CFG" "$CFG.antes-fase1"
  [ -f "$SOUL" ] && cp -p "$SOUL" "$SOUL.antes-fase1"
  echo "Cópias de segurança: $CFG.antes-fase1 e $SOUL.antes-fase1"
fi
if [ ! -s medicoes/prompt-antes-fase1.json ]; then
  echo "== Medindo o prompt atual da API"
  "${H[@]}" prompt-size --platform api_server --json > medicoes/prompt-antes-fase1.json
fi

echo "== Ferramentas da API: ficam terminal e memory"
"${H[@]}" tools disable "${DESLIGAR[@]}" --platform api_server

echo "== Tool Search desligado (sem as 3 ferramentas-ponte)"
"${H[@]}" config set tools.tool_search.enabled off

echo "== Raciocínio desligado"
# "false", não "none": o config set do Hermes grava "none" como vazio, e aí o raciocínio volta ao padrão
"${H[@]}" config set agent.reasoning_effort false

echo "== Blocos de orientação que um modelo pequeno não aproveita"
"${H[@]}" config set agent.execution_guidance false
"${H[@]}" config set agent.parallel_tool_call_guidance false

echo "== Dica da plataforma API, em versão curta"
"${H[@]}" config set platform_hints.api_server.replace "$DICA" \
  || echo "   aviso: não consegui trocar a dica (não é grave, ela só fica maior)"

echo "== SOUL.md novo"
cp hermes/SOUL.md "$SOUL"

echo "== Reiniciando o Hermes"
docker compose restart hermes
esperar_hermes

echo "== Medindo o prompt novo"
"${H[@]}" prompt-size --platform api_server --json > medicoes/prompt-depois-fase1.json
python3 - medicoes/prompt-antes-fase1.json medicoes/prompt-depois-fase1.json <<'PY'
import json, sys

def ler(caminho):
    texto = open(caminho, encoding="utf-8").read()
    return json.JSONDecoder().raw_decode(texto[texto.index("{"):])[0]

BYTES_POR_TOKEN = 4.75  # calibrado na Fase 0: 63.232 bytes viraram 13.302 tokens
antes, depois = ler(sys.argv[1]), ler(sys.argv[2])
print()
for rotulo, d in (("antes", antes), ("depois", depois)):
    sistema, ferr = d["system_prompt"]["bytes"], d["tools"]["json_bytes"]
    print("%-7s  sistema %5.1f KB | %2d ferramentas %5.1f KB | ~%d tokens por pergunta"
          % (rotulo, sistema / 1024, d["tools"]["count"], ferr / 1024, (sistema + ferr) / BYTES_POR_TOKEN))
grupos = ", ".join("%s (%d)" % (g["toolset"], g["tool_count"]) for g in depois.get("toolsets_breakdown", []))
print("ferramentas agora:", grupos or "nenhuma")
PY
echo
"${H[@]}" tools list --platform api_server || true
echo
echo "Pronto. Agora meça: python3 scripts/medicoes.py --nome SEU_NOME --rotulo fase1-enxuto"

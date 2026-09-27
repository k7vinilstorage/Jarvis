#!/usr/bin/env bash
# Troca o modelo de IA do Jarvis e ajusta os parâmetros, sem mexer na configuração do Hermes.
#
#   ./scripts/modelo.sh                            mostra o modelo atual, os parâmetros e os modelos baixados
#   ./scripts/modelo.sh usar qwen3.5:9b            troca de modelo (baixa se precisar) e faz um teste rápido
#   ./scripts/modelo.sh usar qwen3.5:4b --gpu 99   idem, forçando todas as camadas na GPU (padrão: automático)
#   ./scripts/modelo.sh voltar                     volta para o modelo anterior
#   ./scripts/modelo.sh ajustar temperatura 0.3    muda um parâmetro e aplica. Nomes: temperatura, top_p,
#                                                  top_k, presence_penalty, gpu. Valor "padrao" = o do modelo
#   ./scripts/modelo.sh aplicar                    recria o modelo do Jarvis com o que está no .env
#
# O Hermes (e o Honcho) sempre usam o mesmo nome, JARVIS_MODEL (jarvis-qwen). Este script só troca o que está
# por trás dele (BASE_MODEL) e os parâmetros. Para comparar modelos: python3 scripts/testar-jarvis.py
set -euo pipefail
cd "$(dirname "$0")/.."

ENV=.env
[ -f "$ENV" ] || { echo "Não achei o .env. Rode na pasta do projeto." >&2; exit 1; }
O=(docker exec ollama ollama)

tem_env() { grep -q "^$1=" "$ENV"; }
valor_env() { grep "^$1=" "$ENV" | tail -n 1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']$//" || true; }
padrao() {  # padrao NOME VALOR: o valor do .env ou, se a variável não existe, o padrão do docker-compose.yml
  if tem_env "$1"; then valor_env "$1"; else echo "$2"; fi
}
definir_env() {  # definir_env NOME VALOR: troca a linha, ou acrescenta no fim
  if tem_env "$1"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV"
  else
    [ -z "$(tail -c 1 "$ENV")" ] || echo >> "$ENV"
    echo "$1=$2" >> "$ENV"
  fi
}
ou_padrao() { if [ -n "$1" ]; then echo "$1"; else echo "${2:-padrão do modelo}"; fi; }

# Se algo falhar no meio (download, ollama-init), o .env volta a ser o de antes: nunca fica apontando para um
# modelo que não foi aplicado
restaurar_env() {
  local status=$?
  if [ "$status" -ne 0 ] && [ -f "$ENV.antes-modelo" ]; then
    mv "$ENV.antes-modelo" "$ENV"
    echo "Deu erro: o .env voltou a ser o de antes." >&2
  fi
}
guardar_env() {
  cp "$ENV" "$ENV.antes-modelo"
  trap restaurar_env EXIT
}
confirmar_env() { rm -f "$ENV.antes-modelo"; trap - EXIT; }

JARVIS=$(padrao JARVIS_MODEL jarvis-qwen)
CTX=$(padrao JARVIS_NUM_CTX 65536)

mostrar() {
  echo "Modelo do Jarvis: $JARVIS = $(padrao BASE_MODEL qwen3.5:4b)"
  echo "  contexto: $CTX · camadas na GPU: $(ou_padrao "$(padrao JARVIS_NUM_GPU '')" automático)"
  echo "  temperatura: $(ou_padrao "$(padrao JARVIS_TEMPERATURE 0.5)") · top_p: $(ou_padrao "$(padrao JARVIS_TOP_P 0.9)")" \
       "· top_k: $(ou_padrao "$(padrao JARVIS_TOP_K 20)") · presence_penalty: $(ou_padrao "$(padrao JARVIS_PRESENCE_PENALTY 0.3)")"
  local anterior
  anterior=$(valor_env BASE_MODEL_ANTERIOR)
  if [ -n "$anterior" ]; then echo "  anterior: $anterior (./scripts/modelo.sh voltar)"; fi
  echo
  echo "Parâmetros gravados no Ollama para o $JARVIS:"
  "${O[@]}" show "$JARVIS" --parameters 2>&1 | sed 's/^/  /' || true
  echo
  echo "Carregado agora (coluna PROCESSOR: o ideal é 100% GPU):"
  "${O[@]}" ps || true
  echo
  echo "Modelos baixados:"
  "${O[@]}" list || true
}

conferir_modelo() {  # baixa se precisar e confere se o modelo serve para o Jarvis
  local modelo=$1 info capacidades maximo
  if ! "${O[@]}" show "$modelo" > /dev/null 2>&1; then
    echo "== Baixando $modelo (pode demorar)"
    "${O[@]}" pull "$modelo"
  fi
  info=$("${O[@]}" show "$modelo")
  capacidades=$(awk '/^ *Capabilities/{f=1; next} f && NF == 0 {f=0} f {print $1}' <<< "$info")
  if ! grep -qx tools <<< "$capacidades"; then
    echo "O $modelo não faz chamadas de ferramenta (falta 'tools' nas capacidades), e o Jarvis depende disso." >&2
    echo "Escolha outro modelo; nada foi trocado." >&2
    exit 1
  fi
  echo "   $modelo: $(awk '/parameters/{print $2; exit}' <<< "$info") de parâmetros," \
       "$(awk '/quantization/{print $2; exit}' <<< "$info"), capacidades: $(tr '\n' ' ' <<< "$capacidades")"
  maximo=$(awk '/context length/{print $3; exit}' <<< "$info")
  if [[ "$maximo" =~ ^[0-9]+$ ]] && [ "$maximo" -lt "$CTX" ]; then
    echo "   Aviso: o $modelo foi treinado para até $maximo tokens de contexto, e o Jarvis usa $CTX (o mínimo do"
    echo "   Hermes é 64 mil). Ele deve funcionar, mas pode perder qualidade em conversas longas."
  fi
}

esperar_hermes() {
  for _ in $(seq 1 90); do
    curl -fsS http://127.0.0.1:8642/health > /dev/null 2>&1 && return 0
    sleep 1
  done
  echo "O Hermes não respondeu. Veja: docker compose logs --tail 50 hermes" >&2
  return 1
}

aplicar() {  # aplicar [recarregar]: recria o jarvis-qwen com o .env; recarregar = tira da memória e reinicia o Hermes
  echo "== Recriando o $JARVIS com o que está no .env"
  docker compose run --rm ollama-init
  if [ "${1:-}" = recarregar ]; then
    "${O[@]}" stop "$JARVIS" > /dev/null 2>&1 || true
    echo "== Reiniciando o Hermes"
    docker compose restart hermes > /dev/null
    esperar_hermes
  fi
}

testar() {
  echo
  echo "== Teste rápido (a primeira resposta inclui carregar o modelo na memória)"
  python3 scripts/chat.py --pergunta "Que horas são?" || true
  echo
  echo "== Onde o modelo está rodando"
  "${O[@]}" ps || true
  echo "Se a coluna PROCESSOR não mostrar 100% GPU, parte do modelo está na CPU e as respostas ficam bem mais lentas."
  echo "Para comparar com o anterior: python3 scripts/testar-jarvis.py  (o relatório leva o nome do modelo)"
}

gpu_valida() {
  [ "$1" = auto ] || [[ "$1" =~ ^[0-9]+$ ]] || { echo "--gpu aceita auto ou um número de camadas (99 = todas)." >&2; exit 2; }
}

usar() {
  local novo=${1:-} gpu=auto atual gpu_atual
  [ -n "$novo" ] || { echo "Diga o modelo, por exemplo: ./scripts/modelo.sh usar qwen3.5:9b" >&2; exit 2; }
  shift
  while [ $# -gt 0 ]; do
    case $1 in
      --gpu) gpu=${2:-}; gpu_valida "$gpu"; shift 2 ;;
      *) echo "Opção desconhecida: $1" >&2; exit 2 ;;
    esac
  done
  echo "== Conferindo $novo"
  conferir_modelo "$novo"
  atual=$(padrao BASE_MODEL qwen3.5:4b)
  gpu_atual=$(padrao JARVIS_NUM_GPU "")
  guardar_env
  if [ "$novo" != "$atual" ]; then
    definir_env BASE_MODEL_ANTERIOR "$atual"
    definir_env JARVIS_NUM_GPU_ANTERIOR "$gpu_atual"
  fi
  definir_env BASE_MODEL "$novo"
  if [ "$gpu" = auto ]; then definir_env JARVIS_NUM_GPU ""; else definir_env JARVIS_NUM_GPU "$gpu"; fi
  aplicar recarregar
  confirmar_env
  testar
}

voltar() {
  local anterior gpu_anterior atual gpu_atual
  anterior=$(valor_env BASE_MODEL_ANTERIOR)
  [ -n "$anterior" ] || { echo "Não há modelo anterior guardado no .env." >&2; exit 1; }
  gpu_anterior=$(valor_env JARVIS_NUM_GPU_ANTERIOR)
  atual=$(padrao BASE_MODEL qwen3.5:4b)
  gpu_atual=$(padrao JARVIS_NUM_GPU "")
  echo "== Voltando de $atual para $anterior"
  guardar_env
  definir_env BASE_MODEL "$anterior"
  definir_env JARVIS_NUM_GPU "$gpu_anterior"
  definir_env BASE_MODEL_ANTERIOR "$atual"
  definir_env JARVIS_NUM_GPU_ANTERIOR "$gpu_atual"
  aplicar recarregar
  confirmar_env
  testar
}

ajustar() {
  local nome=${1:-} valor=${2:-} variavel
  case $nome in
    temperatura|temperature) variavel=JARVIS_TEMPERATURE ;;
    top_p|top-p) variavel=JARVIS_TOP_P ;;
    top_k|top-k) variavel=JARVIS_TOP_K ;;
    presence_penalty|presence-penalty|penalidade) variavel=JARVIS_PRESENCE_PENALTY ;;
    gpu) variavel=JARVIS_NUM_GPU ;;
    *) echo "Parâmetros: temperatura, top_p, top_k, presence_penalty ou gpu." >&2; exit 2 ;;
  esac
  if [ "$valor" = padrao ] || [ "$valor" = padrão ] || { [ "$nome" = gpu ] && [ "$valor" = auto ]; }; then
    valor=""
  elif [ "$nome" = gpu ] || [ "$variavel" = JARVIS_TOP_K ]; then
    [[ "$valor" =~ ^[0-9]+$ ]] || { echo "Valor inválido: '$valor'. $nome aceita um número inteiro." >&2; exit 2; }
  elif ! [[ "$valor" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Valor inválido: '$valor'. Use um número (ex.: 0.3), ou padrao para o valor do próprio modelo." >&2
    exit 2
  fi
  guardar_env
  definir_env "$variavel" "$valor"
  echo "   $variavel=$valor"
  if [ "$nome" = gpu ]; then aplicar recarregar; else aplicar; fi
  confirmar_env
  echo "Pronto: vale a partir da próxima pergunta."
}

case ${1:-} in
  ""|mostrar|status) mostrar ;;
  usar) shift; usar "$@" ;;
  voltar) voltar ;;
  ajustar) shift; ajustar "$@" ;;
  aplicar) aplicar recarregar ;;
  -h|--help|ajuda) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "Comando desconhecido: $1. Veja: ./scripts/modelo.sh ajuda" >&2; exit 2 ;;
esac

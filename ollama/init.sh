#!/bin/sh
# Prepara o modelo do Jarvis no Ollama. Roda a cada "docker compose up" e pode repetir sem problema.
#   1. baixa o modelo base, só se ainda não estiver no servidor (funciona offline depois)
#   2. cria a variante do Jarvis com contexto de 64K (o Ollama usa só 2048 por padrão)
#   3. se JARVIS_NUM_GPU estiver definido, força essa quantidade de camadas na GPU
#   4. aplica a amostragem do .env (temperatura, top_p, top_k, presence_penalty)
# Para trocar de modelo ou de parâmetros, use o scripts/modelo.sh (ou edite o .env e rode este init de novo).
set -eu

if ollama show "$BASE_MODEL" >/dev/null 2>&1; then
  echo "[init] $BASE_MODEL já está no servidor."
else
  echo "[init] baixando $BASE_MODEL ..."
  ollama pull "$BASE_MODEL"
fi

echo "FROM $BASE_MODEL" > /tmp/Modelfile
echo "PARAMETER num_ctx $JARVIS_NUM_CTX" >> /tmp/Modelfile
if [ -n "${JARVIS_NUM_GPU:-}" ]; then
  echo "PARAMETER num_gpu $JARVIS_NUM_GPU" >> /tmp/Modelfile
fi
# Amostragem (vazio = o padrão do próprio modelo). Valores mais baixos deixam as respostas mais fiéis
# ao que as ferramentas devolveram; os padrões do qwen3.5 (temperatura 1) inventam mais.
if [ -n "${JARVIS_TEMPERATURE:-}" ]; then
  echo "PARAMETER temperature $JARVIS_TEMPERATURE" >> /tmp/Modelfile
fi
if [ -n "${JARVIS_TOP_P:-}" ]; then
  echo "PARAMETER top_p $JARVIS_TOP_P" >> /tmp/Modelfile
fi
if [ -n "${JARVIS_TOP_K:-}" ]; then
  echo "PARAMETER top_k $JARVIS_TOP_K" >> /tmp/Modelfile
fi
if [ -n "${JARVIS_PRESENCE_PENALTY:-}" ]; then
  echo "PARAMETER presence_penalty $JARVIS_PRESENCE_PENALTY" >> /tmp/Modelfile
fi

echo "[init] criando $JARVIS_MODEL com:"
sed 's/^/[init]   /' /tmp/Modelfile
ollama create "$JARVIS_MODEL" -f /tmp/Modelfile

echo "[init] parâmetros de $JARVIS_MODEL:"
ollama show "$JARVIS_MODEL" --parameters

# Modelo de embeddings do Honcho (só quando o docker-compose.honcho.yml está ativo).
# Roda na CPU (num_gpu 0) para não disputar a VRAM com o jarvis-qwen.
if [ -n "${EMBED_BASE_MODEL:-}" ]; then
  if ollama show "$EMBED_BASE_MODEL" >/dev/null 2>&1; then
    echo "[init] $EMBED_BASE_MODEL já está no servidor."
  else
    echo "[init] baixando $EMBED_BASE_MODEL ..."
    ollama pull "$EMBED_BASE_MODEL"
  fi
  echo "FROM $EMBED_BASE_MODEL" > /tmp/Modelfile.embed
  echo "PARAMETER num_ctx 8192" >> /tmp/Modelfile.embed
  echo "PARAMETER num_gpu 0" >> /tmp/Modelfile.embed
  echo "[init] criando $EMBED_MODEL (embeddings na CPU) ..."
  ollama create "$EMBED_MODEL" -f /tmp/Modelfile.embed
fi

echo "[init] pronto."

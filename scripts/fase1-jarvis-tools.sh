#!/usr/bin/env bash
# Fase 1, passo 2: sobe o jarvis-tools (ferramentas próprias do Jarvis) e liga ao Hermes.
# Pode rodar mais de uma vez: rode de novo depois de atualizar o projeto.
#
#   ./scripts/fase1-jarvis-tools.sh
#
# Moodle e Google precisam de um login separado depois (o script mostra os comandos no fim).
# A busca na web é ligada sozinha se houver um contêiner "searxng" (ou o nome em SEARXNG_CONTAINER).
set -euo pipefail
cd "$(dirname "$0")/.."

ENV=.env
H=(docker compose exec -T hermes hermes)
SEARXNG_CONTAINER=${SEARXNG_CONTAINER:-searxng}

[ -f "$ENV" ] || { echo "Não achei o .env. Rode na pasta do projeto." >&2; exit 1; }
[ -f data/hermes/config.yaml.antes-fase1 ] || { echo "Rode antes o passo 1: ./scripts/fase1-enxugar.sh" >&2; exit 1; }

valor_env() { grep "^$1=" "$ENV" | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
definir_env() {  # definir_env NOME VALOR: troca a linha, ou acrescenta no fim
  if grep -q "^$1=" "$ENV"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV"
  else
    echo "$1=$2" >> "$ENV"
  fi
}
acrescentar_compose() {  # acrescentar_compose arquivo.yml
  if grep -q '^COMPOSE_FILE=' "$ENV"; then
    grep -q "^COMPOSE_FILE=.*$1" "$ENV" || sed -i "s/^COMPOSE_FILE=.*/&:$1/" "$ENV"
  else
    echo "COMPOSE_FILE=docker-compose.yml:$1" >> "$ENV"
  fi
}

echo "== .env"
[ -z "$(tail -c 1 "$ENV")" ] || echo >> "$ENV"   # garante a quebra de linha no fim antes de acrescentar
if ! grep -q '^JARVIS_TOOLS_TOKEN=..*' "$ENV"; then
  sed -i '/^JARVIS_TOOLS_TOKEN=/d' "$ENV"
  printf '\n# ---- Ferramentas do Jarvis (jarvis-tools) ----\nJARVIS_TOOLS_TOKEN=%s\n' "$(openssl rand -hex 32)" >> "$ENV"
fi
# Entre aspas: o .env também é lido pelo bash (scripts/pergunta.sh), e o espaço quebraria a leitura
grep -q '^JARVIS_CIDADE=' "$ENV" || echo 'JARVIS_CIDADE="Cornélio Procópio, PR"' >> "$ENV"
grep -q '^MOODLE_URL=' "$ENV" || echo 'MOODLE_URL=https://moodle.utfpr.edu.br' >> "$ENV"
grep -q '^GOOGLE_CONTA_PADRAO=' "$ENV" || echo 'GOOGLE_CONTA_PADRAO=' >> "$ENV"
acrescentar_compose docker-compose.tools.yml

echo "== Busca na web (SearXNG)"
if docker inspect "$SEARXNG_CONTAINER" > /dev/null 2>&1; then
  REDE=$(docker inspect "$SEARXNG_CONTAINER" \
    --format '{{range $nome, $v := .NetworkSettings.Networks}}{{$nome}}{{"\n"}}{{end}}' \
    | grep -v -x -e bridge -e host -e none | head -n 1 || true)
  PORTA=$(docker inspect "$SEARXNG_CONTAINER" --format '{{range $p, $v := .Config.ExposedPorts}}{{$p}}{{"\n"}}{{end}}' \
    | grep '/tcp' | head -n 1 | cut -d/ -f1 || true)
  if [ -n "$REDE" ]; then
    definir_env SEARXNG_REDE "$REDE"
    definir_env SEARXNG_URL "http://$SEARXNG_CONTAINER:${PORTA:-8080}"
    acrescentar_compose docker-compose.searxng.yml
    echo "   SearXNG na rede $REDE, porta ${PORTA:-8080}"
  else
    echo "   aviso: o $SEARXNG_CONTAINER só está na rede padrão do Docker; não dá para ligar automaticamente."
    echo "   Veja 'Busca na web' em docs/fase1.md. Por enquanto, a busca fica desligada."
  fi
else
  echo "   Não achei o contêiner $SEARXNG_CONTAINER: a busca fica desligada."
  echo "   (Se ele tem outro nome, rode: SEARXNG_CONTAINER=nome ./scripts/fase1-jarvis-tools.sh)"
fi

echo "== Pasta de dados (tokens do Moodle e do Google, só do jarvis-tools)"
mkdir -p data/jarvis-tools/google
chmod 700 data/jarvis-tools data/jarvis-tools/google
if [ "$(id -u)" = 0 ]; then  # rodando com sudo: a pasta precisa ser do usuário do contêiner
  chown -R "$(valor_env HERMES_UID):$(valor_env HERMES_GID)" data/jarvis-tools
fi
docker compose config -q

echo "== Compilando e subindo o jarvis-tools"
docker compose build jarvis-tools
docker compose up -d jarvis-tools
for _ in $(seq 1 60); do
  [ "$(docker inspect --format '{{.State.Health.Status}}' jarvis-tools 2>/dev/null)" = healthy ] && break
  sleep 2
done
[ "$(docker inspect --format '{{.State.Health.Status}}' jarvis-tools)" = healthy ] \
  || { echo "O jarvis-tools não ficou saudável. Veja: docker compose logs --tail 50 jarvis-tools" >&2; exit 1; }

echo "== Hermes: servidor MCP 'jarvis'"
TOKEN=$(valor_env JARVIS_TOOLS_TOKEN)
"${H[@]}" config set JARVIS_TOOLS_TOKEN "$TOKEN" > /dev/null   # vai para data/hermes/.env
"${H[@]}" config set mcp_servers.jarvis.url http://jarvis-tools:8000/mcp
# shellcheck disable=SC2016  # o ${...} é resolvido pelo Hermes, não pelo shell
"${H[@]}" config set mcp_servers.jarvis.headers.Authorization 'Bearer ${JARVIS_TOOLS_TOKEN}' > /dev/null
"${H[@]}" config set mcp_servers.jarvis.tools.prompts false
"${H[@]}" config set mcp_servers.jarvis.tools.resources false
"${H[@]}" config set mcp_servers.jarvis.timeout 60
"${H[@]}" config set mcp_servers.jarvis.connect_timeout 15

echo "== API sem terminal (a hora vem do jarvis-tools)"
"${H[@]}" tools disable terminal --platform api_server

echo "== Raciocínio desligado"
# "false", não "none": o config set do Hermes grava "none" como vazio, e aí o raciocínio volta ao padrão
"${H[@]}" config set agent.reasoning_effort false

echo "== SOUL.md novo"
cp hermes/SOUL.md data/hermes/SOUL.md

echo "== Reiniciando o Hermes (ele lê a lista de ferramentas ao conectar)"
docker compose up -d hermes
docker compose restart hermes
for _ in $(seq 1 90); do
  curl -fsS http://127.0.0.1:8642/health > /dev/null 2>&1 && break
  sleep 1
done
curl -fsS http://127.0.0.1:8642/health > /dev/null || { echo "O Hermes não respondeu. Veja: docker compose logs --tail 50 hermes" >&2; exit 1; }

echo "== Conexão do Hermes com o jarvis-tools"
"${H[@]}" mcp test jarvis || echo "   aviso: o teste falhou; veja docker compose logs --tail 50 hermes jarvis-tools"

echo
echo "== O que está ligado"
RECURSOS=$(docker compose exec -T jarvis-tools python -m app.cli recursos)
echo "   $RECURSOS"
if grep -q '"busca": true' <<< "$RECURSOS"; then
  echo "== Teste da busca"
  docker compose exec -T jarvis-tools python -m app.cli buscar "previsão do tempo" | sed -n '1,3p'
fi

echo
echo "Pronto. Próximos passos (detalhes em docs/fase1.md):"
if grep -q '"moodle": false' <<< "$RECURSOS"; then
  echo "  Moodle: docker compose exec -it jarvis-tools python -m app.moodle_login"
fi
if grep -q '"google": \[\]' <<< "$RECURSOS"; then
  echo "  Google: salve o cliente OAuth em data/jarvis-tools/google/cliente.json e rode"
  echo "          docker compose exec -it jarvis-tools python -m app.google_login pessoal"
fi
echo "  Depois de cada login: docker compose restart jarvis-tools hermes"
echo "  Teste tudo: python3 scripts/testar-jarvis.py    Converse: python3 scripts/chat.py"

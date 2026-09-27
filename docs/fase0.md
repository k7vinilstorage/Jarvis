# Fase 0: fundação

**Objetivo:** Ollama com o modelo do Jarvis rodando 100% na GPU, Hermes respondendo pela API, e os serviços de voz (Whisper, Piper e openWakeWord) no ar. No fim você terá medições de base que orientam os ajustes da Fase 1.

Tempo estimado: 30 a 60 minutos, a maior parte esperando downloads.

## O que vai rodar

| Contêiner | Porta no servidor | Acesso | Função |
|---|---|---|---|
| `ollama` | 11434 | rede local, como hoje | LLM na GPU |
| `jarvis-ollama-init` | nenhuma | roda uma vez e sai | baixa o modelo e cria o `jarvis-qwen` |
| `hermes` | 8642 (API) | só o próprio servidor | o agente |
| `hermes` | 9119 (painel) | rede local, com senha | painel web do Hermes |
| `jarvis-whisper` | 10300 | só o próprio servidor | fala para texto |
| `jarvis-piper` | 10200 | só o próprio servidor | texto para fala |
| `jarvis-openwakeword` | 10400 | só o próprio servidor | palavra de ativação |

O stack antigo `wisper` (whisper.cpp na 9000 e piper na 5002) continua rodando e não conflita com nada. Só o contêiner `ollama` atual é substituído.

> **Segurança:** o Hermes executa comandos dentro do próprio contêiner. Nunca abra as portas 8642, 9119 ou 11434 para a internet no roteador.

---

## Antes de começar

Rode no servidor:

```bash
docker compose version        # precisa do Compose v2
nvidia-smi                    # quanto de VRAM já está em uso, e por quem
timedatectl                   # o fuso deve ser America/Sao_Paulo
df -h /var/lib/docker         # precisa de uns 12 GB livres (imagens + modelo)
```

Se o fuso estiver errado: `sudo timedatectl set-timezone America/Sao_Paulo`.

No `nvidia-smi`, preste atenção na **Open WebUI**. A imagem `:cuda` dela pode reservar VRAM para modelos de embedding, e o Jellyfin também usa a GPU enquanto transcodifica. O modelo do Jarvis deve ocupar em torno de 5 GB dos 6 GB (é uma estimativa; o passo 4 confirma).

---

## Passo 1: copiar o projeto para o servidor

No seu computador:

```bash
scp jarvis-fase0.tar.gz usuario@IP-DO-SERVIDOR:~
```

No servidor:

```bash
sudo mkdir -p /opt/jarvis && sudo chown "$USER": /opt/jarvis
tar xzf ~/jarvis-fase0.tar.gz -C /opt
cd /opt/jarvis
```

> **Portainer:** o stack vai aparecer lá como `jarvis`, com controle limitado, porque foi criado pela linha de comando. Use o Portainer para ver status e logs. Para mudar algo, edite os arquivos em `/opt/jarvis` e rode `docker compose up -d`.

---

## Passo 2: configurar o `.env`

```bash
cp .env.example .env
sed -i "s/^HERMES_API_KEY=.*/HERMES_API_KEY=$(openssl rand -hex 32)/" .env
sed -i "s/^DASHBOARD_SECRET=.*/DASHBOARD_SECRET=$(openssl rand -hex 32)/" .env
sed -i "s/^JARVIS_TOOLS_TOKEN=.*/JARVIS_TOOLS_TOKEN=$(openssl rand -hex 32)/" .env
id -u; id -g
nano .env
```

No editor:
- coloque a saída de `id -u` e `id -g` em `HERMES_UID` e `HERMES_GID`;
- defina uma senha em `DASHBOARD_PASSWORD`.

---

## Passo 3: migrar o Ollama atual sem perder os modelos

Descubra onde o contêiner atual guarda os modelos e quais variáveis ele usa:

```bash
docker inspect ollama --format '{{range .Mounts}}{{.Type}} {{.Name}} {{.Source}} -> {{.Destination}}{{println}}{{end}}'
docker inspect ollama --format '{{range .Config.Env}}{{println .}}{{end}}'
```

Conforme a primeira saída:

| O que apareceu | O que fazer |
|---|---|
| `volume NOME ... -> /root/.ollama` | coloque `NOME` em `OLLAMA_VOLUME` no `.env` (o padrão é `ollama`) |
| `bind /algum/caminho -> /root/.ollama` | no `docker-compose.yml`, troque `- ollama-models:/root/.ollama` por `- /algum/caminho:/root/.ollama` e apague o bloco `volumes:` do final do arquivo |
| nada | os modelos estão dentro do contêiner e serão baixados de novo. Rode `docker volume create ollama` |

Na segunda saída, se houver variáveis que você mesmo definiu (fora `PATH`, `NVIDIA_*`, `LD_LIBRARY_PATH` e `OLLAMA_HOST`), copie-as para o bloco `environment:` do serviço `ollama` no compose.

Depois remova o contêiner antigo. O volume com os modelos continua intacto:

```bash
docker stop ollama && docker rm ollama
```

A Open WebUI fica sem modelos até o próximo passo terminar.

---

## Passo 4: subir o Ollama e os serviços de voz

```bash
docker compose up -d ollama ollama-init whisper piper openwakeword
docker compose logs -f ollama-init
```

Espere aparecer `[init] pronto.` e saia com Ctrl+C. O primeiro download do `qwen3.5:4b` tem 3,4 GB.

### Verificações

```bash
docker compose ps -a
```
O `ollama` deve estar `healthy` e o `jarvis-ollama-init` com `Exited (0)`. Whisper, Piper e openWakeWord baixam os próprios modelos na primeira vez e ficam `healthy` em alguns minutos. Se demorar, veja com `docker compose logs whisper piper`.

```bash
docker exec ollama ollama list
```
Devem aparecer `qwen3.5:4b` e `jarvis-qwen`, além dos seus modelos antigos.

```bash
docker exec -it ollama ollama run jarvis-qwen --verbose "Em uma frase: o que faz um assistente pessoal?"
docker exec -it ollama ollama run jarvis-qwen --verbose --think=false "Em uma frase: o que faz um assistente pessoal?"
```
Anote o `eval rate` (velocidade de geração) e o `prompt eval rate` (velocidade de leitura) das duas execuções. Anote também se, na primeira, o modelo mostrou um bloco de raciocínio ("Thinking...") antes de responder. A diferença entre as duas mostra quanto o raciocínio custa em tempo.

```bash
docker exec ollama ollama ps
nvidia-smi
```
No `ollama ps`, a coluna `PROCESSOR` precisa mostrar **`100% GPU`** e o contexto deve ser 65536. No `nvidia-smi`, anote a VRAM total usada e deixe uma sobra de uns 400 MB para o Jellyfin transcodificar. Se aparecer uma divisão entre CPU e GPU, veja [Problemas comuns](#problemas-comuns).

> O `.env` já vem com `JARVIS_NUM_GPU=99`, que força todas as camadas na GPU. Sem isso, o Ollama estima a memória por cima (conta a parte de visão do qwen3.5, os buffers e o contexto de 64K) e deixa algumas camadas na CPU mesmo com VRAM sobrando.

---

## Passo 5: configurar o Hermes

Coloque a personalidade do Jarvis no lugar e aponte o Hermes para o Ollama. A configuração é feita direto, sem o assistente interativo, porque ele tenta primeiro o login no Nous Portal:

```bash
mkdir -p data/hermes && cp hermes/SOUL.md data/hermes/
docker compose run --rm hermes sh -c '
hermes config set model.provider custom &&
hermes config set model.base_url http://ollama:11434/v1 &&
hermes config set model.default jarvis-qwen &&
hermes config set model.api_key ollama &&
hermes config set model.context_length 65536 &&
hermes config set web.keyless_fallback false &&
hermes config set skills.write_approval true &&
hermes config set skills.guard_agent_created true &&
hermes config set auxiliary.background_review.enabled false &&
hermes config set curator.enabled false &&
hermes config get model'
head -3 data/hermes/SOUL.md
```

- **`model.*`** aponta o Hermes para o Ollama pela rede interna do Docker. O Ollama não confere a chave; `ollama` é só um valor preenchido.
- **`model.context_length`** informa ao Hermes o tamanho real do contexto, que o Ollama não informa sozinho.
- **`web.keyless_fallback false`** impede que buscas web sejam enviadas a serviços gratuitos na nuvem. A busca volta com o SearXNG (seção opcional abaixo ou Fase 1).
- **Skills travadas** (segurança), com quatro ajustes:
  - `skills.write_approval true`: toda criação, edição ou remoção de skill fica pendente até você aprovar. Para revisar: `/skills pending`, `/skills diff <id>` e `/skills approve <id>` no chat do Hermes (`docker compose exec -it hermes hermes`) ou no painel.
  - `skills.guard_agent_created true`: examina o conteúdo de skills escritas pelo agente em busca de padrões perigosos.
  - `auxiliary.background_review.enabled false`: desliga a revisão automática que roda após cada turno e cria skills e memórias por conta própria. Ela também reprocessaria a conversa inteira na GPU, atrasando a próxima resposta. O Jarvis continua salvando memória quando você pede.
  - `curator.enabled false`: desliga a manutenção automática que arquiva e reorganiza skills.
  - O `SOUL.md` também pede ao Jarvis que só sugira skills, sem criá-las.
- O **`config get model`** deve mostrar `custom`, `http://ollama:11434/v1` e `jarvis-qwen`.
- O **`head`** deve mostrar `# Jarvis`.

> **Não use `hermes setup` sem argumentos:** o assistente rápido começa pelo login no Nous Portal (o serviço pago da Nous). Se precisar do assistente, use `docker compose run --rm hermes setup model`, que vai direto para a escolha do provedor, e escolha **Custom endpoint** com a mesma URL e o mesmo modelo.

---

## Passo 6: subir o Hermes e testar

```bash
docker compose up -d hermes
docker compose logs -f hermes
```
Espere a linha `API server listening on http://0.0.0.0:8642` e saia com Ctrl+C.

```bash
set -a; . ./.env; set +a
curl -s http://127.0.0.1:8642/health
curl -s -H "Authorization: Bearer $HERMES_API_KEY" http://127.0.0.1:8642/v1/models
```
O primeiro comando deve responder `{"status": "ok", ...}` e o segundo deve listar `hermes-agent`.

### Testes funcionais

O script `scripts/pergunta.sh` envia uma pergunta e mostra a resposta, os tokens e o tempo.

| # | Comando | O que se espera |
|---|---|---|
| 1 | `./scripts/pergunta.sh "Olá! Quem é você? Responda em uma frase."` | Resposta em português, como Jarvis. O 1º tempo inclui o carregamento. |
| 2 | Repita o teste 1 | O tempo "quente", que é o que importa |
| 3 | `./scripts/pergunta.sh "Que horas são agora e que dia da semana é hoje?"` | Hora de Brasília correta. Provavelmente usa uma ferramenta. |
| 4a | `./scripts/pergunta.sh "Meu nome é SEU_NOME. Guarde isso na sua memória."` | Confirma que guardou |
| 4b | `./scripts/pergunta.sh "Qual é o meu nome?"` | Responde o seu nome, numa sessão nova |
| 5 | `docker compose exec hermes hermes prompt-size` | Tamanho do prompt fixo: prompt de sistema e ferramentas |

Para conferir o teste 4 por fora: `ls data/hermes/memories/` e `cat` no arquivo que aparecer.

**Painel:** abra `http://IP-DO-SERVIDOR:9119` e entre com `DASHBOARD_USER` e `DASHBOARD_PASSWORD`. As sessões dos testes devem aparecer lá.

---

## Passo 7: medir

Um script faz todas as medições de uma vez:

```bash
python3 scripts/medicoes.py --nome SEU_NOME
```

- Leva de 3 a 6 minutos. Não use o Jarvis nem a Open WebUI enquanto ele roda, porque tudo divide a mesma GPU.
- Ele tira o modelo da VRAM para medir o tempo frio, repete os testes 1 a 4 do passo 6 pelo Hermes, mede a velocidade direto no Ollama e anota GPU, RAM e o prompt fixo.
- Além da tabela, ele mede o tempo até a primeira palavra da resposta, que é o que mais pesa na voz, e registra as ferramentas usadas em cada teste.
- No fim, ele espera o minuto virar e repete o teste 2 (teste 2b). Se o horário no prompt mudar a cada minuto, o Ollama perde o cache e relê o prompt inteiro; o 2b mostra isso.
- O teste 4 grava o seu nome na memória do Jarvis, como no uso normal. Sem `--nome`, o script pergunta; Enter pula o teste.
- O relatório vai para `medicoes/`, com a tabela preenchida, observações automáticas e as respostas completas. A chave da API não aparece nele.

Opções: `--rotulo` muda o nome do arquivo (o padrão é `sem-honcho` ou `com-honcho`), `--pular-frio` não descarrega o modelo, `--sem-memoria` pula o teste 4, `--rapido` pula o teste 2b.

Guarde o relatório, porque ele orienta a Fase 1. A tabela que ele preenche:

| Medida | Valor |
|---|---|
| VRAM total usada com o modelo carregado (`nvidia-smi`) | |
| `ollama ps`, coluna PROCESSOR | |
| Geração, com raciocínio (`eval rate`, tokens/s) | |
| Geração, sem raciocínio (`--think=false`) | |
| Leitura do prompt (`prompt eval rate`, tokens/s) | |
| O modelo raciocina antes de responder? | |
| Prompt fixo do Hermes (`prompt-size`) | |
| Teste 1: tempo frio | |
| Teste 2: tempo quente | |
| Teste 2b: quente, depois de o minuto virar | |
| Teste 3: hora certa? tempo | |
| Teste 4: lembrou o nome? | |
| RAM livre com tudo rodando (`free -h`) | |

Como ler os resultados:
- **Tempo quente acima de ~5 s** para uma pergunta simples: a primeira ação da Fase 1 é enxugar as ferramentas (o `prompt-size` mostra o peso de cada parte).
- **Raciocínio longo:** testar `agent.reasoning_effort` na Fase 1.

---

## Passo 8: conferir o que já existia

- **Open WebUI:** os modelos devem aparecer de novo. Se a conexão com o Ollama usa o IP do servidor ou `host.docker.internal:11434`, nada muda.
- **Disputa de GPU:** se você usar outro modelo na Open WebUI, o Ollama descarrega o do Jarvis para caber na VRAM. A próxima pergunta ao Jarvis leva alguns segundos a mais para recarregar.
- **Stack antigo `wisper`:** antes de removê-lo, veja em Open WebUI → Configurações → Áudio se ele usa as portas 9000 ou 5002.

---

## Opcional: busca web local com o seu SearXNG

1. Descubra a rede do contêiner `searxng`:
   ```bash
   docker inspect searxng --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}'
   ```
2. No `docker-compose.yml`, descomente as linhas marcadas com "SearXNG": três no serviço `hermes` e quatro no final do arquivo. Apague só o `# ` do começo de cada linha, para manter a indentação. Troque `open-web-ui_default` pelo nome que apareceu no item 1 e confira com `docker compose config -q`: se não imprimir nada, o arquivo está válido.
3. Aplique e teste se o SearXNG responde em JSON:
   ```bash
   docker compose up -d hermes
   docker compose exec hermes python3 -c "import urllib.request as u; print(u.urlopen('http://searxng:8080/search?q=teste&format=json').status)"
   ```
   O esperado é `200`. Um erro 403 significa que o formato JSON está desativado no `settings.yml` do SearXNG. A Open WebUI também precisa dele ativado, então provavelmente já está.
4. Configure o Hermes e teste:
   ```bash
   docker compose exec hermes hermes config set SEARXNG_URL http://searxng:8080
   docker compose exec hermes hermes config set web.search_backend searxng
   docker compose restart hermes
   ./scripts/pergunta.sh "Pesquise na web: qual é a versão mais recente do Ubuntu?"
   ```

---

## Problemas comuns

| Sintoma | Causa provável e solução |
|---|---|
| `external volume "ollama" not found` | `OLLAMA_VOLUME` errado. Refaça o passo 3. |
| `ollama ps` mostra CPU e GPU divididas com VRAM sobrando | Confira se `JARVIS_NUM_GPU=99` está no `.env` e se `docker exec ollama ollama show jarvis-qwen --parameters` mostra `num_gpu 99`. Se não mostrar, rode `docker compose run --rm ollama-init` e depois `docker exec ollama ollama stop jarvis-qwen` para recarregar. |
| Erro de memória (`out of memory`) ao carregar o modelo | Falta VRAM de verdade. Veja no `nvidia-smi` quem está usando. Para testar, pare a Open WebUI por um momento (`docker stop open-web-ui-open-webui-1`). Se for ela, troque a imagem `:cuda` pela `:main`, porque o trabalho pesado é do Ollama. Em último caso, reduza `JARVIS_NUM_GPU` (o `docker logs ollama 2>&1 \| grep offloaded` mostra quantas camadas o modelo tem). |
| O Hermes não encontra o modelo | Teste de dentro do contêiner: `docker compose exec hermes python3 -c "import urllib.request as u; print(u.urlopen('http://ollama:11434/v1/models').read()[:300])"`. Confira também o nome em `config get model`. |
| O teste devolve `ERRO` com 401 | A chave não bate. Rode `set -a; . ./.env; set +a` de novo. Se mudou a chave no `.env`, rode `docker compose up -d hermes`. |
| A resposta mostra JSON de ferramenta como texto | Rode `docker exec ollama ollama show jarvis-qwen`. Em "Capabilities" precisa aparecer `tools`. |
| Hora errada no teste 3 | Fuso do servidor. Compare `timedatectl` com `docker compose exec hermes date`. |
| `Permission denied` em `data/hermes` | `HERMES_UID`/`HERMES_GID` diferentes do seu usuário. Rode `sudo chown -R "$(id -u):$(id -g)" data/hermes` e `docker compose up -d hermes`. |
| Whisper ou Piper não ficam `healthy` | Veja `docker compose logs whisper piper`. Normalmente é o download do modelo ou da voz na primeira vez. |

Diagnóstico geral do Hermes:

```bash
docker compose exec hermes hermes doctor
docker compose logs --tail 100 hermes
```

---

## A Fase 0 está pronta quando

- [ ] `ollama ps` mostra `jarvis-qwen` com `100% GPU` e contexto 65536
- [ ] Whisper, Piper e openWakeWord estão `healthy`
- [ ] Os testes 1 a 4 funcionam
- [ ] O relatório de `scripts/medicoes.py` foi gerado
- [ ] A Open WebUI continua funcionando

**Próximo, na Fase 1 (o cérebro em texto):**
- enxugar as ferramentas com base no `prompt-size`;
- skills de clima e agenda;
- MCP do Moodle;
- Telegram como segundo canal;
- o teste de 10 perguntas reais, que decide se o `qwen3.5:4b` é suficiente.

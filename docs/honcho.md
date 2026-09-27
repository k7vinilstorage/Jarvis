# Honcho: memória de longo prazo

O [Honcho](https://github.com/plastic-labs/honcho) observa as conversas em segundo plano e monta um modelo de quem você é: preferências, rotina, objetivos. A cada turno, o Hermes recebe um resumo disso. Ele complementa a memória nativa do Hermes (`MEMORY.md` e `USER.md`), que continua funcionando.

Aqui ele roda **inteiro no servidor**, sem nuvem:

| Peça | Onde roda |
|---|---|
| Postgres com pgvector, Redis, API e deriver do Honcho | contêineres novos na stack `jarvis` |
| LLM do Honcho (extração de fatos, resumos, dialético) | o mesmo `jarvis-qwen`, na GPU |
| Embeddings | `jarvis-embed` (bge-m3, multilíngue), **na CPU**, para não ocupar VRAM |

A versão fica fixada em `v3.2.1` (`HONCHO_VERSION` no `.env`) e é compilada a partir do código-fonte.

## Antes de ativar: os custos

- **RAM:** cerca de 2,5 a 3 GB a mais. Confira com `free -h` se sobra isso com tudo rodando.
- **Disco:** a imagem do Honcho, mais 1,2 GB do bge-m3.
- **GPU compartilhada:** o Ollama atende uma requisição por vez. Se o Honcho estiver processando quando você fizer uma pergunta, a resposta espera alguns segundos. A configuração abaixo reduz isso ao mínimo; se ainda incomodar, veja [Se as respostas ficarem lentas](#se-as-respostas-ficarem-lentas).
- **Qualidade:** o Honcho foi pensado para modelos maiores que 4B. As observações podem sair mais simples. Trate como experimento e avalie depois de alguns dias de uso.

---

## Instalação

Tudo dentro de `/opt/jarvis`.

### 1. Baixar o código do Honcho

```bash
git clone --depth 1 --branch v3.2.1 https://github.com/plastic-labs/honcho.git honcho-src
```

### 2. Configurar

```bash
cp honcho/honcho.env.example honcho/honcho.env
grep -q '^HONCHO_VERSION=' .env || sed -n '/^# ---- Honcho/,$p' .env.example >> .env
sed -i "s/^HONCHO_DB_PASSWORD=.*/HONCHO_DB_PASSWORD=$(openssl rand -hex 16)/" .env
grep -q '^COMPOSE_FILE=' .env || echo 'COMPOSE_FILE=docker-compose.yml' >> .env
grep -q '^COMPOSE_FILE=.*docker-compose.honcho.yml' .env || sed -i 's/^COMPOSE_FILE=.*/&:docker-compose.honcho.yml/' .env
docker compose config -q && echo ok
```

A linha `COMPOSE_FILE` faz o `docker compose` incluir o `docker-compose.honcho.yml` (e o `docker-compose.tools.yml` da Fase 1) em todos os comandos. O último comando deve imprimir só `ok`.

### 3. Compilar e subir

```bash
docker compose build honcho-api
docker compose up -d
docker compose logs -f ollama-init
```

A compilação leva alguns minutos e só acontece na primeira vez. Espere `[init] pronto.` no log; o `ollama-init` agora também baixa o bge-m3 e cria o `jarvis-embed`. Saia com Ctrl+C.

### 4. Verificar os serviços

```bash
docker compose ps -a
```

O `jarvis-honcho-migrate` deve estar `Exited (0)`, o `jarvis-honcho-api` `healthy` e o `jarvis-honcho-deriver` rodando.

Confira também se os embeddings saem com 1024 dimensões:

```bash
curl -s http://127.0.0.1:11434/v1/embeddings -H "Content-Type: application/json" \
  -d '{"model":"jarvis-embed","input":"teste"}' \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"][0]["embedding"]))'
docker exec ollama ollama ps
```

O primeiro comando deve imprimir `1024`. No `ollama ps`, o `jarvis-embed` aparece com `100% CPU` e o `jarvis-qwen` continua com `100% GPU`.

### 5. Ligar o Hermes ao Honcho

```bash
docker compose exec -it hermes hermes memory setup honcho
```

No assistente:

| Pergunta | Resposta |
|---|---|
| Cloud ou local | **local** (self-hosted) |
| Base URL | `http://honcho-api:8000` |
| Token / JWT | deixe vazio |
| Nome do usuário (peer) | seu nome, minúsculo e sem espaços |
| Nome da IA | `jarvis` |
| Demais perguntas | o padrão |

Se o Honcho não aparecer como opção de memória, o plugin saiu da imagem do Hermes (a Nous está tirando os provedores de memória do núcleo). Instale e rode o assistente de novo:

```bash
docker compose exec hermes hermes plugins install NousResearch/hermes-plugin-honcho
docker compose exec hermes hermes plugins enable honcho
```

### 6. Ajustar para o modelo pequeno e para a voz

```bash
./scripts/honcho-ajustes.sh
docker compose exec hermes hermes honcho status
```

O script grava estes valores no `honcho.json` do Hermes e reinicia o Hermes:

| Ajuste | Valor | Por quê |
|---|---|---|
| `recallMode` | `context` | O contexto do Honcho entra no prompt sem acrescentar ferramentas, o que é melhor para um modelo de 4B |
| `contextTokens` | 800 | Limita o tamanho do que é injetado a cada turno |
| `dialecticCadence` | 5 | A consulta dialética, que chama o LLM, roda só a cada 5 turnos |
| `dialecticReasoningLevel` | `minimal` | Consulta curta: uma iteração, até 250 tokens |
| `timeout` | 10 | Se o Honcho cair, o Jarvis não fica esperando |

O `status` deve mostrar a conexão com `http://honcho-api:8000`.

---

## Testar

O deriver junta as mensagens até ter uns 512 tokens, ou por até 30 minutos, antes de processar. Para ver o resultado na hora, ligue o modo de teste:

```bash
cat >> honcho/honcho.env <<'EOF'
DERIVER_FLUSH_ENABLED=true
DERIVER_LOG_OBSERVATIONS=true
EOF
docker compose up -d honcho-api honcho-deriver

./scripts/pergunta.sh "Uma coisa sobre mim: tomo café sem açúcar e acordo às 6h nos dias de semana."
docker compose logs -f honcho-deriver
```

Espere as observações aparecerem no log e saia com Ctrl+C. Depois, numa sessão nova:

```bash
./scripts/pergunta.sh "Como eu gosto do meu café?"
```

Quando terminar, **tire as duas linhas de teste** do final do `honcho/honcho.env` e rode `docker compose up -d honcho-api honcho-deriver` de novo.

Por fim, meça o impacto e compare com o relatório que você gerou sem o Honcho:

```bash
python3 scripts/medicoes.py --sem-memoria
```

O `--sem-memoria` evita gravar o seu nome de novo na memória do Hermes. O relatório sai com o rótulo `com-honcho`.

---

## Se as respostas ficarem lentas

Em ordem, do ajuste mais simples ao mais pesado:

1. **Consultar menos o dialético:** edite `dialecticCadence` no script para 10 ou mais e rode-o de novo.
2. **Tirar o trabalho de fundo da GPU:** o deriver, os resumos e os "sonhos" passam para uma cópia do modelo na CPU. O dialético continua na GPU, porque ele lê históricos longos e seria lento demais na CPU. Custa uns 4 GB de RAM e uso de CPU, o que pode deixar o Whisper mais lento enquanto o Honcho trabalha.
   ```bash
   docker exec ollama sh -c 'printf "FROM qwen3.5:4b\nPARAMETER num_ctx 16384\nPARAMETER num_gpu 0\n" > /tmp/M && ollama create jarvis-qwen-cpu -f /tmp/M'
   sed -i -E 's/^((DERIVER|SUMMARY|DREAM_DEDUCTION|DREAM_INDUCTION)_MODEL_CONFIG__MODEL)=jarvis-qwen$/\1=jarvis-qwen-cpu/' honcho/honcho.env
   docker compose up -d honcho-api honcho-deriver
   ```

---

## Problemas comuns

| Sintoma | Causa provável e solução |
|---|---|
| `jarvis-honcho-migrate` sai com erro | Veja `docker compose logs honcho-migrate`. Se você mudou `HONCHO_DB_PASSWORD` depois da primeira subida, o volume guarda a senha antiga: volte a senha anterior ou recrie o banco (abaixo). |
| API `unhealthy` com "dim ... does not match EMBEDDING_VECTOR_DIMENSIONS" | O ajuste de dimensões não rodou. Rode `docker compose run --rm honcho-migrate` e depois `docker compose up -d`. |
| O deriver processa mas não gera observações | O Ollama ignorou o formato `json_schema`. Descomente `DERIVER_MODEL_CONFIG__STRUCTURED_OUTPUT_MODE=json_object` no `honcho/honcho.env` e rode `docker compose up -d honcho-deriver`. |
| `hermes honcho status` não conecta | Teste de dentro do Hermes: `docker compose exec hermes python3 -c "import urllib.request as u; print(u.urlopen('http://honcho-api:8000/health').read())"`. |
| Erros de modelo no log do Honcho | Os modelos precisam suportar chamadas de ferramenta. O `jarvis-qwen` suporta; confira com `docker exec ollama ollama show jarvis-qwen`. |

## Trocar o modelo de embeddings

O tamanho dos vetores fica gravado no banco. Trocar o bge-m3 por um modelo com outra dimensão **apaga a memória do Honcho**:

```bash
docker compose rm -sf honcho-api honcho-deriver honcho-migrate honcho-db
docker volume rm jarvis_honcho-db
# ajuste HONCHO_EMBED_BASE no .env e EMBEDDING_VECTOR_DIMENSIONS no honcho/honcho.env
docker compose up -d
```

## Desativar

```bash
docker compose exec hermes hermes honcho disable
sed -i 's/:docker-compose.honcho.yml//' .env
docker compose up -d --remove-orphans
```

Os dados continuam no volume `jarvis_honcho-db` até você removê-lo.

## Para a Fase 2

A bridge de voz vai mandar o cabeçalho `X-Hermes-Session-Key` com um identificador fixo por cômodo. Assim, o Honcho mantém a mesma memória entre as sessões de voz.

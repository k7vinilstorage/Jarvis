# Jarvis: plano e decisões

Última atualização: 27/09/2026 (firmware do ESP32).

**Ordem combinada com o usuário:** Fase 2 (voz) → ESP32 (antecipado em 27/09) → acabamento da Fase 1 (precisão das respostas) → Honcho.

## Situação
- **Fase 0:** pronta e medida.
- **Fase 1: meta atingida.** No teste real de 25/09 às 19h06 (`qwen3.5:4b`, temperatura 0,5, Google com duas contas), foram 27 de 27 casos e 10 de 10 perguntas da lista.
- **Acabamento da Fase 1: em andamento (27/09).** O teste de 27/09 às 22h59 deu 26 de 27, mas lendo as respostas:
  - **privacidade:** em "servidores", ele disse "Vou verificar" e buscou na web com o nome do usuário;
  - "fim de semana" num domingo: a ferramenta misturava hoje com o sábado seguinte, e ele disse "sábado, 30 de setembro" (era quarta);
  - "próximas semanas" dava erro na ferramenta; ele repetiu com "semana" e negou uma entrega de TCC de 10/10;
  - chamou agenda e e-mails numa pergunta de Moodle (1ª palavra em 6,8 s; 8,2 s na voz);
  - ofereceu o que não faz ("quer que eu te avise?", "concorda com a criação da skill?");
  - inventou ("deve ter chegado o saldo", "três coisas" seguidas de seis) e estropiou nomes ("Aprendizagem Professa");
  - buscou na web para explicar webhook; markdown em 6 respostas.
  Feito: ferramentas corrigidas (com testes), SOUL reescrito, testador mais exigente (`testes/test_conferencias.py`). Decisões do usuário: nome só de vez em quando; no domingo, "fim de semana" é o próximo; "Engenharia da Computação" continua na lista de disciplinas; medir temperatura 0,3 contra 0,5.
  - **2ª rodada (27/09, 23h25 e 23h28):** 21 de 30 com 0,5 e 19 de 30 com 0,3 (lista: 7 e 5 de 10). O 0,3 não tirou os nomes estropiados e piorou respostas: **fica 0,5**. Próximo suspeito: `presence_penalty` 0,3, que penaliza repetir o que já está no contexto (os nomes que vieram da ferramenta).
  - Achados: criou um evento sem confirmar e no dia errado ("amanhã" virou 1º/10); leu em voz alta o código de um e-mail de "one-time passcode"; usou o id de exemplo da descrição do `ler_email`; o Moodle mandava o mesmo questionário com `instance` diferente nas ações e no calendário (e "Início de"/"Término de" no nome), e ele contava duas provas; duas tarefas reais com o mesmo nome e prazo no TCC; listas longas lidas inteiras.
  - Corrigido: `agenda_criar` em dois passos (a 1ª chamada só guarda o pedido; cria se a mesma chamada voltar entre 5 s e 10 min); e-mails de código escondem os números; "o mais novo de todas as contas" na lista de e-mails; questionários juntados pelo nome limpo na disciplina; tarefas iguais numa linha; toda lista termina com "Ao responder: ..." (em frases corridas, só os 3 primeiros). No testador: criação afirmada sem pedido reprova (chamar o `agenda_criar` não), `pausa_antes` por turno, "fonte" só como citação, fim de semana sem datas vale.
- Problemas de 25/09 (antes da voz), que o teste também não pegava:
  - recusou "dicas para estudar", porque a regra de precisão foi aplicada a conhecimento geral;
  - a busca se contradisse (Ubuntu 26.04 "esperado para 2027"; Python 3.16 chamado de mais recente, sem citar o site);
  - "entregas de TCC nas próximas semanas" olhou só esta semana e fez 4 chamadas;
  - inventou um comentário nas provas ("parece um erro no sistema");
  - respondeu "Sim, mas nenhuma" em "atividades para amanhã";
  - saíram listas e negrito em 3 respostas.
- **Fase 2 (voz):** instalada em 25/09; o usuário relatou em 27/09 que funcionou. No `voz-20260925-2015.md`:
  - o Whisper acertou 100% das 3 perguntas, em 1,4 a 2,0 s;
  - o "1º áudio" (2,5 a 3,0 s) é o "Um momento.", porque as 3 perguntas usam ferramenta (até a hora); a resposta começa depois de 3,2 a 3,6 s;
  - as respostas faladas saíram limpas ("20 e 16", "31 graus"). Na do Moodle, ele ofereceu a lista em vez de ler 9 itens.
- **Google:** o app ainda está em "Testando", e o login vence em 7 dias. Para publicar, falta a página inicial e a política de privacidade; as páginas estão prontas em `docs/google-paginas/` para o GitHub Pages.
- **ESP32 (Fase 3):** firmware novo em `esp32/jarvis/` (27/09). Testado no PC (26 testes da lógica e o código de rede contra a ponte com dublês, em 8 cenários) e na placa: em 27/09, o usuário relatou que funciona. Ainda sem medição dos tempos pela placa. Guia em `esp32/LEIA-ME.md`.

## Hardware e ambiente
- **Servidor:** Ubuntu Server, tudo em Docker (Portainer), RTX 2060 (6 GB) e 16 GB de RAM. Projeto em `/opt/jarvis`.
- **Já existentes no servidor:**
  - Open WebUI, SearXNG, Jellyfin (NVIDIA), Pi-hole e Beszel;
  - o stack antigo `wisper` (contêineres `whisper` e `piper`).
- **Versões:** Ollama 0.34.0, Hermes Agent v0.21.5 (tag `v2026.9.24`), driver NVIDIA 580.178.04.
- **Arquivos do compose** (`COMPOSE_FILE` no `.env`): `docker-compose.yml:docker-compose.tools.yml`, mais `:docker-compose.searxng.yml`, `:docker-compose.voz.yml` e, no futuro, `:docker-compose.honcho.yml`.
- **Cidade padrão:** Cornélio Procópio, PR (`JARVIS_CIDADE`, entre aspas).
- **Moodle:** https://moodle.utfpr.edu.br.

## Decisões
1. O ESP32 é só ouvido e boca; toda a lógica fica no servidor. A ponte é própria (o xiaozhi é o plano B).
2. **Hermes:** a ponte fala com a API `/v1/chat/completions` em streaming.
   - "Um momento." sai no primeiro `hermes.tool.progress`, se nada foi dito ainda.
   - O cabeçalho `X-Hermes-Session-Key` por sala está pronto, mas desligado (`VOZ_SESSAO_HERMES`); entra junto com o Honcho.
3. **Ativação:** por botão (Enter no PC, botão no ESP32). A palavra de ativação e o VAD ficam para a Fase 4. Falar por cima já interrompe a resposta.
4. **Voz frase a frase:** a primeira frase sai sozinha; as seguintes são juntadas até uns 60 caracteres.
5. **GPU só para o modelo:** Whisper, Piper, palavra de ativação e embeddings rodam na CPU. `JARVIS_NUM_GPU=99`, deixando uns 400 MB livres para o Jellyfin.
6. **Modelo:** `qwen3.5:4b`, com o plano B `qwen3.5:9b`.
   - Em 27/09, o usuário pediu um modelo mais inteligente. Nenhum maior cabe 100% na GPU de 6 GB com 64K: o `qwen3.5:9b` e o `gemma4:12b` (7,2 GB só o arquivo) iriam em parte para a CPU, e o `gemma4:e4b` (6,1 a 9,6 GB) tem a mesma faixa de inteligência do 4b. Decisão: manter o 4b e atacar os erros no acabamento da Fase 1. Com uma placa de 12 GB, comparar o `qwen3.5:9b` e o `gemma4:12b`.
   - Troca com `scripts/modelo.sh`.
   - Amostragem pelo `.env`: temperatura 0,5, top_p 0,9, top_k 20, presence_penalty 0,3. O padrão do qwen3.5 (1 / 0,95 / 20 / 1,5) inventava mais.
7. **Busca local:** SearXNG via `jarvis-tools`, com `web.keyless_fallback false`.
8. **Skills travadas:** `skills.write_approval true`, `skills.guard_agent_created true`, `auxiliary.background_review.enabled false` e `curator.enabled false`. O toolset `skills` fica fora da `api_server`.
9. **Honcho** (self-hosted v3.2.1, `docs/honcho.md`):
   - usa o `jarvis-qwen` na GPU e o bge-m3 na CPU;
   - custa uns 1,5 s a mais por pergunta, porque o prompt é relido;
   - plano B: o `jarvis-qwen-cpu`.
10. **Fatos do Hermes v0.21.5:**
    - `agent.reasoning_effort` precisa ser `false`, porque `none` grava vazio;
    - o prompt só leva a data, então o cache sobrevive à troca de minuto;
    - o contexto mínimo é de 64K;
    - as ferramentas MCP aparecem como `mcp__<servidor>__<ferramenta>`;
    - os cabeçalhos MCP aceitam `${VAR}` do `data/hermes/.env`;
    - o cliente MCP usa `mcp==2.0.0`;
    - com provedor custom, a temperatura vem do Modelfile;
    - o SSE manda `: keepalive` a cada 10 s e o raciocínio em `reasoning_content`.
11. **`jarvis-tools`** (servidor MCP próprio):
    - **Isolamento:** sem portas publicadas; `read_only`, `cap_drop ALL`; token Bearer; segredos só em `data/jarvis-tools`.
    - **Chamadas:** uma linha de log por chamada, sem tokens; orçamento de 50 s por chamada.
    - **Ferramentas:**
      - hora e clima;
      - Moodle: prazos, provas, disciplinas, conteúdo e atividade, com nomes limpos e siglas (TCC);
      - Google: agenda, criar evento, e-mails e ler e-mail;
      - web: buscar (lê as 3 melhores páginas) e ler página (só links de uma busca recente, com proteção SSRF).
    - 162 testes de unidade.
12. **Segurança:** textos de terceiros são informação, nunca ordem. Criar evento exige confirmação. Não apaga nem instala nada. A API não tem terminal.
13. **Vetor de teste** (`scripts/testar-jarvis.py`, 30 casos): 3 s até a 1ª palavra sem ferramenta, 10 s com ferramenta e 20 s na busca. O relatório leva o nome do modelo.
14. **Ponte de voz `jarvis-voz`:**
    - **Protocolo:** WebSocket em `/voz?token=&sala=`. A entrada é PCM de 16 kHz, mono e 16 bits.
    - **Eventos:** estados, transcrição, ferramenta, frase, `audio_inicio` e `audio_fim` em pares, e `fim` sempre.
    - **Saída para o ESP32:** o firmware usa o PCM5102 em `s16le`, sem `config`: o áudio vem na taxa do Piper (22050 Hz) e não passa pela reamostragem linear da ponte. O `u8` fica para quem usar o DAC interno.
    - **Envio do áudio:** no ritmo da reprodução, com 2 s de folga.
    - **Trava do Ollama:** só enquanto o Hermes escreve. Um cliente parado por 15 s é derrubado.
    - **Recusas:** 401, 400 e 429. Histórico por sala: 8 mensagens ou 5 min.
    - **Rede:** `JARVIS_VOZ_IP` limita a placa de rede, porque o Docker passa por fora do `ufw`.
    - **Meta:** 1º áudio em até 2 s nas perguntas simples e 4 s com ferramenta.
15. **Firmware do ESP32** (`esp32/jarvis/`, PlatformIO com o core Arduino 3.x do pioarduino):
    - **Hardware:** ESP32 clássico, INMP441 no I2S0, PCM5102 no I2S1, BOOT (GPIO0) e o LED da placa (GPIO2).
    - **Botão:** segurar para falar; um toque de menos de 300 ms só manda parar (`inicio` + `cancelar`). Apertar durante a resposta cala na hora e já grava.
    - **WebSocket próprio** (`lib/logica/websocket.*`), sem biblioteca: o leitor entrega o áudio em pedaços, direto para um anel de 48 KB (~1,1 s), e só lê do TCP o que cabe. O resto da folga de 2 s da ponte espera no TCP.
    - **Alto-falante:** tarefa própria, que junta 100 ms antes de tocar e desliga o DAC entre as falas. O DMA toca zeros sozinho nas pausas (`auto_clear`).
    - **Conexão:** token no cabeçalho `Authorization`, nunca na URL. Reconecta sozinho (1 a 15 s; 60 s depois de um token recusado), manda `ping` a cada 25 s parado e desiste da ponte depois de 70 s sem receber nada.
    - **Memória:** o anel fica no heap; na memória estática, o WiFi ficaria sem espaço (DRAM estática em 52%).

## Medições

| Medida | Fase 0 | Passo 1 (24/09) | Passo 2a (24/09) | Passo 2 (25/09) |
|---|---|---|---|---|
| Prompt fixo por pergunta | 13.302 tokens | 3.900 | 2.855 | 3.818 (~7,8 mil com ferramentas) |
| Frio, 1º token | 12,5 s | 7,6 s | 5,9 s | 5,8 s |
| Quente, 1ª palavra | 0,9 s | 1,0 s | 0,2 s | 0,2 s |
| "Que horas são?" | 3,6 s | 3,2 s | 1,0 s | 1,1 s |

No vetor de teste de 25/09 às 19h06 (13 ferramentas, ~9,5 mil tokens com ferramenta), o tempo até a 1ª palavra foi:
- clima: 1,3 a 3,4 s;
- agenda: cerca de 2,3 s;
- Moodle: 1,2 a 5,3 s;
- e-mail: 3,4 a 7,5 s;
- busca: 3,2 a 3,8 s;
- sem ferramenta: 0,5 a 1,5 s.

Na GPU, 5462 de 6144 MiB estão em uso. A geração faz ~64 tokens/s e a leitura do prompt, ~1.750 tokens/s. Há um processo python3 com 188 MiB na GPU que ainda não foi identificado.

## Pendências
- **ESP32:** medir pelo Serial (`[tempos]`) e conferir o ganho do microfone com o `/mic`.
- **Fase 2:**
  - o `voz-teste.sh` medir o 1º áudio da resposta separado do "Um momento.";
  - ajustar o Whisper (small ou medium) e a voz do Piper.
- **Acabamento da Fase 1:** rodar `./scripts/fase1-jarvis-tools.sh` e o `testar-jarvis.py` no servidor, ler as respostas, e comparar a temperatura 0,5 com 0,3.
- **Honcho**, por último.
- Publicar o app Google.
- **Fase 4:** palavra de ativação, rotinas, barge-in, Home Assistant e Beszel.

## Perguntas da lista do usuário → casos
1. Clima (`clima-hoje`, `clima-londrina`)
2. Agenda de hoje (`agenda-hoje`)
3. Atividades da semana (`moodle-semana`)
4. Provas (`moodle-provas`)
5. Vencimentos para amanhã (`moodle-amanha`)
6. Webhook (`webhook`)
7. Criar uma skill (`skill-dolar`)
8. Estado dos servidores (`servidores`)
9. E-mails importantes (`email-hoje`)
10. Busca (`busca-ubuntu`)

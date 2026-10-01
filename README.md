# Jarvis

Assistente de voz pessoal que roda inteiro no servidor de casa, sem nenhuma API de nuvem para o modelo. Um ESP32 (ou o PC) serve de ouvido e boca; o servidor transcreve a fala, consulta as ferramentas (faculdade, agenda, e-mail, clima, busca) e responde em voz alta, frase a frase.

```
PC ou ESP32 ──WebSocket 10800 (token)──> jarvis-voz ──> Whisper (fala -> texto, CPU)
                                              │──> Hermes Agent (API /v1/chat/completions, SSE)
                                              │       │──> Ollama: jarvis-qwen (qwen3.5:4b, 64K, 100% GPU)
                                              │       └──> jarvis-tools (servidor MCP próprio)
     <── áudio da resposta, frase a frase ────└──> Piper (texto -> fala, CPU)
```

Tudo em português do Brasil: código, comentários, mensagens e documentação.

## Situação

| Fase | O que é | Situação |
|---|---|---|
| 0. Fundação | Ollama, Hermes Agent, Whisper, Piper | pronta e medida (`docs/fase0.md`) |
| 1. Cérebro em texto | prompt enxuto e ferramentas próprias | pronta: 28 de 30 casos e 8 de 10 perguntas da lista do dono (`docs/fase1.md`) |
| 2. Voz no servidor | ponte de voz e cliente no PC | instalada e funcionando; a meta de tempo ainda não foi atingida (`docs/fase2.md`) |
| 3. ESP32 | firmware com botão, microfone e DAC | funciona na placa; a correção da voz picotada ainda não foi conferida na placa (`esp32/LEIA-ME.md`) |
| Agenda recorrente | criar, alterar, apagar e desfazer séries | pronta; com o modelo de 4B, mudar uma série ainda falha às vezes |
| Honcho | memória de longo prazo | preparado, ainda não ligado (`docs/honcho.md`) |
| 4. Jarvis de verdade | palavra de ativação, rotinas, interrupção por voz | a fazer |

O histórico de decisões e medições está em `docs/plano.md`.

## O que ele faz

- **Faculdade (Moodle):** prazos, provas, disciplinas, o conteúdo de uma disciplina e os detalhes de uma atividade (se já foi entregue, nota).
- **Google Agenda:** lê os compromissos de várias contas; cria, altera e apaga eventos, inclusive os que se repetem, e desfaz a última mudança.
- **Gmail:** lista e lê e-mails de várias contas (só leitura).
- **Clima e hora:** previsão do Open-Meteo para a cidade padrão ou outra.
- **Busca na web:** pelo SearXNG da casa, lendo as páginas e citando a fonte.
- **Conhecimento geral:** responde direto, sem busca.
- **Voz:** botão para falar, resposta frase a frase, e falar por cima interrompe.

## Hardware

- **Servidor:** Ubuntu Server com Docker, uma GPU de 6 GB (RTX 2060) e 16 GB de RAM. A GPU fica só para o modelo; Whisper, Piper e embeddings rodam na CPU.
- **Placa:** ESP32 clássico (ESP32-WROOM DevKit), microfone I2S INMP441 e DAC I2S PCM5102. As ligações estão em `esp32/LEIA-ME.md`.
- **Já existente na casa:** um SearXNG, usado pela busca.

## Como funciona

### Cérebro: Hermes Agent e Ollama

O [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.21.5 conversa com o Ollama, que serve o `jarvis-qwen`: o `qwen3.5:4b` com 64 mil tokens de contexto, 100% na GPU. A personalidade e as regras ficam em `hermes/SOUL.md`.

- **Prompt enxuto:** o prompt fixo caiu de 13.302 para cerca de 3.800 tokens, e "que horas são?" de 3,6 s para 1,1 s (`scripts/fase1-enxugar.sh`).
- **Raciocínio desligado:** `agent.reasoning_effort` precisa ser `false`; `none` grava vazio e religa.
- **Amostragem:** temperatura 0,5, top_p 0,9, top_k 20, pelo `.env` e por `scripts/modelo.sh`.
- **Skills travadas:** o Jarvis não cria nem altera skills; só explica.

### Ferramentas: `jarvis-tools` (servidor MCP)

Um contêiner próprio em Python 3.12 com o SDK `mcp`. O Hermes fala com ele pela rede interna do Docker, com um token; nenhuma porta é publicada. Os tokens do Moodle e do Google ficam só em `data/jarvis-tools`: o Hermes e o modelo nunca os veem.

| Ferramenta | O que faz | Precisa de |
|---|---|---|
| `hora` | data e hora de Brasília | nada |
| `clima` | previsão do tempo: hoje, amanhã, um dia, fim de semana ou semana | nada |
| `moodle_prazos` | atividades pendentes, por período e por disciplina | login no Moodle |
| `moodle_provas` | provas e questionários dos próximos 30 dias | login no Moodle |
| `moodle_disciplinas` | disciplinas em andamento | login no Moodle |
| `moodle_conteudo` | uma disciplina: professores, avisos, prazos, materiais; ou um assunto dentro dela | login no Moodle |
| `moodle_atividade` | uma atividade: descrição, prazo, se foi entregue, nota | login no Moodle |
| `agenda` | compromissos de uma ou de todas as contas | login no Google |
| `agenda_criar` | propõe um evento, que pode se repetir ("toda terça até 15/12") | login no Google |
| `agenda_alterar` | propõe mudar título, dia, hora ou duração | login no Google |
| `agenda_apagar` | propõe apagar um evento | login no Google |
| `agenda_desfazer` | propõe desfazer a última mudança (até 24 horas) | login no Google |
| `agenda_confirmar` | faz o que foi proposto, depois do "sim" do usuário | login no Google |
| `emails` | lista os e-mails, dos mais novos para os mais antigos | login no Google |
| `ler_email` | lê um e-mail inteiro, pelo id ou por remetente e assunto | login no Google |
| `buscar` | pesquisa no SearXNG e lê as 3 melhores páginas | SearXNG |
| `ler_pagina` | lê mais de uma página que apareceu numa busca | SearXNG |

As ferramentas de Moodle, Google e busca só são registradas quando a integração está configurada, para o modelo não ver o que não funcionaria. Cada ferramenta pode ser testada sem o modelo:

```bash
docker compose exec -T jarvis-tools python -m app.cli moodle_prazos semana
```

**Saída pensada para um modelo pequeno e para a voz.** O texto de cada ferramenta é simples, curto, uma coisa por linha, com as datas já por extenso ("sábado, 3 de outubro"). As listas terminam com uma linha "Ao responder: ..." dizendo como falar, porque o modelo de 4B segue melhor o que leu por último. Períodos ("próximas semanas", "fim de semana") e repetições ("toda segunda e quarta") são interpretados pela ferramenta, nunca pelo modelo.

### Moodle em detalhe

O Jarvis fala com o Moodle da faculdade do mesmo jeito que o aplicativo oficial do celular: pela API REST do serviço `moodle_mobile_app`. Não raspa páginas nem precisa de permissão de administrador; basta a faculdade ter o acesso pelo aplicativo ligado. O código está em `jarvis-tools/app/moodle.py` e `jarvis-tools/app/moodle_login.py`.

**Login e token**

```bash
docker compose exec -it jarvis-tools python -m app.moodle_login            # conecta
docker compose exec -it jarvis-tools python -m app.moodle_login --remover  # desconecta
```

- O comando pede usuário e senha uma vez e troca por um token em `/login/token.php`. **A senha não é guardada.**
- O token fica em `data/jarvis-tools/moodle.json`, com permissão 0600, fora do git. Só o contêiner `jarvis-tools` lê esse arquivo; o Hermes e o modelo nunca veem o token.
- O endereço vem de `MOODLE_URL` no `.env`. Se não for `https://`, o login avisa que a senha iria sem criptografia e pede confirmação.
- As chamadas vão por POST, para o token não aparecer em URLs nem nos logs de acesso do Moodle.
- Se a faculdade usa login único (SSO), esse método pode não funcionar.
- Quando o token vence, as ferramentas respondem "o acesso ao Moodle expirou; rode moodle_login de novo".

**As cinco ferramentas**

| Ferramenta | Parâmetros | O que devolve | Funções do Moodle |
|---|---|---|---|
| `moodle_prazos` | `quando` (hoje, amanhã, um dia, uma data, semana, próximas semanas, mês, atrasadas) e `disciplina` | atividades pendentes, uma por linha | `core_calendar_get_action_events_by_timesort`, `core_calendar_get_action_events_by_course` |
| `moodle_provas` | `disciplina` | questionários e eventos com nome de prova nos próximos 30 dias | as duas acima, `core_calendar_get_calendar_events`, `core_calendar_get_calendar_upcoming_view` |
| `moodle_disciplinas` | nenhum | as disciplinas em andamento | `core_course_get_enrolled_courses_by_timeline_classification` |
| `moodle_conteudo` | `disciplina` e `assunto` | visão geral da disciplina, ou os trechos que falam do assunto | `core_course_get_contents`, `core_course_get_courses_by_field`, `mod_forum_*`, `mod_page_get_pages_by_courses`, `mod_assign_get_assignments`, `mod_quiz_get_quizzes_by_courses` |
| `moodle_atividade` | `atividade` e `disciplina` | datas, descrição do professor, se foi entregue, nota e tentativas | `mod_assign_get_submission_status`, `mod_quiz_get_user_attempts`, `mod_quiz_get_user_best_grade` e as de conteúdo |

- **`moodle_prazos`** usa os "eventos de ação" do Moodle, que já excluem o que foi entregue. Com `atrasadas`, olha os últimos 30 dias. O lembrete de "conclusão esperada" sai da lista quando a mesma atividade já tem um prazo de verdade.
- **`moodle_provas`** junta duas fontes: os questionários e os eventos do calendário cujo nome parece prova (prova, avaliação, exame, P1, recuperação, segunda chamada). A abertura e o fechamento do mesmo questionário viram uma linha só ("abre terça às 19h e fecha às 21h"). A resposta lembra que provas combinadas só em sala podem não estar no Moodle.
- **`moodle_conteudo`** sem assunto dá a visão geral: docentes, os 3 avisos mais recentes, prazos dos próximos 30 dias e as seções com suas atividades. Com assunto ("data da defesa"), procura nas seções, páginas, descrições de tarefas e questionários e nos avisos, e devolve até 6 trechos.
- **`moodle_atividade`** acha a atividade pelo nome, na disciplina pedida ou em todas. Para uma tarefa, diz quando abre, o prazo, o limite para entrega atrasada, se foi enviada e a nota. Para um questionário, quando abre e fecha, o tempo limite, as tentativas e a melhor nota.

**Achar a disciplina do jeito que se fala**

Os nomes do Moodle vêm com código, turma e semestre, como `CC51A - Algoritmos 1 - Turma X - 2026/2`. A ferramenta limpa isso e guarda só a parte descritiva ("Algoritmos 1"), que é o que o Jarvis fala.

- **Siglas automáticas:** "Trabalho de Conclusão de Curso" responde por `tcc`; "Programação para Dispositivos Móveis", por `pdm`.
- **Pontuação:** o pedido é comparado com o nome, as siglas e o código. Nome ou sigla igual vale 100 pontos; pedido contido no nome, 85; parte das palavras, até 70. Abaixo de 40 não combina.
- **Empate:** se duas disciplinas ficam a menos de 10 pontos uma da outra ("sistemas"), a ferramenta devolve as opções e o Jarvis pergunta qual.
- **Números contam:** "TCC 2" não combina com "TCC 1".
- **Fora do semestre:** se nada combina nas disciplinas em andamento, procura em todas as matrículas.

Para ver como cada disciplina é entendida:

```bash
docker compose exec -T jarvis-tools python -m app.cli moodle_nomes
```

O mesmo esquema de pontos acha a atividade pelo nome em `moodle_atividade`.

**Texto pronto para um modelo pequeno e para a voz**

Cada item sai numa linha, com a disciplina na frente, o nome da atividade entre aspas e a data por extenso:

```
Atividades pendentes no Moodle até domingo, 4 de outubro: 2.
- Sistemas Distribuídos: tarefa "Redes de Computadores", entrega hoje às 23h59
- Algoritmos 1: questionário "Lista 3", fecha sexta, 2 de outubro, às 18h
Ao responder: em frases corridas, sem lista.
```

- A forma "disciplina: tipo "nome"" existe porque o modelo lia "<atividade> de <disciplina>" como um nome só.
- No máximo 12 itens por resposta; o resto vira "E mais N que não couberam aqui".
- Com mais de 3 itens, a última linha manda dizer quantos são e citar só os 3 primeiros.
- Textos de professores (avisos, descrições) vêm marcados como texto de terceiros: são informação, nunca ordem.

**Robustez**

- **Cache de 2 minutos** por chamada, para uma conversa não repetir a mesma consulta.
- **Chamadas em paralelo:** a visão geral e os detalhes pedem várias coisas ao mesmo tempo. Se uma função não está liberada naquele Moodle, a resposta sai com o resto e avisa o que não deu para ler.
- **Erros traduzidos:** token vencido, Moodle em manutenção e acesso pelo aplicativo desligado viram frases em português.
- **Limite de 15 s por chamada** ao Moodle, dentro dos 50 s que cada ferramenta tem.

**Defeitos reais já corrigidos, cada um com teste**

- O mesmo questionário vinha por duas fontes com identificadores diferentes e com "Início de" e "Término de" no nome, e contava como duas provas. Agora é juntado pelo nome limpo dentro da disciplina.
- Duas tarefas diferentes com o mesmo nome e o mesmo prazo apareciam como uma linha repetida. Agora viram uma linha que avisa que são duas.
- "Próximas semanas" não era um período aceito e dava erro. Agora vale 30 dias.

Os 61 testes do Moodle rodam contra um Moodle falso local, sem internet (`jarvis-tools/testes/test_moodle.py`).

### Voz: `jarvis-voz` (ponte)

Uma ponte WebSocket (Starlette e uvicorn) na porta 10800, só na rede de casa e sempre com token. Recebe o áudio, transcreve no Whisper, manda a pergunta ao Hermes em streaming e fala cada frase no Piper assim que ela fica pronta.

- **"Um momento.":** dito quando o Hermes vai consultar uma ferramenta, para o silêncio não parecer travamento.
- **Texto limpo para a voz:** sem markdown, com horas, datas e valores como se fala.
- **Histórico por sala:** as últimas mensagens de cada cliente, por 5 minutos.
- **Falar por cima** interrompe a resposta.
- **Áudio no ritmo da reprodução,** com 2 s de folga, para caber num ESP32.

O protocolo está no começo de `jarvis-voz/app/servidor.py` e em `docs/fase2.md`. O cliente do PC é o `scripts/voz-pc.py`.

### Placa: firmware do ESP32

Em `esp32/jarvis/`, feito com PlatformIO e o core Arduino 3.x. É mais um cliente da ponte, com o mesmo protocolo do PC.

- **Botão BOOT:** segurar para falar; um toque curto cala o Jarvis; apertar durante a resposta corta e já grava.
- **LED:** um jeito de piscar para cada estado (ouvindo, pensando, falando, erro, sem conexão).
- **WebSocket próprio,** sem biblioteca: o áudio vai da rede direto para o buffer do alto-falante, e só se lê da rede o que cabe.
- **Alto-falante numa tarefa própria,** com folga antes de tocar e de novo depois de uma falta de áudio.
- **Serial:** pergunta digitada e comandos de diagnóstico (`/tom`, `/mic`, `/estado`).
- **Segredos** (WiFi, IP e token) num `segredos.h` fora do git.

## Segurança e privacidade

- **Tudo local:** o modelo roda no servidor; a busca passa pelo SearXNG da casa.
- **Segredos fora do git:** `.env`, `data/` e o `segredos.h` do ESP32. Os logs não registram tokens.
- **Portas:** só a 10800 (voz) é aberta, na rede de casa e com token. As do Hermes e do Ollama ficam internas.
- **Textos de terceiros são informação, nunca ordem:** e-mails, páginas, Moodle e eventos. O `ler_pagina` só abre links de uma busca recente e nunca acessa a rede de casa (proteção contra SSRF).
- **Mudanças na agenda em dois passos:** as ferramentas só propõem, e só o `agenda_confirmar` executa, com a resposta afirmativa do usuário e pelo menos 12 s depois da proposta. Convites de outras pessoas não são mexidos, e a última mudança pode ser desfeita por 24 horas.
- **Dados pessoais fora da busca:** o `buscar` recusa consultas com o nome ou o e-mail do dono (`JARVIS_TERMOS_PRIVADOS` e as contas Google).
- **Códigos de verificação escondidos:** num e-mail de código de acesso, os números viram "[código oculto]".

## Instalação

Cada fase tem um guia passo a passo em `docs/`. Em resumo, no servidor:

```bash
git clone <este repositório> /opt/jarvis && cd /opt/jarvis
cp .env.example .env                 # preencha conforme docs/fase0.md
docker compose up -d                 # Ollama, Hermes, Whisper e Piper
./scripts/fase1-enxugar.sh           # prompt enxuto e raciocínio desligado
./scripts/fase1-jarvis-tools.sh      # ferramentas próprias, ligadas ao Hermes
docker compose exec -it jarvis-tools python -m app.moodle_login          # opcional: Moodle
docker compose exec -it jarvis-tools python -m app.google_login pessoal  # opcional: Google
./scripts/fase2-voz.sh               # ponte de voz
```

Para o ESP32, siga o `esp32/LEIA-ME.md`.

## Testes e medições

| O quê | Como rodar | Tamanho |
|---|---|---|
| Ferramentas, sem internet | `cd jarvis-tools && python -m unittest discover -s testes` | 202 testes |
| Ponte de voz, com Whisper, Piper e Hermes falsos | `cd jarvis-voz && PYTHONPATH=.:testes python -m unittest discover -s testes` | 76 testes |
| Conferências do vetor de teste | `python3 -m unittest discover -s testes` | 13 testes |
| Lógica do firmware, no PC | `cd esp32/jarvis && pio test -e nativo` | 30 testes |
| Rede do firmware contra a ponte, com dublês | `cd esp32/jarvis && ./teste-ponte/rodar.sh` | 8 cenários |
| Vetor de teste com o modelo de verdade | `python3 scripts/testar-jarvis.py` | 34 casos |
| Voz de ponta a ponta, sem microfone | `./scripts/voz-teste.sh` | 3 perguntas |

O vetor de teste (`testes/jarvis-casos.json`) faz perguntas reais pela API e confere a resposta. Em todo turno, reprovam:

- markdown na resposta;
- dia da semana que não bate com a data;
- oferta de algo que nenhuma ferramenta faz;
- afirmar que criou um evento sem confirmação.

O relatório com todas as respostas vai para `medicoes/`. Ele confere a forma; se as provas e os compromissos estão certos, só o dono sabe lendo as respostas.

Medições no servidor (setembro de 2026):

| Medida | Valor |
|---|---|
| Prompt fixo por pergunta | cerca de 3.800 tokens (eram 13.302) |
| Pergunta sem ferramenta, até a 1ª palavra | 0,5 s |
| Pergunta com uma ferramenta, até a 1ª palavra | 1 a 5 s |
| Whisper (CPU) | 1,3 a 2,0 s |
| Voz: 1º áudio | 2,2 a 3,5 s, e esse áudio é o "Um momento." (meta: 2 s nas simples, 4 s com ferramenta) |
| GPU | 5,4 de 6 GB, cerca de 64 tokens/s |

## O que foi feito, em ordem

1. **Fase 0:** Ollama com o modelo inteiro na GPU, Hermes Agent, Whisper e Piper no Compose; skills travadas.
2. **Fase 1:** prompt enxuto; `jarvis-tools` com hora, clima, Moodle, Google Agenda, Gmail e busca; vetor de teste com as 10 perguntas do dono.
3. **Fase 2:** ponte `jarvis-voz`, teste sem microfone e cliente do PC.
4. **Fase 3:** firmware do ESP32 com WebSocket próprio, testado no PC contra a ponte real com dublês.
5. **Acabamento da Fase 1:** os relatórios aprovavam respostas erradas, então:
   - "fim de semana" num domingo passou a ser o próximo, e "próximas semanas" virou 30 dias (antes dava erro);
   - o mesmo questionário do Moodle vinha duas vezes, com ids diferentes nas ações e no calendário, e contava como duas provas;
   - o `ler_email` recebia ids inventados pelo modelo e abria um e-mail qualquer;
   - a busca levou o nome do dono para a web, o que virou a trava de privacidade;
   - um código de acesso foi lido em voz alta, o que virou a ocultação de códigos;
   - o vetor de teste ficou mais exigente.
6. **Agenda recorrente:** criar, alterar e apagar séries, com os alcances "só esta", "esta e as próximas" e "todas".
7. **Confirmação da agenda:** o teste contra a agenda real mostrou que a confirmação por repetição da mesma chamada era burlada pelo próprio modelo. Daí veio o desenho atual, em que as ferramentas só propõem e o `agenda_confirmar` executa.
8. **Voz picotada no ESP32:** folga antes de tocar e depois de cada falta de áudio, buffer de até 2 s e contagem de engasgos no Serial.

## Limites conhecidos

- **Modelo de 4B:** é o maior que cabe inteiro na GPU de 6 GB com 64 mil tokens de contexto. Ele ainda inventa de vez em quando (um compromisso que não consultou, um nome copiado errado) e às vezes entende errado um pedido com vários dados, como a data de início de uma série. As defesas estão nas ferramentas: confirmação com a data por extenso, travas e instruções coladas no resultado.
- **Tempo da voz:** o Whisper na CPU leva de 1,3 a 2,0 s, e quase toda pergunta usa uma ferramenta. A meta de 2 s para o primeiro áudio não foi atingida.
- **Ativação por botão:** a palavra de ativação fica para a Fase 4.
- **Memória de longo prazo:** o Honcho está preparado, mas desligado.

## Estrutura

```
jarvis/
├── docker-compose.yml           Ollama, Hermes, Whisper, Piper
├── docker-compose.tools.yml     jarvis-tools (servidor MCP)
├── docker-compose.searxng.yml   liga o jarvis-tools à rede do SearXNG
├── docker-compose.voz.yml       jarvis-voz (ponte de voz, porta 10800)
├── docker-compose.honcho.yml    Honcho (memória de longo prazo), opcional
├── .env.example                 configurações e segredos (copiar para .env)
├── hermes/SOUL.md               personalidade e regras do Jarvis
├── ollama/init.sh               cria o jarvis-qwen (64K, GPU) e o jarvis-embed (CPU)
├── jarvis-tools/
│   ├── app/                     ferramentas: moodle, agenda, agenda_mudancas, recorrencia, emails, busca, clima...
│   └── testes/                  testes de unidade, com Moodle, Google e SearXNG falsos
├── jarvis-voz/
│   ├── app/                     ponte: servidor, turno, voz, textos, sessoes
│   └── testes/                  testes com Whisper, Piper e Hermes falsos
├── esp32/
│   ├── LEIA-ME.md               ligações, gravação, uso e problemas comuns
│   └── jarvis/                  firmware (PlatformIO): src/, lib/logica/, test/, teste-ponte/
├── scripts/
│   ├── fase1-enxugar.sh         enxuga o prompt da API e desliga o raciocínio
│   ├── fase1-jarvis-tools.sh    (re)instala o jarvis-tools e liga ao Hermes
│   ├── fase2-voz.sh             (re)instala a ponte de voz e testa
│   ├── modelo.sh                troca o modelo e ajusta a amostragem
│   ├── testar-jarvis.py         vetor de teste em texto
│   ├── voz-teste.sh             teste de voz sem microfone
│   ├── voz-pc.py                cliente de voz para o PC
│   ├── chat.py                  conversa pelo terminal
│   └── medicoes.py              mede tudo e gera o relatório
├── testes/
│   ├── jarvis-casos.json        perguntas do vetor de teste e o que conferir
│   └── test_conferencias.py     testes das conferências do vetor
├── docs/                        guias das fases, do Honcho e o plano com as decisões
├── data/                        criado na execução; fora do git (tokens, memória, modelos)
└── medicoes/                    relatórios; fora do git
```

## Comandos do dia a dia

```bash
cd /opt/jarvis
docker compose ps                         # status
docker compose logs -f jarvis-tools       # uma linha por chamada de ferramenta
docker compose logs -f jarvis-voz         # uma linha por pergunta falada, com os tempos
python3 scripts/chat.py                   # conversa pelo terminal
python3 scripts/testar-jarvis.py          # vetor de teste (--listar mostra os casos)
./scripts/voz-teste.sh                    # mede a voz de ponta a ponta
./scripts/modelo.sh                       # modelo atual; "usar <modelo>", "ajustar temperatura 0.3"
./scripts/fase1-jarvis-tools.sh           # aplica mudanças nas ferramentas e no SOUL
docker compose exec -T jarvis-tools python -m app.cli recursos   # o que está ligado
```

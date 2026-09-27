# Jarvis

Assistente de voz local: ESP32 como ouvido e boca, Hermes Agent como cérebro, tudo rodando no servidor de casa.

## Estrutura

```
jarvis/
├── docker-compose.yml         serviços principais: Ollama, Hermes, Whisper, Piper, openWakeWord
├── docker-compose.tools.yml   jarvis-tools: ferramentas próprias do Jarvis (servidor MCP)
├── docker-compose.searxng.yml liga o jarvis-tools à rede do seu SearXNG (busca na web)
├── docker-compose.voz.yml     jarvis-voz: ponte de voz (Fase 2), porta 10800 na rede de casa
├── docker-compose.honcho.yml  Honcho (memória de longo prazo), opcional
├── .env.example               configurações e segredos (copiar para .env)
├── jarvis-tools/              ferramentas: hora, clima, Moodle, Google Agenda, Gmail e busca na web
│   ├── app/                   código (python -m app.cli testa cada ferramenta direto)
│   └── testes/                testes de unidade, sem internet
├── jarvis-voz/                ponte de voz: WebSocket -> Whisper -> Hermes -> Piper, frase a frase
│   ├── app/                   código (python -m app.teste mede a voz de ponta a ponta)
│   └── testes/                testes com Whisper, Piper e Hermes falsos
├── ollama/init.sh             cria o jarvis-qwen (64K, 100% GPU, temperatura do .env) e o jarvis-embed (Honcho, CPU)
├── hermes/SOUL.md             personalidade e regras do Jarvis (copiada para data/hermes/)
├── honcho/honcho.env.example  configuração do Honcho (copiar para honcho/honcho.env)
├── scripts/pergunta.sh        testa o Hermes pela API
├── scripts/chat.py            conversa com o Jarvis pelo terminal, mostrando as ferramentas usadas
├── scripts/testar-jarvis.py   vetor de teste: faz as perguntas de testes/jarvis-casos.json e confere
├── scripts/jarvis_cliente.py  código comum do chat.py e do testar-jarvis.py
├── scripts/modelo.sh          troca o modelo de IA e ajusta temperatura e outros parâmetros
├── scripts/honcho-ajustes.sh  ajusta o Honcho para o modelo pequeno e para a voz
├── scripts/medicoes.py        mede tudo de uma vez e gera o relatório em medicoes/
├── scripts/fase1-enxugar.sh   Fase 1, passo 1: enxuga o prompt da API e desliga o raciocínio
├── scripts/fase1-jarvis-tools.sh  Fase 1, passo 2: sobe o jarvis-tools e liga ao Hermes
├── scripts/memoria-remover.py apaga anotações da memória do Hermes
├── scripts/fase2-voz.sh       Fase 2: sobe a ponte de voz e testa
├── scripts/voz-teste.sh       teste de voz sem microfone (relatório em medicoes/)
├── scripts/voz-pc.py          cliente de voz para o PC (roda no PC: pip install websockets sounddevice)
├── testes/jarvis-casos.json   perguntas do vetor de teste e o que conferir em cada uma
├── docs/fase0.md              guia da Fase 0
├── docs/fase1.md              guia da Fase 1
├── docs/fase2.md              guia da Fase 2 (voz)
├── docs/plano.md              plano, decisões e medições
├── CLAUDE.md                  contexto e regras para o Claude Code
├── esp32/                     Fase 3 (por enquanto, só um rascunho antigo de referência)
├── docs/google-paginas/       página inicial e política de privacidade para publicar o app do Google
├── docs/honcho.md             guia do Honcho
├── honcho-src/                código do Honcho (baixado no passo 1 de docs/honcho.md)
├── data/                      criado na execução: dados do Hermes, Whisper, Piper e wake word
│   └── jarvis-tools/          tokens do Moodle e das contas Google (só o jarvis-tools lê)
└── medicoes/                  relatórios do medicoes.py e do testar-jarvis.py
```

## Fases

| Fase | Situação |
|---|---|
| 0. Fundação: Ollama, Hermes e serviços de voz | concluída; ajustes de GPU e de skills em `docs/fase0.md` |
| Extra: Honcho (memória de longo prazo) | depois do acabamento da Fase 1; ver `docs/honcho.md` |
| 1. Cérebro em texto: prompt enxuto, hora, clima, Moodle, agenda, e-mail, busca | meta atingida (10 de 10); acabamento depois da Fase 2: `docs/fase1.md` |
| 2. Voz no servidor: ponte e cliente no PC | em andamento: `docs/fase2.md` |
| 3. Hardware: ESP32-S3 | a fazer |
| 4. Jarvis de verdade: wake word, rotinas, barge-in | a fazer |

## Comandos do dia a dia

```bash
cd /opt/jarvis
docker compose ps                      # status
docker compose logs -f hermes          # logs de um serviço
docker compose up -d                   # aplica mudanças no compose ou no .env
docker compose pull && docker compose up -d   # atualiza as imagens
./scripts/pergunta.sh "sua pergunta"   # testa o Hermes
python3 scripts/chat.py                # conversa com o Jarvis pelo terminal
python3 scripts/testar-jarvis.py       # roda o vetor de teste (--listar mostra os casos)
python3 scripts/medicoes.py --sem-memoria   # mede tudo de novo (depois de uma mudança)
docker compose logs -f jarvis-tools    # chamadas às ferramentas próprias
docker compose exec -T jarvis-tools python -m app.cli recursos   # o que está ligado
docker compose exec -T jarvis-tools python -m app.cli moodle_prazos   # testa uma ferramenta sem o modelo
./scripts/modelo.sh                    # modelo atual; "usar <modelo>", "voltar", "ajustar temperatura 0.3"
./scripts/voz-teste.sh                 # mede a voz de ponta a ponta, sem microfone
docker compose logs -f jarvis-voz      # uma linha por pergunta falada, com os tempos
```

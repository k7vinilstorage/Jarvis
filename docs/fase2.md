# Fase 2: a voz no servidor

**Objetivo:** falar com o Jarvis e ouvir a resposta, primeiro pelo PC, com a mesma ponte que o ESP32 vai usar na Fase 3.

**Ordem combinada:** Fase 2 (voz) → acabamento da Fase 1 (precisão das respostas) → Honcho (memória de longo prazo).

## Como funciona

```
PC ou ESP32 ──WebSocket (porta 10800, com token)──> jarvis-voz ──> Whisper (fala -> texto)
                                                         │──> Hermes (resposta em streaming, com as ferramentas)
     <── áudio da resposta, frase a frase ───────────────└──> Piper (texto -> fala)
```

- **Botão para falar:** no PC, Enter começa a gravar e Enter de novo manda. No ESP32 vai ser um botão. A palavra de ativação ("hey jarvis") fica para a Fase 4.
- **Frase a frase:** a voz começa assim que a primeira frase da resposta fica pronta, enquanto o Hermes ainda escreve o resto.
- **"Um momento":** quando o Hermes vai consultar uma ferramenta (Moodle, clima, e-mail) antes de dizer qualquer coisa, o Jarvis diz "Um momento." para o silêncio não parecer travamento.
- **Texto limpo para a voz:** listas, negrito e links saem, e horas, datas e símbolos viram o jeito falado. "23h59" é lido "23 e 59", "25/09" vira "25 de setembro", "31°C" vira "31 graus" e "R$ 120,50" vira "120 reais e 50 centavos".
- **Conversa:** cada sala (o PC, e depois cada ESP32) tem o seu histórico das últimas mensagens. Ele some depois de 5 minutos parado, ou com `/nova`.
- **Falar por cima:** começar a falar de novo interrompe a resposta que estava tocando.
- **Segurança:** a porta 10800 fica aberta só na rede de casa e sempre exige o token (`JARVIS_VOZ_TOKEN`). O contêiner não guarda áudio. No log fica uma linha por pergunta, com o começo do texto entendido e os tempos.

## Passo 1: instalar

```bash
cd /opt/jarvis
./scripts/fase2-voz.sh
```

O script faz o seguinte:
1. Gera o `JARVIS_VOZ_TOKEN` no `.env`.
2. Acrescenta `docker-compose.voz.yml` ao `COMPOSE_FILE`.
3. Garante que o Whisper e o Piper estão rodando.
4. Compila e sobe o `jarvis-voz`.
5. Roda o teste de ponta a ponta.

Na primeira vez, o Whisper e o Piper carregam os modelos, e o teste demora mais.

**Rede:** o Docker publica a porta por fora do `ufw`, então regras do `ufw` não a limitam. Para ela escutar só na rede de casa (e não, por exemplo, numa VPN ou no IPv6), ponha o IP do servidor na rede de casa em `JARVIS_VOZ_IP` no `.env` e aplique com `docker compose up -d jarvis-voz`. Veja o IP com `hostname -I`. Exemplo: `JARVIS_VOZ_IP=192.168.0.10`. Nunca redirecione a porta 10800 no roteador; o token protege, mas ela não foi feita para a internet.

## Passo 2: teste sem microfone

```bash
./scripts/voz-teste.sh
./scripts/voz-teste.sh --pergunta "Que horas são?" --pergunta "Tenho prova esta semana?"
```

O Piper fala a pergunta, e esse áudio entra na ponte como se fosse você falando. O relatório vai para `medicoes/voz-AAAAMMDD-HHMM.md`, com uma linha por pergunta:

| Coluna | O que é |
|---|---|
| Ouvido / Acerto | o que o Whisper entendeu e quantas palavras da pergunta ele acertou |
| Whisper | tempo para transcrever, depois que você parou de falar |
| 1ª palavra | quando o Hermes começou a responder |
| 1º áudio | **quando você começa a ouvir a resposta**: o número que mais importa |
| Tudo enviado | quando o último pedaço de áudio saiu da ponte |

**Meta da Fase 2:** 1º áudio em até 2 s nas perguntas simples ("que horas são") e até 4 s nas que usam ferramenta.

## Passo 3: falar pelo PC

No PC (Windows, Linux ou Mac, com Python 3.9 ou mais novo):

```bash
pip install websockets sounddevice
```

Copie o `scripts/voz-pc.py` do servidor para o PC:
- com `scp USUARIO@IP-DO-SERVIDOR:/opt/jarvis/scripts/voz-pc.py .`;
- ou abrindo o arquivo e colando o conteúdo num arquivo com o mesmo nome.

Veja o token no servidor com `grep JARVIS_VOZ_TOKEN /opt/jarvis/.env` e rode no PC:

```bash
python voz-pc.py --servidor IP-DO-SERVIDOR --token O-TOKEN
```

Comandos na conversa:
- **Enter:** começa a gravar; Enter de novo manda.
- **Texto e Enter:** manda a pergunta digitada, sem microfone.
- **`/nova`:** conversa nova.
- **`/sair`:** sai.

Depois de cada resposta, ele mostra o que entendeu, as ferramentas usadas e os tempos.

Outras opções:
- **Aparelhos de áudio:** `--listar-audio` mostra os microfones e alto-falantes; `--entrada N` e `--saida N` escolhem pelo número.
- **Arquivo:** `--wav pergunta.wav` manda um arquivo gravado em vez do microfone.
- **Pergunta única:** `--texto "que horas são"` faz uma pergunta e sai.
- **Salvar:** `--salvar resposta.wav` guarda o áudio da resposta.
- **Token fixo:** para não digitar o token toda vez, use as variáveis `JARVIS_SERVIDOR` e `JARVIS_VOZ_TOKEN`.

## Ajustes

| O quê | Onde | Efeito |
|---|---|---|
| Voz | `PIPER_VOICE` no `.env` (amostras em rhasspy.github.io/piper-samples) | `pt_BR-faber-medium`, `pt_BR-cadu-medium`, `pt_BR-jeff-medium` |
| Precisão do Whisper | `WHISPER_MODEL` no `.env` | `small-int8` é o equilíbrio na CPU; `medium-int8` entende melhor, mas demora mais |
| Porta | `JARVIS_VOZ_PORTA` no `.env` | padrão 10800 |

Depois de mudar, aplique com `docker compose up -d whisper piper jarvis-voz` e rode `./scripts/voz-teste.sh` para comparar.

## O protocolo (para o ESP32, na Fase 3)

Endereço: `ws://IP:10800/voz?token=TOKEN&sala=quarto`. O token também vale no cabeçalho `Authorization: Bearer`. As mensagens de texto são JSON; as binárias são áudio.

Do cliente para a ponte:
- `{"tipo":"config","saida_taxa":16000,"saida_formato":"u8"}`: formato do áudio de volta. `u8` são 8 bits sem sinal, que o DAC do ESP32 toca direto. O padrão é o do Piper (22050 Hz, 16 bits).
- `{"tipo":"inicio"}`, depois o áudio em PCM de 16 kHz, mono e 16 bits (binário, em pedaços), e por fim `{"tipo":"fim"}`.
- `{"tipo":"texto","texto":"..."}`: pergunta digitada.
- `{"tipo":"falar","texto":"..."}`: só fala o texto, sem passar pelo Hermes (teste do alto-falante).
- `{"tipo":"cancelar"}`: interrompe a resposta atual.
- `{"tipo":"nova"}`: começa uma conversa nova.

Da ponte para o cliente:
- `estado`: `ouvindo`, `aguardando` (outra sala está usando o modelo), `transcrevendo`, `pensando`, `falando` ou `pronto`. Serve para o LED.
- `transcricao`, `ferramenta`, `frase` (com `falada: false` quando passou do limite de fala): para mostrar na tela.
- `audio_inicio` (taxa e formato), o áudio em binário (pedaços de até 4 KB) e `audio_fim`. Sempre em pares. O áudio chega no ritmo em que toca, com uns 2 s de folga, então o cliente não precisa de um buffer grande.
- `fim`: pergunta, resposta, ferramentas, tempos e erro. Toda vez termina com um `fim`; uma resposta interrompida termina com `erro: "interrompido"`.
- `erro`: mensagem de erro. Também há os ecos `config`, `nova` e `pong` (resposta a `{"tipo":"ping"}`).

O formato de saída (`config`) vale a partir da próxima resposta. Uma recusa antes de conectar volta como HTTP: 401 (token), 400 (sala inválida) ou 429 (mais de 4 conexões).

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| `voz-pc.py` diz "token errado" | Use o valor de `JARVIS_VOZ_TOKEN` do `.env` do servidor, sem aspas. |
| Não conecta (tempo esgotado) | Confira o IP, o `JARVIS_VOZ_IP` e se `docker compose ps` mostra o `jarvis-voz` saudável. |
| "Não ouvi nada" | O microfone está mudo ou a fala ficou curta demais (menos de 0,3 s). Veja `--listar-audio` e escolha o microfone com `--entrada`. |
| Entende errado | Fale mais perto do microfone; ou teste o `WHISPER_MODEL=medium-int8`. |
| 1º áudio lento | Veja no relatório onde está o tempo: Whisper (CPU), 1ª palavra (modelo, ferramentas) ou Piper. `docker compose logs jarvis-voz` mostra os tempos de cada pergunta. |
| Voz lê algo estranho | Me mande a frase: a limpeza do texto para a voz está em `jarvis-voz/app/textos.py`. |

Testes automáticos, sem internet: `docker compose run --rm --no-deps jarvis-voz python -m unittest discover -s testes`.

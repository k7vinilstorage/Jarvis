# ESP32: o Jarvis numa plaquinha

O firmware fica em `jarvis/`. Ele é mais um cliente da ponte `jarvis-voz`, como o `voz-pc.py`: segure o botão, fale, solte, e a resposta sai no alto-falante frase a frase. Toda a lógica continua no servidor. O protocolo está em `docs/fase2.md`.

`rascunho-antigo/` é um esboço de antes da ponte atual. Ele fala HTTP com uma ponte que não existe mais e ficou só como histórico.

## Material

- ESP32 clássico (ESP32-WROOM DevKit)
- Microfone I2S **INMP441**
- DAC I2S **PCM5102**, com saída de linha: ligue numa caixinha amplificada ou num fone
- O botão BOOT e o LED que já vêm na placa

## Ligações

| Peça | Pino da peça | ESP32 |
|---|---|---|
| INMP441 | VDD | 3V3 |
| INMP441 | GND | GND |
| INMP441 | L/R | GND (canal esquerdo) |
| INMP441 | SCK | GPIO26 |
| INMP441 | WS | GPIO25 |
| INMP441 | SD | GPIO33 |
| PCM5102 | VIN | 3V3 ou 5V (o módulo comum aceita os dois) |
| PCM5102 | GND | GND |
| PCM5102 | SCK | GND (o PCM5102 gera o próprio clock) |
| PCM5102 | BCK | GPIO27 |
| PCM5102 | LCK (LRCK) | GPIO14 |
| PCM5102 | DIN | GPIO13 |
| PCM5102 | XSMT | 3V3 (no módulo comum já vem ligado; em nível baixo, o DAC fica mudo) |
| Botão | BOOT da placa | GPIO0 |
| LED | LED da placa | GPIO2 |

Os pinos podem ser trocados em `jarvis/include/config.h`.

Não segure o BOOT enquanto liga ou reinicia a placa: assim ela entra no modo de gravação do firmware.

## Gravar o firmware

Precisa do PlatformIO: a extensão do VS Code ou o `pio` na linha de comando. O `platformio.ini` já aponta para o core Arduino 3.x (pioarduino), e o PlatformIO baixa tudo sozinho na primeira vez.

1. Crie o arquivo de segredos. Ele fica fora do git:

   ```bash
   cd esp32/jarvis
   cp include/segredos.exemplo.h include/segredos.h
   ```

2. Preencha o `include/segredos.h`:
   - `WIFI_SSID` e `WIFI_SENHA`: a sua rede. O ESP32 só funciona em 2,4 GHz.
   - `JARVIS_HOST`: o IP do servidor na rede de casa. Veja com `hostname -I` no servidor.
   - `JARVIS_TOKEN`: o valor de `JARVIS_VOZ_TOKEN` no `.env` do servidor, sem aspas. Veja com `grep JARVIS_VOZ_TOKEN /opt/jarvis/.env`.

3. Se quiser, mude a sala em `include/config.h` (`JARVIS_SALA`, padrão `quarto`). Cada sala tem o seu histórico de conversa na ponte.

4. Ligue a placa no USB, grave e abra o Serial:

   ```bash
   pio run -t upload
   pio device monitor
   ```

   No Linux, se der erro de permissão na porta serial, adicione o usuário ao grupo `dialout` (`sudo usermod -aG dialout $USER`) e entre de novo na sessão.

O token e a senha do WiFi ficam gravados na memória da placa. Se ela for perdida ou emprestada, gere um token novo no servidor.

## Primeiro teste na placa (nesta ordem)

1. **`/tom`:** toca 440 Hz por 2 s, sem rede. Se não sair som, é a ligação do PCM5102.
2. **`/mic`:** fale por 3 s em cada canal. O teste mostra se o microfone manda sinal, em qual canal, e sugere o ganho. Ele já passa a usar o que achou; para ficar assim depois de reiniciar, copie os valores que ele mostra para o `config.h`.
3. **`/estado`:** mostra o WiFi, a conexão com a ponte e a memória livre. O LED para de dar piscadas curtas quando a ponte conecta.
4. **`/falar Olá, tudo certo?`:** a ponte gera a fala sem passar pelo modelo. Isso testa a rede e o alto-falante.
5. **Pergunta escrita:** digite `que horas são` e Enter.
6. **Voz:** segure o BOOT, fale e solte.
7. **Falar por cima:** enquanto o Jarvis responde, segure o BOOT e faça outra pergunta. Um toque curto só manda ele parar de falar.

## Como usar

- **Segure o BOOT e fale; solte para mandar.** Cada fala vai até 20 s (`MAX_FALA_S`).
- **Toque curto no BOOT:** o Jarvis para de falar.
- **Folga do áudio:** o alto-falante junta 250 ms antes de tocar, e de novo se faltar áudio no meio (`FOLGA_AUDIO_MS` no `config.h`). O buffer guarda até uns 2 s, se a memória deixar (o `/estado` mostra o tamanho).
- **Falar por cima:** apertar o botão enquanto o Jarvis fala corta a resposta e já começa a ouvir.

O LED mostra o estado:

| LED | Estado |
|---|---|
| apagado | pronto, esperando o botão |
| aceso | ouvindo (botão apertado) |
| pisca rápido | transcrevendo ou pensando |
| pisca devagar | falando |
| duas piscadas e uma pausa | esperando: outra sala está usando o modelo |
| pisca muito rápido por 1,5 s | deu erro (o motivo aparece no Serial) |
| uma piscada curta a cada 2 s | sem WiFi ou sem a ponte; ele tenta de novo sozinho |

Comandos no Serial (115200 baud):

| Comando | O que faz |
|---|---|
| texto qualquer | pergunta escrita, com resposta falada |
| `/falar <texto>` | só fala o texto (teste do alto-falante pela ponte) |
| `/nova` | começa uma conversa nova |
| `/tom` | 440 Hz por 2 s, sem rede |
| `/mic` | testa o microfone e sugere o canal e o ganho |
| `/volume 0-100` | volume da fala |
| `/estado` | WiFi, ponte e memória |
| `/ajuda` | lista os comandos |

No Serial aparecem:
- o que o Jarvis entendeu (`Você>`);
- cada frase da resposta (`Jarvis>`);
- as ferramentas usadas;
- os tempos da ponte, por exemplo `[tempos] stt=1.40 primeira_palavra=3.20 primeiro_audio=3.30 total=6.10`.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| `[ponte] token recusado` | `JARVIS_TOKEN` no `segredos.h` tem de ser igual ao `JARVIS_VOZ_TOKEN` do `.env`. Depois de um token recusado, ele só tenta de novo a cada 60 s. |
| `[ponte] sem resposta de ...` | Confira o IP em `JARVIS_HOST`, o `docker compose ps jarvis-voz` e o `JARVIS_VOZ_IP` do `.env` (a ponte só escuta nesse IP). De outro computador da rede, `curl http://IP:10800/saude` deve responder `{"status":"ok"}`. |
| `[WiFi] sem conexão` o tempo todo | Nome e senha da rede, rede de 2,4 GHz, sinal onde a placa está (`/estado` mostra o sinal; abaixo de -75 dBm fica ruim). |
| "Não ouvi nada" ou `microfone quase mudo` | Rode `/mic` e fale perto. Se nenhum canal mostrar sinal, confira VDD, GND, L/R no GND e os pinos 26, 25 e 33. |
| Sem som na resposta | Rode `/tom`. Se o tom também não tocar, confira a ligação do PCM5102 e o XSMT. |
| Som picotado | Depois de cada resposta, o Serial mostra `[áudio] 6.2 s tocados; engasgos: N`. Com engasgos, a rede não entregou a tempo: veja o sinal no `/estado`, aproxime a placa do roteador, ou use `SAIDA_TAXA = 16000` no `config.h` (27% menos dados). Com zero engasgos e ainda picotado, o problema é no som (fiação, alimentação do PCM5102): me mande a saída do Serial. |
| Voz muito baixa ou estourada | `/volume`, e o volume da caixinha. |

## Testes (no PC, sem placa)

```bash
cd esp32/jarvis
pio test -e nativo          # a lógica: WebSocket, anel de áudio, mensagens, LED
./teste-ponte/rodar.sh      # o código de rede do firmware contra a ponte jarvis-voz de verdade, com dublês
```

O `teste-ponte` precisa de um Python com o `jarvis-voz/requirements.txt` instalado; aponte com `PYTHON=caminho/do/python`.

Nenhum dos dois testa o hardware: o microfone, o DAC, o WiFi e o botão só dá para conferir na placa.

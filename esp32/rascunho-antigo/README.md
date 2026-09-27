# ESP32: Jarvis básico (botão para falar)

Exemplo simples, sem tela: segure o botão **BOOT**, fale e solte. O áudio vai em streaming para o
`jarvis-voz` no servidor, que transcreve (Whisper), pergunta ao Jarvis (Hermes) e devolve a resposta
falada (Piper). A fala toca no DAC enquanto chega, sem guardar o áudio inteiro na memória.

Sketch: `esp32/jarvis_basico/jarvis_basico.ino`

## Material

- ESP32 comum (ESP32-WROOM DevKit)
- Microfone I2S **INMP441**
- DAC I2S **PCM5102** (saída de linha: ligue numa caixinha amplificada ou num fone)

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
| Botão | BOOT da placa | GPIO0 (já vem na placa; um botão externo vai entre GPIO0 e GND) |
| LED | LED da placa | GPIO2 (na maioria das placas; um LED externo precisa de um resistor de 330 Ω até o GND) |

Não segure o BOOT enquanto liga ou reinicia a placa: assim ela entra no modo de gravação do firmware.

## Arduino IDE

1. Em **Preferências > URLs adicionais para Gerenciadores de Placas**, coloque
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
2. Em **Gerenciador de Placas**, instale **esp32 by Espressif Systems**, versão **3.x**. A 2.x não serve,
   porque o sketch usa a biblioteca `ESP_I2S` da 3.x.
3. Placa: **ESP32 Dev Module**. As outras opções ficam no padrão.
4. Não precisa de biblioteca extra: `WiFi` e `ESP_I2S` já vêm com a placa.
5. Monitor Serial: **115200** baud, final de linha **Nova linha** ("Newline").

## Configuração

No começo do sketch:

```cpp
#define WIFI_SSID      "SUA_REDE"
#define WIFI_PASS      "SUA_SENHA"
#define JARVIS_HOST    "10.0.1.11"                     // IP do servidor na rede local
#define JARVIS_PORT    10800
#define JARVIS_TOKEN   "COLE_AQUI_O_JARVIS_VOZ_TOKEN"
#define SESSAO         "esp32"
#define MAX_RECORD_SEC 15
#define VOLUME         80
```

- **WIFI_SSID / WIFI_PASS:** a sua rede. O ESP32 só funciona em redes de 2,4 GHz.
- **JARVIS_HOST:** o IP do servidor na rede local. Para ver, rode `hostname -I` no servidor.
- **JARVIS_TOKEN:** é o `JARVIS_VOZ_TOKEN` do arquivo `.env` do servidor. Para ver:

  ```bash
  grep JARVIS_VOZ_TOKEN /opt/jarvis/.env
  ```

  Copie só o valor, sem aspas. O token e a senha do WiFi ficam dentro do sketch: não publique o
  arquivo preenchido.
- **SESSAO:** o nome do histórico da conversa no servidor. O servidor lembra das últimas perguntas por
  5 minutos.
- Os pinos, o slot e o ganho do microfone (`MIC_SLOT`, `MIC_GAIN_SHIFT`) e o LED (`LED_PIN`, ou -1
  para desligar) ficam logo abaixo.

## Como usar

Antes do ESP32, confira o servidor pelo PC: `python3 scripts/voz-teste.py --texto "Oi"`.

- **Por voz:** segure o BOOT (o LED acende), fale e solte. O LED pisca enquanto o Jarvis pensa, e a
  resposta sai no DAC. Cada fala vai até 15 s.
- **Pelo Monitor Serial:**

| Comando | O que faz |
|---|---|
| texto qualquer | pergunta escrita, com resposta falada |
| `/falar <texto>` | só fala o texto (testa o Piper e o DAC) |
| `/reset` | o Jarvis esquece a conversa |
| `/mic` | testa o microfone: mostra o nível dos dois canais e sugere `MIC_SLOT` e `MIC_GAIN_SHIFT` |
| `/tom` | toca 440 Hz por 2 s, sem rede (testa a fiação do DAC) |
| `/volume 0-100` | volume da fala |
| `/ajuda` | lista os comandos |

No Serial aparecem a pergunta entendida (`Você>`), a resposta (`Jarvis>`) e os tempos de cada etapa,
por exemplo `[tempos] stt=0.82;llm=1.10;tts=0.45`. Os tempos são em segundos: `stt` é a transcrição,
`llm` é o Jarvis pensando e `tts` é a geração da voz.

## Problemas comuns

- **`[erro HTTP 401]`:** o token está errado. `JARVIS_TOKEN` tem de ser igual ao `JARVIS_VOZ_TOKEN`
  do `.env`. Se você mudou o `.env`, rode `docker compose up -d jarvis-voz` no servidor.
- **`[erro] sem conexão com 10.0.1.11:10800`:**
  - Confira o IP em `JARVIS_HOST`.
  - Veja se o contêiner está no ar: `docker compose ps jarvis-voz`.
  - Teste de outro computador da rede: `curl http://10.0.1.11:10800/saude` deve responder `{"status":"ok"}`.
  - Se o firewall do servidor estiver ligado (`sudo ufw status`), libere a porta só para a rede local:

    ```bash
    sudo ufw allow from 10.0.1.0/24 to any port 10800 proto tcp
    ```

  - **Nunca** redirecione a porta 10800 no roteador: ela não deve ficar aberta para a internet.
- **O Jarvis responde "Não ouvi nada" ou aparece `[aviso] microfone quase mudo`:** rode `/mic` e fale
  perto do microfone. Se nenhum dos dois canais mostrar sinal, confira VDD, GND, L/R no GND e os pinos
  26, 25 e 33. Se ele sugerir outro `MIC_SLOT` ou `MIC_GAIN_SHIFT`, ajuste no sketch.
- **Sem som na resposta:** rode `/tom`.
  - Se o tom também não tocar, confira a fiação do PCM5102.
  - No módulo comum, o pino XSMT (mudo) precisa estar em nível alto; normalmente ele já vem assim.
- **A frase foi cortada no meio:** o Whisper do servidor encerra a frase depois de 0,8 s de silêncio
  (`WYO_WHISPER_VAD_ENDPOINTING` no `docker-compose.yml`). Fale sem pausas longas.
- **A primeira resposta demora:** o modelo pode estar carregando na GPU. O ESP32 espera até 120 s.
- **`[WiFi] ... falhou`:** confira o nome e a senha da rede, se ela é de 2,4 GHz e o sinal onde a placa
  está.

## Limitações deste exemplo

- A fala só começa depois que a resposta inteira fica pronta no servidor. Ainda não há streaming frase
  a frase.
- É half-duplex: enquanto fala, o Jarvis não ouve. Não tem palavra de ativação ("hey jarvis") nem tela.

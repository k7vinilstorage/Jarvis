// Jarvis básico: botão para falar (push-to-talk) com ESP32, microfone I2S INMP441 e DAC I2S PCM5102.
//
// Segure o botão BOOT e fale. Enquanto o botão está apertado, o áudio vai em streaming para o
// jarvis-voz no servidor (POST /conversa, Transfer-Encoding: chunked). Ao soltar, o servidor
// transcreve (Whisper), pergunta ao Jarvis (Hermes) e devolve a resposta falada (WAV do Piper),
// que toca direto no DAC, sem guardar o áudio inteiro na RAM.
//
// Serial Monitor (115200 baud, final de linha "Newline"):
//   texto qualquer     pergunta escrita (POST /pergunta), com resposta falada
//   /falar <texto>     só fala o texto (testa o Piper e o DAC)
//   /reset             o Jarvis esquece a conversa
//   /mic               testa o microfone (nível dos dois canais e ganho sugerido)
//   /tom               toca 440 Hz por 2 s (testa o DAC sem rede)
//   /volume 0-100      volume da fala
//   /ajuda             mostra os comandos
//
// LED: aceso enquanto ouve, piscando enquanto espera o Jarvis.
// Placa "ESP32 Dev Module", core esp32 3.x, sem bibliotecas extras. Ligações: esp32/README.md

#include <WiFi.h>
#include <ESP_I2S.h>
#include <lwip/sockets.h>

// ---------- Configuração ----------
#define WIFI_SSID      "SUA_REDE"
#define WIFI_PASS      "SUA_SENHA"
#define JARVIS_HOST    "10.0.1.11"                     // IP do servidor na rede local
#define JARVIS_PORT    10800                           // porta do jarvis-voz
#define JARVIS_TOKEN   "COLE_AQUI_O_JARVIS_VOZ_TOKEN"  // o JARVIS_VOZ_TOKEN do .env do servidor
#define SESSAO         "esp32"                         // nome do histórico da conversa no servidor
#define MAX_RECORD_SEC 15                              // fala mais longa aceita (o servidor aceita até 30)
#define VOLUME         80                              // 0 a 100; o comando /volume muda na hora

// Microfone I2S INMP441: SCK=26, WS=25, SD=33, L/R=GND, VDD=3V3
const int8_t MIC_SCK = 26;
const int8_t MIC_WS  = 25;
const int8_t MIC_SD  = 33;
const int8_t MIC_SLOT = (int8_t)I2S_STD_SLOT_LEFT;  // L/R no GND = esquerdo; se só vier silêncio, troque para I2S_STD_SLOT_RIGHT
const int    MIC_GAIN_SHIFT = 12;           // 16 = sem ganho; cada -1 dobra o volume (o /mic sugere o valor)

// DAC I2S PCM5102: BCK=27, LCK=14, DIN=13 (SCK do módulo no GND)
const int8_t SPK_BCK  = 27;
const int8_t SPK_WS   = 14;
const int8_t SPK_DOUT = 13;

const int8_t BUTTON_PIN = 0;  // botão BOOT da placa: segure para falar
const int8_t LED_PIN    = 2;  // LED da placa (GPIO2 na maioria); -1 desliga
// ----------------------------------

const uint32_t SAMPLE_RATE       = 16000;
const uint32_t MAX_RECORD_BYTES  = SAMPLE_RATE * 2 * MAX_RECORD_SEC;
const int32_t  TEMPO_CONEXAO_MS  = 5000;    // para abrir a conexão com o servidor
const uint32_t TEMPO_RESPOSTA_MS = 120000;  // o Jarvis pode demorar se o modelo estiver carregando
const uint32_t TEMPO_DADOS_MS    = 10000;   // pausa máxima no meio da resposta

I2SClass i2s;     // entrada: microfone
I2SClass i2sOut;  // saída: DAC PCM5102
int8_t micSlot = MIC_SLOT;  // pode ser trocado em tempo de execução pelo comando /mic
int volumePct = VOLUME;
String inputLine;

// ---------- LED ----------

void ledSet(bool on) {
  if (LED_PIN >= 0) digitalWrite(LED_PIN, on ? HIGH : LOW);
}

// Chamado em laço enquanto espera: pisca 4 vezes por segundo
void ledBlink() {
  if (LED_PIN >= 0) digitalWrite(LED_PIN, (millis() / 125) % 2 ? HIGH : LOW);
}

// ---------- WiFi ----------

// Garante o WiFi conectado (a reconexão automática costuma resolver; se não, começa de novo)
bool wifiOk() {
  if (WiFi.status() == WL_CONNECTED) return true;
  Serial.printf("[WiFi] conectando em %s", WIFI_SSID);
  unsigned long t0 = millis();
  bool reiniciou = false;
  while (WiFi.status() != WL_CONNECTED) {
    if (!reiniciou && millis() - t0 > 8000) {
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      reiniciou = true;
    }
    if (millis() - t0 > 20000) {
      Serial.println(" falhou. Confira WIFI_SSID, WIFI_PASS e o sinal.");
      return false;
    }
    delay(250);
    Serial.print('.');
  }
  Serial.printf(" ok, IP %s\n", WiFi.localIP().toString().c_str());
  return true;
}

// ---------- HTTP com o jarvis-voz ----------

bool conectar(WiFiClient& cli) {
  if (!wifiOk()) return false;
  if (!cli.connect(JARVIS_HOST, JARVIS_PORT, TEMPO_CONEXAO_MS)) {
    Serial.printf("[erro] sem conexão com %s:%d. O jarvis-voz está no ar? A porta %d está liberada no "
                  "firewall do servidor?\n", JARVIS_HOST, JARVIS_PORT, JARVIS_PORT);
    return false;
  }
  cli.setNoDelay(true);
  return true;
}

// O connected() do core continua verdadeiro depois que o servidor fecha a conexão normalmente;
// olhando o socket direto, um servidor que caiu no meio do pedido não faz esperar 120 s à toa
bool conexaoAberta(WiFiClient& cli) {
  if (!cli.connected()) return false;
  uint8_t b;
  return recv(cli.fd(), &b, 1, MSG_DONTWAIT | MSG_PEEK) != 0;  // 0 = o servidor fechou
}

// tamanho < 0: corpo em pedaços (Transfer-Encoding: chunked)
void enviarCabecalhos(WiFiClient& cli, const char* caminho, const char* tipo, int32_t tamanho) {
  String h = "POST ";
  h += caminho;
  h += " HTTP/1.1\r\nHost: " JARVIS_HOST ":";
  h += JARVIS_PORT;
  h += "\r\nAuthorization: Bearer " JARVIS_TOKEN "\r\nContent-Type: ";
  h += tipo;
  if (tamanho < 0) {
    h += "\r\nTransfer-Encoding: chunked";
  } else {
    h += "\r\nContent-Length: ";
    h += tamanho;
  }
  h += "\r\nConnection: close\r\n\r\n";
  cli.print(h);
}

// Lê exatamente n bytes (false se a conexão cair ou ficar TEMPO_DADOS_MS sem chegar nada)
bool lerExato(WiFiClient& cli, uint8_t* buf, size_t n) {
  size_t lidos = 0;
  unsigned long t = millis();
  while (lidos < n) {
    int disp = cli.available();
    if (disp > 0) {
      int r = cli.read(buf + lidos, min((size_t)disp, n - lidos));
      if (r > 0) {
        lidos += r;
        t = millis();
        continue;
      }
    } else if (!conexaoAberta(cli)) {
      return false;
    }
    if (millis() - t > TEMPO_DADOS_MS) return false;
    delay(1);
  }
  return true;
}

bool pular(WiFiClient& cli, uint32_t n) {
  uint8_t lixo[64];
  while (n > 0) {
    size_t parte = min((uint32_t)sizeof(lixo), n);
    if (!lerExato(cli, lixo, parte)) return false;
    n -= parte;
  }
  return true;
}

// Uma linha do cabeçalho HTTP, sem o \r\n
bool lerLinha(WiFiClient& cli, String& linha) {
  linha = "";
  uint8_t c;
  while (lerExato(cli, &c, 1)) {
    if (c == '\n') {
      if (linha.endsWith("\r")) linha.remove(linha.length() - 1);
      return true;
    }
    if (linha.length() < 4096) linha += (char)c;
  }
  return false;
}

int valorHex(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// Os cabeçalhos X-Jarvis-* vêm em UTF-8 com percent-encoding (%C3%A3 = "ã")
String decodificar(const String& s) {
  String out;
  out.reserve(s.length());
  for (size_t i = 0; i < s.length(); i++) {
    if (s[i] == '%' && i + 2 < s.length()) {
      int a = valorHex(s[i + 1]), b = valorHex(s[i + 2]);
      if (a >= 0 && b >= 0) {
        out += (char)(a * 16 + b);
        i += 2;
        continue;
      }
    }
    out += s[i];
  }
  return out;
}

// Corpo curto (JSON de erro ou do /reset), para mostrar no Serial
String lerCorpoTexto(WiFiClient& cli, int32_t tamanho, size_t maximo) {
  String corpo;
  size_t limite = tamanho >= 0 ? min((size_t)tamanho, maximo) : maximo;
  unsigned long t = millis();
  while (corpo.length() < limite && millis() - t < 3000) {
    int c = cli.read();
    if (c >= 0) {
      corpo += (char)c;
      t = millis();
    } else if (!conexaoAberta(cli)) {
      break;
    } else {
      delay(1);
    }
  }
  return corpo;
}

static uint32_t le32(const uint8_t* p) { return p[0] | (p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }
static uint16_t le16(const uint8_t* p) { return p[0] | (p[1] << 8); }

int16_t comVolume(int16_t v) {
  return (int16_t)((int32_t)v * volumePct / 100);
}

// Toca o WAV que vem da rede direto no DAC, em blocos pequenos
void tocarWav(WiFiClient& cli) {
  // Cabeçalho WAV: percorre os blocos até achar "fmt " e "data"
  uint8_t hdr[12];
  if (!lerExato(cli, hdr, 12) || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) {
    Serial.println("[erro] a resposta não é WAV");
    return;
  }
  uint32_t rate = 22050, dataLen = 0;
  uint16_t channels = 1, bits = 16;
  while (true) {
    uint8_t ck[8];
    if (!lerExato(cli, ck, 8)) break;
    uint32_t sz = le32(ck + 4);
    if (!memcmp(ck, "data", 4)) {
      dataLen = sz;
      break;
    }
    uint8_t fmt[16];
    uint32_t take = min(sz, (uint32_t)sizeof(fmt));
    if (!lerExato(cli, fmt, take)) break;
    if (!memcmp(ck, "fmt ", 4) && take >= 16) {
      channels = le16(fmt + 2);
      rate = le32(fmt + 4);
      bits = le16(fmt + 14);
    }
    if (!pular(cli, sz - take + (sz & 1))) break;  // resto do bloco (e o byte de alinhamento)
  }
  if (!dataLen || bits != 16 || channels < 1 || channels > 2) {
    Serial.printf("[erro] formato inesperado (bits=%u, canais=%u, data=%u)\n", bits, channels, (unsigned)dataLen);
    return;
  }

  i2sOut.end();
  i2sOut.setPins(SPK_BCK, SPK_WS, SPK_DOUT, -1);
  if (!i2sOut.begin(I2S_MODE_STD, rate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.println("[erro] falha ao iniciar o DAC I2S");
    return;
  }
  Serial.printf("[falando: %u Hz, %.1f s]\n", (unsigned)rate, dataLen / (float)(rate * 2 * channels));

  static int16_t amostras[256], estereo[512];
  uint32_t tocado = 0;
  while (tocado < dataLen) {
    size_t quero = min((uint32_t)sizeof(amostras), dataLen - tocado);
    if (!lerExato(cli, (uint8_t*)amostras, quero)) {
      Serial.println("[erro] o áudio parou no meio");
      break;
    }
    tocado += quero;
    size_t n = quero / 2;
    if (channels == 2) {
      for (size_t i = 0; i < n; i++) amostras[i] = comVolume(amostras[i]);
      i2sOut.write((uint8_t*)amostras, n * 2);
    } else {  // mono -> os dois canais do DAC
      for (size_t i = 0; i < n; i++) {
        int16_t v = comVolume(amostras[i]);
        estereo[2 * i] = v;
        estereo[2 * i + 1] = v;
      }
      i2sOut.write((uint8_t*)estereo, n * 4);
    }
  }
  delay(150);    // deixa o buffer DMA esvaziar antes de desligar
  i2sOut.end();  // evita chiado do DAC quando não está tocando
}

// Lê a resposta: status, cabeçalhos X-Jarvis-* e o WAV (ou mostra o erro)
void tratarResposta(WiFiClient& cli) {
  Serial.println("[esperando o Jarvis...]");
  unsigned long t0 = millis();
  while (!cli.available()) {
    if (!conexaoAberta(cli)) {
      Serial.println("[erro] o servidor fechou a conexão sem responder");
      ledSet(false);
      return;
    }
    if (millis() - t0 > TEMPO_RESPOSTA_MS) {
      Serial.printf("[erro] sem resposta em %u s\n", (unsigned)(TEMPO_RESPOSTA_MS / 1000));
      ledSet(false);
      return;
    }
    ledBlink();
    delay(10);
  }
  ledSet(false);

  String linha;
  if (!lerLinha(cli, linha) || !linha.startsWith("HTTP/")) {
    Serial.printf("[erro] resposta inválida: %s\n", linha.c_str());
    return;
  }
  int codigo = linha.substring(linha.indexOf(' ') + 1).toInt();
  int32_t tamanho = -1;
  bool ehWav = false;
  String pergunta, resposta, tempos, erro;
  while (lerLinha(cli, linha) && linha.length() > 0) {
    int dp = linha.indexOf(':');
    if (dp <= 0) continue;
    String nome = linha.substring(0, dp);
    nome.trim();
    nome.toLowerCase();  // só o nome: o valor codificado diferencia maiúsculas
    String valor = linha.substring(dp + 1);
    valor.trim();
    if (nome == "content-length") tamanho = valor.toInt();
    else if (nome == "content-type") ehWav = valor.startsWith("audio/wav");
    else if (nome == "x-jarvis-pergunta") pergunta = decodificar(valor);
    else if (nome == "x-jarvis-resposta") resposta = decodificar(valor);
    else if (nome == "x-jarvis-tempos") tempos = valor;
    else if (nome == "x-jarvis-erro") erro = decodificar(valor);
  }

  if (pergunta.length()) Serial.printf("Você> %s\n", pergunta.c_str());
  if (resposta.length()) Serial.printf("Jarvis> %s\n", resposta.c_str());
  if (tempos.length()) Serial.printf("[tempos] %s\n", tempos.c_str());
  if (erro.length()) Serial.printf("[aviso] %s\n", erro.c_str());

  if (codigo == 200 && ehWav) {
    tocarWav(cli);
    return;
  }
  String corpo = lerCorpoTexto(cli, tamanho, 600);
  if (codigo == 200) {
    Serial.printf("[servidor] %s\n", corpo.c_str());
    return;
  }
  Serial.printf("[erro HTTP %d] %s\n", codigo, corpo.c_str());
  if (codigo == 401) {
    Serial.println("[dica] token errado: JARVIS_TOKEN tem de ser igual ao JARVIS_VOZ_TOKEN do .env do servidor");
  }
}

// Pedido com texto no corpo: /pergunta, /falar e /reset
void enviarTexto(const char* caminho, const String& texto) {
  WiFiClient cli;
  if (!conectar(cli)) return;
  enviarCabecalhos(cli, caminho, "text/plain; charset=utf-8", texto.length());
  if (texto.length()) cli.print(texto);
  tratarResposta(cli);
  cli.stop();
}

// ---------- Microfone I2S ----------

void micBegin() {
  i2s.end();
  i2s.setPins(MIC_SCK, MIC_WS, -1, MIC_SD);
  if (!i2s.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO, micSlot)) {
    Serial.println("[erro] falha ao iniciar o microfone I2S");
  } else {
    Serial.printf("[mic ok: SCK=%d WS=%d SD=%d slot=%s]\n", MIC_SCK, MIC_WS, MIC_SD,
                  micSlot == (int8_t)I2S_STD_SLOT_LEFT ? "LEFT" : "RIGHT");
  }
}

// Lê um bloco do microfone e converte para PCM 16 bits (com ganho e remoção de DC)
size_t micRead(int16_t* out, size_t maxSamples) {
  static int32_t raw[256];
  static float prevX = 0, prevY = 0;
  size_t n = i2s.readBytes((char*)raw, min(maxSamples, (size_t)256) * sizeof(int32_t)) / sizeof(int32_t);
  for (size_t i = 0; i < n; i++) {
    float x = raw[i] >> MIC_GAIN_SHIFT;    // INMP441 entrega 24 bits alinhados à esquerda em 32
    float y = x - prevX + 0.995f * prevY;  // filtro passa-alta simples (tira o offset DC)
    prevX = x;
    prevY = y;
    out[i] = (int16_t)constrain((int32_t)y, -32768, 32767);
  }
  return n;
}

// ---------- Botão para falar ----------

void esperarSoltar() {
  while (digitalRead(BUTTON_PIN) == LOW) delay(10);
}

// Grava enquanto o BOOT estiver apertado e manda o áudio em pedaços, já durante a fala
void conversaPorVoz() {
  Serial.println("\n[ouvindo... solte o botão para enviar]");
  ledSet(true);
  WiFiClient cli;
  if (!conectar(cli)) {
    ledSet(false);
    esperarSoltar();
    return;
  }
  enviarCabecalhos(cli, "/conversa?sessao=" SESSAO, "audio/L16; rate=16000; channels=1", -1);

  // Cada pedaço do chunked: tamanho em hexa, \r\n, os bytes do áudio, \r\n
  static uint8_t pedaco[8 + 512 + 2];
  int16_t pcm[256];
  uint32_t enviado = 0;
  int pico = 0;
  bool caiu = false;
  unsigned long t0 = millis();
  while (enviado < MAX_RECORD_BYTES) {
    if (digitalRead(BUTTON_PIN) == HIGH && millis() - t0 > 300) break;  // soltou (mínimo de 0,3 s)
    if (cli.available()) break;  // o servidor já respondeu (um erro): para de mandar
    size_t n = micRead(pcm, 256);
    if (n == 0) continue;
    size_t bytes = min(n * 2, (size_t)(MAX_RECORD_BYTES - enviado));
    for (size_t i = 0; i < bytes / 2; i++) pico = max(pico, abs((int)pcm[i]));
    int h = snprintf((char*)pedaco, 8, "%X\r\n", (unsigned)bytes);
    memcpy(pedaco + h, pcm, bytes);
    pedaco[h + bytes] = '\r';
    pedaco[h + bytes + 1] = '\n';
    size_t total = h + bytes + 2;
    if (cli.write(pedaco, total) != total) {
      caiu = true;
      break;
    }
    enviado += bytes;
  }
  if (enviado >= MAX_RECORD_BYTES) Serial.printf("[limite de %d s]\n", MAX_RECORD_SEC);
  Serial.printf("[gravou %.1f s, pico %d de 32767]\n", enviado / (2.0f * SAMPLE_RATE), pico);
  if (pico < 500) Serial.println("[aviso] microfone quase mudo - rode /mic para conferir");
  if (caiu) {
    Serial.println("[erro] a conexão caiu durante o envio");  // ainda tenta ler, pode ser um erro do servidor
  } else {
    cli.print("0\r\n\r\n");  // fim do corpo
  }
  ledSet(false);
  tratarResposta(cli);
  cli.stop();
  esperarSoltar();
}

// ---------- Diagnóstico ----------

// Toca 2 s de 440 Hz no DAC: testa fiação e I2S sem depender da rede
void toneTest() {
  i2sOut.end();
  i2sOut.setPins(SPK_BCK, SPK_WS, SPK_DOUT, -1);
  if (!i2sOut.begin(I2S_MODE_STD, 22050, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.println("[erro] i2sOut.begin() falhou - o ESP32 não conseguiu abrir o segundo I2S");
    return;
  }
  Serial.printf("[tom 440 Hz por 2 s | BCK=%d WS=%d DIN=%d]\n", SPK_BCK, SPK_WS, SPK_DOUT);

  int16_t buf[512];  // 256 quadros estéreo
  for (int block = 0; block < 86; block++) {  // ~2 s a 22050 Hz
    for (int i = 0; i < 256; i++) {
      int16_t v = comVolume((int16_t)(12000 * sin(2 * PI * 440.0 * ((block * 256.0) + i) / 22050.0)));
      buf[2 * i] = v;
      buf[2 * i + 1] = v;
    }
    size_t w = i2sOut.write((uint8_t*)buf, sizeof(buf));
    if (block == 0) Serial.printf("[primeiro bloco: escreveu %u de %u bytes]\n", (unsigned)w, (unsigned)sizeof(buf));
  }
  delay(150);
  i2sOut.end();
  Serial.println("[fim do tom]");
}

// Testa os dois slots I2S e mostra os valores crus de 32 bits:
// assim dá para ver se o microfone está mandando alguma coisa.
void micLevelTest() {
  int32_t raw[256];
  int8_t slots[2] = { (int8_t)I2S_STD_SLOT_LEFT, (int8_t)I2S_STD_SLOT_RIGHT };
  int32_t best = 0;
  int8_t bestSlot = micSlot;

  for (int s = 0; s < 2; s++) {
    micSlot = slots[s];
    micBegin();
    Serial.printf("\n--- slot %s: fale algo (3 s) ---\n", s == 0 ? "LEFT" : "RIGHT");

    size_t bytes = i2s.readBytes((char*)raw, sizeof(raw));  // descarta o primeiro bloco
    int32_t rawPeak = 0;
    unsigned long t0 = millis();
    while (millis() - t0 < 3000) {
      bytes = i2s.readBytes((char*)raw, sizeof(raw));
      size_t n = bytes / sizeof(int32_t);
      if (n == 0) continue;
      for (size_t i = 0; i < n; i++) rawPeak = max(rawPeak, (int32_t)abs(raw[i] >> 8));
      if (millis() - t0 < 300) {  // mostra algumas amostras cruas
        Serial.printf("  amostras: %08X %08X %08X (lidos %u bytes)\n",
                      (unsigned)raw[0], (unsigned)raw[1], (unsigned)raw[2], (unsigned)bytes);
      }
    }
    Serial.printf("  pico cru (24 bits) = %d de 8388607\n", (int)rawPeak);
    if (rawPeak > best) {
      best = rawPeak;
      bestSlot = slots[s];
    }
  }

  micSlot = bestSlot;
  micBegin();
  if (best < 2000) {
    Serial.println("\n[nada chegou do microfone - confira a ligação: VDD=3V3, GND, L/R=GND,\n"
                   " SCK=26, WS=25, SD=33]");
  } else {
    int shift = 0;
    while ((best >> shift) > 16000 && shift < 16) shift++;  // ganho sugerido
    Serial.printf("\n[ok! use MIC_SLOT = I2S_STD_SLOT_%s e MIC_GAIN_SHIFT = %d]\n",
                  bestSlot == (int8_t)I2S_STD_SLOT_LEFT ? "LEFT" : "RIGHT", 8 + shift);
  }
}

// ---------- Serial ----------

void mostrarAjuda() {
  Serial.println("Segure BOOT e fale, ou digite uma pergunta. Comandos:\n"
                 "  /falar <texto>   fala o texto (teste do DAC)\n"
                 "  /reset           o Jarvis esquece a conversa\n"
                 "  /mic             testa o microfone\n"
                 "  /tom             toca 440 Hz por 2 s\n"
                 "  /volume 0-100    volume da fala");
}

void tratarLinha(String linha) {
  linha.trim();
  if (linha.isEmpty()) return;

  if (linha == "/reset") {
    enviarTexto("/reset?sessao=" SESSAO, "");
  } else if (linha == "/falar" || linha.startsWith("/falar ")) {
    String texto = linha.substring(6);
    texto.trim();
    if (texto.isEmpty()) Serial.println("uso: /falar <texto>");
    else enviarTexto("/falar", texto);
  } else if (linha == "/mic") {
    micLevelTest();
  } else if (linha == "/tom") {
    toneTest();
  } else if (linha == "/volume" || linha.startsWith("/volume ")) {
    String valor = linha.substring(7);
    valor.trim();
    if (valor.length() && isdigit((unsigned char)valor[0])) volumePct = constrain((int)valor.toInt(), 0, 100);
    Serial.printf("[volume %d%%]\n", volumePct);
  } else if (linha == "/ajuda" || linha == "/help") {
    mostrarAjuda();
  } else if (linha.startsWith("/")) {
    Serial.println("[comando desconhecido] /ajuda mostra os comandos");
  } else {
    enviarTexto("/pergunta?sessao=" SESSAO, linha);
  }
  Serial.print("> ");
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(BUTTON_PIN, INPUT_PULLUP);
  if (LED_PIN >= 0) pinMode(LED_PIN, OUTPUT);
  ledSet(false);
  micBegin();

  Serial.printf("\nJarvis básico | servidor %s:%d | sessão %s\n", JARVIS_HOST, JARVIS_PORT, SESSAO);
  if (strcmp(JARVIS_TOKEN, "COLE_AQUI_O_JARVIS_VOZ_TOKEN") == 0) {
    Serial.println("[aviso] preencha JARVIS_TOKEN no começo do sketch (é o JARVIS_VOZ_TOKEN do .env do servidor)");
  }
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // sem economia de energia no WiFi: menos atraso e sem engasgos no áudio
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  wifiOk();
  mostrarAjuda();
  Serial.print("> ");
}

void loop() {
  if (digitalRead(BUTTON_PIN) == LOW) {
    delay(30);  // debounce
    if (digitalRead(BUTTON_PIN) == LOW) {
      conversaPorVoz();
      Serial.print("> ");
    }
  }

  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      tratarLinha(inputLine);
      inputLine = "";
    } else if (inputLine.length() < 1000) {
      inputLine += c;
    }
  }
}

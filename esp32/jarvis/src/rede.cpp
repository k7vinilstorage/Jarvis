#include "rede.h"

#include <Arduino.h>
#include <WiFi.h>
#include <errno.h>
#include <esp_random.h>
#include <lwip/sockets.h>

#include "config.h"
#include "segredos.h"
#include "websocket.h"

namespace rede {
namespace {

const int32_t TEMPO_CONEXAO_MS = 3000;
const uint32_t TEMPO_ABERTURA_MS = 5000;     // resposta do pedido de abertura do WebSocket
const uint32_t PING_A_CADA_MS = 25000;       // sem mandar nada há esse tempo: manda um ping
const uint32_t SILENCIO_MAXIMO_MS = 70000;   // sem receber nada há esse tempo (nem o pong): a ponte sumiu
const uint32_t WIFI_RECOMECAR_MS = 30000;    // sem WiFi há esse tempo: começa a conexão de novo
const uint32_t ESPERA_MAXIMA_MS = 15000;     // entre tentativas de conectar à ponte
const uint32_t ESPERA_TOKEN_MS = 60000;      // depois de um token recusado, tenta bem menos

Eventos eventos;
WiFiClient cliente;
bool aberta = false;
bool wifiAntes = false;
uint32_t semWifiDesde = 0;
uint32_t proximaTentativa = 0;
uint32_t espera = 1000;
uint32_t ultimoRecebido = 0;
uint32_t ultimoEnviado = 0;
uint32_t ultimaChecagem = 0;
const char* fecharDepois = nullptr;  // a ponte pediu para fechar (marcado dentro do leitor, tratado depois)

bool enviarQuadro(uint8_t op, const uint8_t* dados, size_t n);

const char* motivoDoFechamento(uint16_t codigo) {
  switch (codigo) {
    case 4401:
      return "token recusado: confira JARVIS_TOKEN no segredos.h";
    case 4400:
      return "sala inválida: confira JARVIS_SALA no config.h";
    case 4429:
      return "conexões demais abertas na ponte";
    case 1001:
      return "a ponte está reiniciando";
    default:
      return "a ponte fechou a conexão";
  }
}

class Ouvinte : public ws::Ouvinte {
 public:
  // Se a conexão caiu no meio da leitura (um pong que não saiu), o que sobrou no buffer não vale mais
  void texto(const char* dados, size_t n, bool cortada) override {
    if (aberta) eventos.mensagem(dados, n, cortada);
  }
  void binario(const uint8_t* dados, size_t n) override {
    if (aberta) eventos.audio(dados, n);
  }
  void controle(uint8_t op, const uint8_t* dados, size_t n) override {
    if (op == ws::Ping) {
      enviarQuadro(ws::Pong, dados, n);
    } else if (op == ws::Fechar) {
      uint16_t codigo = n >= 2 ? (uint16_t)((dados[0] << 8) | dados[1]) : 1005;
      const uint8_t resposta[2] = {(uint8_t)(1000 >> 8), (uint8_t)(1000 & 0xFF)};
      enviarQuadro(ws::Fechar, resposta, sizeof(resposta));
      if (codigo == 4401) espera = ESPERA_TOKEN_MS;
      fecharDepois = motivoDoFechamento(codigo);
    }
  }
};

char bufTexto[8192];  // a maior mensagem de texto da ponte é o "fim", com a resposta inteira
Ouvinte ouvinte;
ws::Leitor leitor(bufTexto, sizeof(bufTexto), ouvinte);

void fechar(const char* motivo) {
  if (!aberta) return;
  cliente.stop();
  aberta = false;  // o leitor recomeça do zero na próxima conexão
  proximaTentativa = millis() + espera;
  eventos.caiu(motivo);
}

bool enviarQuadro(uint8_t op, const uint8_t* dados, size_t n) {
  if (!aberta) return false;
  static uint8_t quadro[ws::MAX_CABECALHO + 1024];
  uint32_t aleatorio = esp_random();
  uint8_t mascara[4];
  memcpy(mascara, &aleatorio, 4);
  size_t cabecalho = ws::montarCabecalho(quadro, op, n, mascara);
  size_t enviado = 0;
  do {  // o cabeçalho vai junto com o primeiro pedaço: um áudio de 1 KB sai num pacote só
    size_t cabe = sizeof(quadro) - cabecalho;
    size_t k = n - enviado < cabe ? n - enviado : cabe;
    memcpy(quadro + cabecalho, dados + enviado, k);
    ws::mascarar(quadro + cabecalho, k, mascara, enviado);
    if (cliente.write(quadro, cabecalho + k) != cabecalho + k) {
      fechar("falha ao enviar");
      return false;
    }
    enviado += k;
    cabecalho = 0;
  } while (enviado < n);
  ultimoEnviado = millis();
  return true;
}

// Uma linha da resposta HTTP, sem o \r\n. false se o tempo acabar ou a conexão cair.
bool lerLinha(std::string& linha, uint32_t prazo) {
  linha.clear();
  while ((int32_t)(prazo - millis()) > 0) {
    int c = cliente.read();
    if (c < 0) {
      if (!cliente.connected()) return false;
      delay(1);
      continue;
    }
    if (c == '\n') {
      if (!linha.empty() && linha.back() == '\r') linha.pop_back();
      return true;
    }
    if (linha.size() < 512) linha += (char)c;
  }
  return false;
}

// O connected() do core continua verdadeiro um tempo depois que a ponte fecha; olhando o socket, a queda
// aparece na hora.
bool fechadaPelaPonte() {
  uint8_t b;
  int r = recv(cliente.fd(), &b, 1, MSG_DONTWAIT | MSG_PEEK);
  if (r > 0) return false;
  if (r == 0) return true;
  return errno != EWOULDBLOCK && errno != EAGAIN;
}

bool conectar() {
  if (!cliente.connect(JARVIS_HOST, JARVIS_PORTA, TEMPO_CONEXAO_MS)) {
    Serial.printf("[ponte] sem resposta de %s:%d. O jarvis-voz está no ar? O IP está certo?\n", JARVIS_HOST,
                  JARVIS_PORTA);
    return false;
  }
  cliente.setNoDelay(true);
  uint8_t chave[16];
  esp_fill_random(chave, sizeof(chave));
  std::string pedido = ws::pedidoDeAbertura(JARVIS_HOST, JARVIS_PORTA, "/voz?sala=" JARVIS_SALA,
                                            ws::base64(chave, sizeof(chave)), JARVIS_TOKEN);
  cliente.write((const uint8_t*)pedido.data(), pedido.size());

  uint32_t prazo = millis() + TEMPO_ABERTURA_MS;
  std::string linha;
  int status = lerLinha(linha, prazo) ? ws::statusHttp(linha) : -1;
  while (status > 0 && lerLinha(linha, prazo) && !linha.empty()) {
  }  // os cabeçalhos da resposta não interessam; o que vem depois da linha em branco já é WebSocket

  if (status == 101) {
    aberta = true;
    leitor.reiniciar();
    ultimoRecebido = ultimoEnviado = millis();
    espera = 1000;
    return true;
  }
  std::string corpo;  // a ponte explica a recusa em uma linha de texto
  lerLinha(corpo, millis() + 500);
  cliente.stop();
  if (status == 401) {
    Serial.println("[ponte] token recusado: JARVIS_TOKEN no segredos.h tem de ser o JARVIS_VOZ_TOKEN do .env");
    espera = ESPERA_TOKEN_MS;
  } else if (status < 0) {
    Serial.printf("[ponte] resposta inesperada de %s:%d. É mesmo o jarvis-voz?\n", JARVIS_HOST, JARVIS_PORTA);
  } else {
    Serial.printf("[ponte] recusou a conexão (HTTP %d): %s\n", status, corpo.c_str());
  }
  return false;
}

void cuidarDoWifi(uint32_t agora) {
  bool ok = WiFi.status() == WL_CONNECTED;
  if (ok != wifiAntes) {
    wifiAntes = ok;
    if (ok) {
      Serial.printf("[WiFi] conectado: IP %s, sinal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
      proximaTentativa = agora;
    } else {
      Serial.println("[WiFi] sem conexão; tentando de novo");
      semWifiDesde = agora;
      fechar("o WiFi caiu");
    }
  }
  if (!ok && agora - semWifiDesde > WIFI_RECOMECAR_MS) {
    semWifiDesde = agora;
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_SENHA);
  }
}

}  // namespace

void comecar(const Eventos& e) {
  eventos = e;
  WiFi.setHostname("jarvis-" JARVIS_SALA);  // antes do mode(), senão não vale
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // sem economia de energia: menos atraso e sem engasgos no áudio
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_SENHA);
  semWifiDesde = millis();
  Serial.printf("[WiFi] conectando em %s\n", WIFI_SSID);
}

void manter() {
  uint32_t agora = millis();
  cuidarDoWifi(agora);
  if (!wifiAntes) return;

  if (!aberta) {
    if ((int32_t)(agora - proximaTentativa) < 0) return;
    if (!conectar()) {
      proximaTentativa = millis() + espera;
      if (espera < ESPERA_MAXIMA_MS) espera = espera * 2 < ESPERA_MAXIMA_MS ? espera * 2 : ESPERA_MAXIMA_MS;
    }
    return;
  }

  static uint8_t buf[1024];
  for (int voltas = 0; voltas < 8 && aberta; voltas++) {  // no máximo 8 KB por volta: o loop não fica preso
    int disponivel = cliente.available();
    if (disponivel <= 0) break;
    size_t quero = leitor.quantoAceita(eventos.espacoAudio());
    if (quero == 0) break;  // o alto-falante está cheio: o resto espera no TCP, e a ponte segura o envio
    if (quero > (size_t)disponivel) quero = disponivel;
    if (quero > sizeof(buf)) quero = sizeof(buf);
    int n = cliente.read(buf, quero);
    if (n <= 0) break;
    ultimoRecebido = millis();
    leitor.alimentar(buf, n);
    if (leitor.comErro()) {
      Serial.printf("[ponte] erro no WebSocket: %s\n", leitor.erro());
      fechar("erro no WebSocket");
    } else if (fecharDepois) {
      const char* motivo = fecharDepois;
      fecharDepois = nullptr;
      fechar(motivo);
    }
  }
  if (!aberta) return;

  agora = millis();
  if (agora - ultimaChecagem > 500) {
    ultimaChecagem = agora;
    if (cliente.available() == 0 && fechadaPelaPonte()) {
      fechar("a ponte fechou a conexão");
      return;
    }
  }
  if (agora - ultimoRecebido > SILENCIO_MAXIMO_MS) {
    fechar("a ponte parou de responder");
    return;
  }
  if (agora - ultimoEnviado > PING_A_CADA_MS) enviarTexto("{\"tipo\":\"ping\"}");
}

bool conectada() { return aberta; }

bool enviarTexto(const std::string& json) {
  return enviarQuadro(ws::Texto, (const uint8_t*)json.data(), json.size());
}

bool enviarAudio(const int16_t* pcm, size_t amostras) {
  return enviarQuadro(ws::Binario, (const uint8_t*)pcm, amostras * 2);
}

void mostrarEstado() {
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("WiFi: %s, IP %s, sinal %d dBm\n", WIFI_SSID, WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    Serial.printf("WiFi: sem conexão com %s\n", WIFI_SSID);
  }
  Serial.printf("Ponte: %s:%d, sala %s, %s\n", JARVIS_HOST, JARVIS_PORTA, JARVIS_SALA,
                aberta ? "conectada" : "desconectada");
  Serial.printf("Memória livre: %u bytes (mínimo desde que ligou: %u)\n", (unsigned)ESP.getFreeHeap(),
                (unsigned)ESP.getMinFreeHeap());
}

}  // namespace rede

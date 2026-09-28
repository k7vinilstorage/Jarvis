// Jarvis no ESP32: segure o BOOT e fale; solte para mandar. A resposta sai no PCM5102, frase a frase.
// Um toque curto no BOOT faz o Jarvis parar de falar. Protocolo da ponte: docs/fase2.md.
//
// Serial (115200): texto qualquer = pergunta escrita. Comandos em /ajuda.

#include <Arduino.h>

#if !__has_include("segredos.h")
#error "Falta o include/segredos.h: copie o include/segredos.exemplo.h para include/segredos.h e preencha"
#endif

#include "config.h"
#include "led.h"
#include "mensagens.h"
#include "pcm.h"
#include "rede.h"
#include "segredos.h"
#include "som.h"

namespace {

const uint32_t ERRO_VISIVEL_MS = 1500;
const uint32_t DEBOUNCE_MS = 30;

Estado estadoPonte = Estado::Desconhecido;
bool recebendoAudio = false;  // entre o audio_inicio e o audio_fim de uma resposta que queremos ouvir
bool gravando = false;
uint32_t inicioGravacao = 0;
uint32_t amostrasGravadas = 0;
int picoGravacao = 0;
uint32_t erroAte = 0;

bool botaoLido = false;
bool botaoFirme = false;
uint32_t botaoMudou = 0;

String linhaSerial;

void marcarErro() { erroAte = millis() + ERRO_VISIVEL_MS; }

// ---------------------------------------------------------------- mensagens da ponte

void aoMensagem(const char* json, size_t n, bool cortada) {
  Evento ev;
  if (cortada) {
    ev.tipo = tipoDoComeco(json, n);  // grande demais para o buffer: só o tipo interessa
  } else if (!interpretar(json, n, ev)) {
    Serial.println("[ponte] mensagem inválida");
    return;
  }
  switch (ev.tipo) {
    case Tipo::Pronto:
      estadoPonte = Estado::Pronto;
      Serial.printf("[ponte] conectado a %s:%d, sala %s\n", JARVIS_HOST, JARVIS_PORTA, JARVIS_SALA);
      if (SAIDA_TAXA) rede::enviarTexto(mensagemConfig(SAIDA_TAXA));
      break;
    case Tipo::Estado:
      estadoPonte = ev.estado;
      break;
    case Tipo::Transcricao:
      Serial.printf("Você> %s\n", ev.texto.c_str());
      break;
    case Tipo::Ferramenta:
      Serial.printf("[ferramenta] %s\n", ev.texto.c_str());
      break;
    case Tipo::Frase:
      Serial.printf("Jarvis> %s%s\n", ev.texto.c_str(), ev.falada ? "" : " (só texto)");
      break;
    case Tipo::AudioInicio:
      if (ev.formato != "s16le" || ev.canais != 1 || ev.taxa < 8000 || ev.taxa > 48000) {
        Serial.printf("[erro] áudio num formato inesperado: %s, %d canais, %u Hz\n", ev.formato.c_str(), ev.canais,
                      (unsigned)ev.taxa);
        recebendoAudio = false;
        marcarErro();
        break;
      }
      som::iniciarFala(ev.taxa);
      recebendoAudio = true;
      break;
    case Tipo::AudioFim:
      if (recebendoAudio) som::terminarFala();
      recebendoAudio = false;
      break;
    case Tipo::Fim:
      if (recebendoAudio) som::terminarFala();
      recebendoAudio = false;
      if (!ev.tempos.empty()) Serial.printf("[tempos] %s\n", ev.tempos.c_str());
      if (!ev.erro.empty() && ev.erro != "interrompido") {
        Serial.printf("[aviso] %s\n", ev.erro.c_str());
        marcarErro();
      }
      break;
    case Tipo::Erro:
      Serial.printf("[erro] %s\n", cortada ? "(mensagem longa demais)" : ev.texto.c_str());
      marcarErro();
      break;
    case Tipo::Nova:
      Serial.println("[conversa nova]");
      break;
    default:
      break;
  }
}

void aoAudio(const uint8_t* dados, size_t n) {
  if (recebendoAudio) som::empurrar(dados, n);  // fora de uma fala (a que foi cortada), descarta
}

size_t espacoAudio() { return recebendoAudio ? som::espacoLivre() : 65536; }

void aoCair(const char* motivo) {
  Serial.printf("[ponte] desconectado: %s\n", motivo);
  estadoPonte = Estado::Desconhecido;
  if (recebendoAudio) som::terminarFala();  // o que já chegou ainda toca
  recebendoAudio = false;
  gravando = false;
}

// ---------------------------------------------------------------- botão e microfone

bool conectadaOuAvisa() {
  if (rede::conectada()) return true;
  Serial.println("[sem conexão com a ponte: espere o LED parar de dar piscadas curtas]");
  return false;
}

bool enviar(const std::string& json) { return conectadaOuAvisa() && rede::enviarTexto(json); }

// Cala a resposta atual antes de uma pergunta nova: a ponte também corta a dela ao receber a pergunta
void calarResposta() {
  som::calar();
  recebendoAudio = false;
}

void comecarGravacao() {
  if (!conectadaOuAvisa()) return;
  calarResposta();
  if (!enviar(mensagemSimples("inicio"))) return;
  gravando = true;
  inicioGravacao = millis();
  amostrasGravadas = 0;
  picoGravacao = 0;
  Serial.println("[ouvindo... solte o botão para mandar]");
}

void terminarGravacao(bool porLimite) {
  gravando = false;
  if (!porLimite && millis() - inicioGravacao < TOQUE_CURTO_MS) {
    enviar(mensagemSimples("cancelar"));
    Serial.println("[toque curto: parei]");
    return;
  }
  if (!enviar(mensagemSimples("fim"))) return;
  Serial.printf("[gravou %.1f s, pico %d de 32767]\n", amostrasGravadas / 16000.0f, picoGravacao);
  if (picoGravacao < 500) Serial.println("[aviso] o microfone está quase mudo: rode /mic");
}

void gravarUmPedaco() {
  static int16_t pcm[512];
  size_t n = som::lerMicrofone(pcm, 512);
  if (n == 0) return;
  int pico = picoAbsoluto(pcm, n);
  if (pico > picoGravacao) picoGravacao = pico;
  if (!rede::enviarAudio(pcm, n)) return;  // a conexão caiu: aoCair já parou a gravação
  amostrasGravadas += n;
  if (amostrasGravadas >= MAX_FALA_S * 16000) {
    Serial.printf("[limite de %u s]\n", (unsigned)MAX_FALA_S);
    terminarGravacao(true);
  }
}

void lerBotao() {
  bool apertado = digitalRead(PINO_BOTAO) == LOW;
  uint32_t agora = millis();
  if (apertado != botaoLido) {
    botaoLido = apertado;
    botaoMudou = agora;
  }
  if (apertado == botaoFirme || agora - botaoMudou < DEBOUNCE_MS) return;
  botaoFirme = apertado;
  if (apertado) {
    comecarGravacao();
  } else if (gravando) {
    terminarGravacao(false);
  }
}

// ---------------------------------------------------------------- Serial

void mostrarAjuda() {
  Serial.println(
      "Segure o BOOT e fale; solte para mandar. Um toque curto faz o Jarvis parar de falar.\n"
      "No Serial:\n"
      "  texto qualquer   pergunta escrita, com resposta falada\n"
      "  /falar <texto>   só fala o texto (teste do alto-falante pela ponte)\n"
      "  /nova            começa uma conversa nova\n"
      "  /tom             toca 440 Hz por 2 s, sem rede (teste do DAC)\n"
      "  /mic             testa o microfone e sugere o canal e o ganho\n"
      "  /volume 0-100    volume da fala\n"
      "  /estado          WiFi, ponte e memória");
}

void tratarLinha(String linha) {
  linha.trim();
  if (linha.isEmpty()) return;
  if (linha == "/ajuda" || linha == "/help") {
    mostrarAjuda();
  } else if (linha == "/nova") {
    enviar(mensagemSimples("nova"));
  } else if (linha == "/tom") {
    calarResposta();
    som::tocarTom();
  } else if (linha == "/mic") {
    som::testarMicrofone();
  } else if (linha == "/volume" || linha.startsWith("/volume ")) {
    String valor = linha.substring(7);
    valor.trim();
    if (valor.length() && isdigit((unsigned char)valor[0])) som::definirVolume(valor.toInt());
    Serial.printf("[volume %d%%]\n", som::volume());
  } else if (linha == "/estado") {
    rede::mostrarEstado();
    Serial.printf("Estado da ponte: %s · volume %d%% · buffer de áudio %u KB\n", nomeDoEstado(estadoPonte),
                  som::volume(), (unsigned)(som::tamanhoBuffer() / 1024));
  } else if (linha == "/falar" || linha.startsWith("/falar ")) {
    String texto = linha.substring(6);
    texto.trim();
    if (texto.isEmpty()) {
      Serial.println("uso: /falar <texto>");
    } else if (conectadaOuAvisa()) {
      calarResposta();
      enviar(mensagemComTexto("falar", texto.c_str()));
    }
  } else if (linha.startsWith("/")) {
    Serial.println("[comando desconhecido] /ajuda mostra os comandos");
  } else if (conectadaOuAvisa()) {
    calarResposta();
    enviar(mensagemComTexto("texto", linha.c_str()));  // a ponte devolve a pergunta como "Você>"
  }
}

void lerSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (linhaSerial.length()) tratarLinha(linhaSerial);
      linhaSerial = "";
    } else if (linhaSerial.length() < 500) {
      linhaSerial += c;
    }
  }
}

void atualizarLed() {
  if (PINO_LED < 0) return;
  uint32_t agora = millis();
  bool erro = (int32_t)(erroAte - agora) > 0;
  Luz luz = escolherLuz(rede::conectada(), gravando, som::tocando(), erro, estadoPonte);
  digitalWrite(PINO_LED, luzAcesa(luz, agora) ? HIGH : LOW);
}

void avisarSegredosDeExemplo() {
  if (strcmp(WIFI_SSID, "SUA_REDE") == 0) Serial.println("[aviso] preencha WIFI_SSID e WIFI_SENHA no segredos.h");
  if (strcmp(JARVIS_HOST, "IP_DO_SERVIDOR") == 0) Serial.println("[aviso] preencha JARVIS_HOST no segredos.h");
  if (strcmp(JARVIS_TOKEN, "COLE_O_JARVIS_VOZ_TOKEN") == 0) {
    Serial.println("[aviso] preencha JARVIS_TOKEN no segredos.h (é o JARVIS_VOZ_TOKEN do .env do servidor)");
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PINO_BOTAO, INPUT_PULLUP);
  if (PINO_LED >= 0) pinMode(PINO_LED, OUTPUT);
  Serial.printf("\nJarvis | ponte %s:%d | sala %s\n", JARVIS_HOST, JARVIS_PORTA, JARVIS_SALA);
  avisarSegredosDeExemplo();
  som::comecar();
  rede::comecar({aoMensagem, aoCair, espacoAudio, aoAudio});
  mostrarAjuda();
}

void loop() {
  lerBotao();
  if (gravando) gravarUmPedaco();  // espera o bloco do microfone (32 ms): é o que dá o ritmo enquanto grava
  rede::manter();
  lerSerial();
  atualizarLed();
  som::mostrarRelatorio();
  if (!gravando) delay(1);
}

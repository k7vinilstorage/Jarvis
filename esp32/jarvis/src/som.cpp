#include "som.h"

#include <Arduino.h>
#include <ESP_I2S.h>

#include <atomic>

#include "anel.h"
#include "config.h"
#include "fala.h"
#include "pcm.h"

namespace som {
namespace {

const uint32_t TAXA_MIC = 16000;
const size_t BLOCO_MIC = 512;
const uint32_t SILENCIO_FINAL_MS = 80;  // mais que o DMA (6 x 240 quadros): a última frase sai inteira

I2SClass mic;
I2SClass dac;
FiltroMic filtro(MIC_DESLOCAMENTO);
bool micDireito = MIC_CANAL_DIREITO;

// Até ~2 s a 22050 Hz, a folga que a ponte manda à frente (o que não couber espera no TCP). Fica no heap: na
// memória estática não sobraria espaço para o WiFi.
Anel* anel = nullptr;
const size_t RESERVA_WIFI = 90 * 1024;  // o anel só pega memória se sobrar isso para o WiFi e o TCP

ControleDeFala controle;  // só a tarefa do alto-falante mexe
char relatorio[160];
std::atomic<bool> temRelatorio{false};

// Combinados entre o loop (rede e botão) e a tarefa do alto-falante
std::atomic<uint32_t> taxaPedida{0};  // != 0: uma fala está aberta
std::atomic<bool> fimPedido{false};   // a fala acabou de chegar: tocar o resto e fechar
std::atomic<bool> calarPedido{false};
std::atomic<bool> dacAberto{false};
std::atomic<int> volumeAtual{VOLUME_INICIAL};
TaskHandle_t tarefa = nullptr;

bool abrirMic() {
  mic.end();
  mic.setPins(PINO_MIC_SCK, PINO_MIC_WS, -1, PINO_MIC_SD);
  if (!mic.begin(I2S_MODE_STD, TAXA_MIC, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO,
                 micDireito ? I2S_STD_SLOT_RIGHT : I2S_STD_SLOT_LEFT)) {
    Serial.println("[erro] o microfone I2S não iniciou");
    return false;
  }
  return true;
}

bool abrirDac(uint32_t taxa) {
  dac.setPins(PINO_DAC_BCK, PINO_DAC_LCK, PINO_DAC_DIN);
  if (!dac.begin(I2S_MODE_STD, taxa, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.println("[erro] o DAC I2S não iniciou");
    return false;
  }
  dacAberto = true;
  return true;
}

void escreverSilencio(uint32_t taxa, uint32_t ms) {
  static const int16_t zeros[256] = {};  // 128 quadros estéreo
  size_t quadros = taxa * ms / 1000;
  while (quadros > 0) {
    size_t k = quadros < 128 ? quadros : 128;
    dac.write((const uint8_t*)zeros, k * 4);
    quadros -= k;
  }
}

void guardarRelatorio() {
  const EstatisticasFala& e = controle.estatisticas();
  int n = snprintf(relatorio, sizeof(relatorio), "[áudio] %.1f s tocados; engasgos: %u", e.msTocados / 1000.0f,
                   (unsigned)e.engasgos);
  if (e.engasgos && n > 0 && n < (int)sizeof(relatorio)) {
    snprintf(relatorio + n, sizeof(relatorio) - n, " (%.2f s ao todo, o maior de %.2f s: a rede não entregou a tempo)",
             e.msEngasgos / 1000.0f, e.maiorEngasgo / 1000.0f);
  }
  temRelatorio = true;
}

// Só na tarefa do alto-falante. taxaPedida zera por último: quem espera !tocando() acha tudo limpo.
void encerrar() {
  if (dacAberto) {
    dac.end();  // desligado entre as falas, o DAC não chia
    dacAberto = false;
  }
  anel->limpar();
  fimPedido = false;
  taxaPedida = 0;
}

void rodarAltoFalante(void*) {
  static int16_t mono[512];
  static int16_t estereo[1024];
  uint32_t taxa = 0;
  bool falaAberta = false;
  for (;;) {
    if (calarPedido.load()) {
      encerrar();
      falaAberta = false;
      calarPedido = false;
      continue;
    }
    uint32_t pedida = taxaPedida.load();
    if (pedida == 0) {
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(5));
      continue;
    }
    if (!falaAberta) {
      controle.comecar(pedida, FOLGA_AUDIO_MS);
      falaAberta = true;
    }
    bool fim = fimPedido.load();  // lido antes do anel: o que chegou antes do fim já está nele
    size_t usado = anel->usado();
    if (!controle.podeTocar(usado, fim, millis())) {
      if (fim && usado < 2) {  // acabou de verdade
        if (dacAberto) escreverSilencio(taxa, SILENCIO_FINAL_MS);
        guardarRelatorio();
        encerrar();
        falaAberta = false;
        continue;
      }
      // Juntando folga (no começo, ou depois de faltar áudio): o DMA toca zeros sozinho enquanto isso
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(5));
      continue;
    }
    if (!dacAberto) {
      if (!abrirDac(pedida)) {
        encerrar();
        falaAberta = false;
        continue;
      }
      taxa = pedida;
    }
    size_t quero = usado & ~(size_t)1;
    if (quero > sizeof(mono)) quero = sizeof(mono);
    size_t n = anel->ler((uint8_t*)mono, quero) / 2;
    paraEstereo(mono, n, estereo, volumeAtual.load());
    dac.write((const uint8_t*)estereo, n * 4);
    controle.tocou(n * 2);
  }
}

}  // namespace

void comecar() {
  for (size_t tamanho : {88 * 1024, 64 * 1024, 48 * 1024, 32 * 1024, 16 * 1024}) {
    if (ESP.getFreeHeap() < tamanho + RESERVA_WIFI || ESP.getMaxAllocHeap() < tamanho) continue;
    uint8_t* memoria = (uint8_t*)malloc(tamanho);
    if (memoria) {
      anel = new Anel(memoria, tamanho);
      Serial.printf("[áudio] buffer de %u KB (%.1f s a 22050 Hz)\n", (unsigned)(tamanho / 1024), tamanho / 44100.0f);
      break;
    }
  }
  if (!anel) {
    Serial.println("[erro] sem memória para o áudio; reiniciando");
    delay(1000);
    ESP.restart();
  }
  abrirMic();
  xTaskCreatePinnedToCore(rodarAltoFalante, "alto-falante", 6144, nullptr, 3, &tarefa, 1);
}

size_t lerMicrofone(int16_t* pcm, size_t maximo) {
  static int32_t bruto[BLOCO_MIC];
  if (maximo > BLOCO_MIC) maximo = BLOCO_MIC;
  size_t n = mic.readBytes((char*)bruto, maximo * sizeof(int32_t)) / sizeof(int32_t);
  for (size_t i = 0; i < n; i++) pcm[i] = filtro.converter(bruto[i]);
  return n;
}

void iniciarFala(uint32_t taxa) {
  // A fala anterior pode estar tocando o finalzinho: espera um pouco; se não acabar, corta
  uint32_t inicio = millis();
  while (tocando() && millis() - inicio < 1500) delay(5);
  if (tocando()) calar();
  fimPedido = false;
  taxaPedida = taxa;
  xTaskNotifyGive(tarefa);
}

size_t espacoLivre() { return taxaPedida.load() ? anel->livre() : 65536; }

void empurrar(const uint8_t* dados, size_t n) {
  if (taxaPedida.load()) anel->escrever(dados, n);  // sem fala aberta (calada ou com erro), descarta
}

void terminarFala() {
  fimPedido = true;
  xTaskNotifyGive(tarefa);
}

void calar() {
  calarPedido = true;
  xTaskNotifyGive(tarefa);
  uint32_t inicio = millis();
  while (calarPedido.load() && millis() - inicio < 300) delay(2);
}

bool tocando() { return taxaPedida.load() != 0 || dacAberto.load(); }

void mostrarRelatorio() {
  if (temRelatorio.exchange(false)) Serial.println(relatorio);
}

size_t tamanhoBuffer() { return anel ? anel->usado() + anel->livre() + 1 : 0; }

void tocarTom() {
  const uint32_t taxa = 22050;
  const uint32_t total = taxa * 2;
  calar();
  iniciarFala(taxa);
  Serial.printf("[tom de 440 Hz por 2 s | BCK=%d LCK=%d DIN=%d]\n", PINO_DAC_BCK, PINO_DAC_LCK, PINO_DAC_DIN);
  int16_t bloco[256];
  for (uint32_t i = 0; i < total;) {
    size_t k = total - i < 256 ? total - i : 256;
    for (size_t j = 0; j < k; j++) bloco[j] = (int16_t)(8000.0f * sinf(2.0f * PI * 440.0f * (i + j) / taxa));
    size_t bytes = k * 2, feito = 0;
    while (feito < bytes) {  // o anel guarda ~1 s: o resto entra enquanto toca
      feito += anel->escrever((const uint8_t*)bloco + feito, bytes - feito);
      if (feito < bytes) delay(5);
    }
    i += k;
  }
  terminarFala();
  Serial.println("[se não ouviu o tom, confira a ligação do PCM5102 e o pino XSMT (mudo) em nível alto]");
}

// Lê os dois canais e mostra os valores crus: dá para ver se o microfone manda alguma coisa, em qual canal, e
// qual ganho usar.
void testarMicrofone() {
  static int32_t bruto[256];
  bool canais[2] = {false, true};
  int32_t melhor = 0;
  bool melhorDireito = micDireito;
  for (bool direito : canais) {
    micDireito = direito;
    abrirMic();
    Serial.printf("\n--- canal %s: fale algo por 3 s ---\n", direito ? "direito" : "esquerdo");
    mic.readBytes((char*)bruto, sizeof(bruto));  // o primeiro bloco depois de ligar vem torto
    int32_t pico = 0;
    uint32_t inicio = millis();
    while (millis() - inicio < 3000) {
      size_t n = mic.readBytes((char*)bruto, sizeof(bruto)) / sizeof(int32_t);
      for (size_t i = 0; i < n; i++) {
        int32_t v = bruto[i] >> 8;  // 24 bits
        if (v < 0) v = -v;
        if (v > pico) pico = v;
      }
      if (millis() - inicio < 100 && n >= 3) {
        Serial.printf("  amostras cruas: %08X %08X %08X\n", (unsigned)bruto[0], (unsigned)bruto[1], (unsigned)bruto[2]);
      }
    }
    Serial.printf("  pico: %d de 8388607\n", (int)pico);
    if (pico > melhor) {
      melhor = pico;
      melhorDireito = direito;
    }
  }
  micDireito = melhorDireito;
  abrirMic();
  if (melhor < 2000) {
    Serial.println("\n[nada chegou do microfone: confira VDD=3V3, GND, L/R=GND, SCK=26, WS=25 e SD=33]");
    return;
  }
  int extra = 0;  // quanto reduzir para a fala ficar perto de metade da escala
  while ((melhor >> extra) > 16000 && extra < 8) extra++;
  int deslocamento = 8 + extra;
  filtro.definirDeslocamento(deslocamento);
  Serial.printf("\n[ok: já estou usando o canal %s e o ganho %d. Para ficar assim depois de reiniciar, ponha no "
                "config.h: MIC_CANAL_DIREITO = %s e MIC_DESLOCAMENTO = %d]\n",
                micDireito ? "direito" : "esquerdo", deslocamento, micDireito ? "true" : "false", deslocamento);
}

void definirVolume(int v) { volumeAtual = v < 0 ? 0 : v > 100 ? 100 : v; }

int volume() { return volumeAtual.load(); }

}  // namespace som

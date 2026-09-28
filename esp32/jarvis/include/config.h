// Pinos e ajustes do firmware. O que é segredo (WiFi, IP e token) fica em segredos.h, fora do git.
#pragma once

#include <stdint.h>

// Nome desta placa na ponte: cada sala tem o seu histórico de conversa (até 64 letras, números, '.', '-', '_')
#define JARVIS_SALA "quarto"

// Microfone I2S INMP441: VDD=3V3, GND, L/R=GND, SCK=26, WS=25, SD=33
const int PINO_MIC_SCK = 26;
const int PINO_MIC_WS = 25;
const int PINO_MIC_SD = 33;
const bool MIC_CANAL_DIREITO = false;  // L/R no GND = esquerdo. Se o /mic mandar, troque para true
const int MIC_DESLOCAMENTO = 12;       // ganho: 16 = sem ganho, cada 1 a menos dobra o volume (o /mic sugere)

// DAC I2S PCM5102: BCK=27, LCK=14, DIN=13, SCK do módulo no GND
const int PINO_DAC_BCK = 27;
const int PINO_DAC_LCK = 14;
const int PINO_DAC_DIN = 13;

const int PINO_BOTAO = 0;  // BOOT da placa: segure para falar
const int PINO_LED = 2;    // LED da placa; -1 desliga

// Áudio da resposta. 0 = a taxa do Piper (22050 Hz, melhor som); 16000 = 27% menos dados na rede, se o
// "[áudio] ... engasgos" no Serial mostrar que o WiFi não dá conta.
const uint32_t SAIDA_TAXA = 0;
// Folga que o alto-falante junta antes de tocar, e de novo se faltar áudio no meio (mais = menos engasgo, mais
// atraso para começar)
const uint32_t FOLGA_AUDIO_MS = 250;

const int VOLUME_INICIAL = 80;          // 0 a 100; o /volume muda na hora
const uint32_t MAX_FALA_S = 20;         // fala mais longa (a ponte aceita até 30 s)
const uint32_t TOQUE_CURTO_MS = 300;    // um toque mais curto que isso só manda o Jarvis parar de falar

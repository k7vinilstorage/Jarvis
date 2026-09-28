// Microfone (INMP441) e alto-falante (PCM5102). O alto-falante toca numa tarefa própria, a partir de um anel que
// a rede enche: assim a leitura da rede e o botão nunca esperam o áudio tocar.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace som {

void comecar();

// Microfone: PCM 16 kHz, mono, 16 bits. Espera até ter o bloco (512 amostras = 32 ms).
size_t lerMicrofone(int16_t* pcm, size_t maximo);
void testarMicrofone();  // /mic

// Alto-falante: iniciarFala, empurrar os pedaços que chegam, terminarFala. calar() corta na hora.
void iniciarFala(uint32_t taxa);
size_t espacoLivre();
void empurrar(const uint8_t* dados, size_t n);
void terminarFala();
void calar();
bool tocando();
void tocarTom();  // /tom: 440 Hz por 2 s, sem rede

void definirVolume(int volume);
int volume();

}  // namespace som

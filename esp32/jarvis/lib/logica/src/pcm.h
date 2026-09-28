// Contas de áudio que não dependem do hardware: microfone -> PCM de 16 bits, e PCM -> os dois canais do DAC.
#pragma once

#include <cstddef>
#include <cstdint>

// O INMP441 entrega 24 bits alinhados à esquerda em palavras de 32. O deslocamento dá o ganho (16 = sem ganho;
// cada 1 a menos dobra o volume), e um passa-alta simples tira o nível DC do microfone.
class FiltroMic {
 public:
  explicit FiltroMic(int deslocamento) : deslocamento_(deslocamento) {}

  void definirDeslocamento(int d) { deslocamento_ = d; }

  int16_t converter(int32_t bruto) {
    float x = (float)(bruto >> deslocamento_);
    float y = x - xAnterior_ + 0.995f * yAnterior_;
    xAnterior_ = x;
    yAnterior_ = y;
    if (y > 32767.0f) return 32767;
    if (y < -32768.0f) return -32768;
    return (int16_t)y;
  }

 private:
  int deslocamento_;
  float xAnterior_ = 0;
  float yAnterior_ = 0;
};

// Mono -> estéreo (o PCM5102 toca os dois canais), com volume de 0 a 100.
inline void paraEstereo(const int16_t* mono, size_t n, int16_t* estereo, int volume) {
  for (size_t i = 0; i < n; i++) {
    int16_t v = (int16_t)((int32_t)mono[i] * volume / 100);
    estereo[2 * i] = v;
    estereo[2 * i + 1] = v;
  }
}

inline int picoAbsoluto(const int16_t* pcm, size_t n) {
  int pico = 0;
  for (size_t i = 0; i < n; i++) {
    int v = pcm[i] < 0 ? -(int)pcm[i] : pcm[i];
    if (v > pico) pico = v;
  }
  return pico;
}

// Quando tocar e quando esperar: o alto-falante só começa (e só volta, depois de faltar áudio no meio) com uma
// folga no anel. Sem isso, depois de uma falta ele tocava cada pedacinho que chegava e a voz saía picotada.
// Também conta os engasgos, para o Serial mostrar se a rede está dando conta.
#pragma once

#include <cstddef>
#include <cstdint>

struct EstatisticasFala {
  uint32_t msTocados = 0;
  uint32_t engasgos = 0;       // faltas curtas no meio da fala: a rede não entregou a tempo
  uint32_t msEngasgos = 0;
  uint32_t maiorEngasgo = 0;
  uint32_t pausas = 0;         // faltas longas: o Hermes pensando entre o "Um momento." e a resposta
};

class ControleDeFala {
 public:
  static const uint32_t PAUSA_MS = 1500;  // falta maior que isso é pausa do Hermes, não engasgo

  void comecar(uint32_t taxa, uint32_t folgaMs) {
    bytesPorMs_ = taxa * 2 / 1000.0f;
    alvo_ = (size_t)(bytesPorMs_ * folgaMs) & ~(size_t)1;
    juntando_ = true;
    emFalta_ = false;
    est_ = EstatisticasFala();
    bytesTocados_ = 0;
  }

  // Com o que há no anel agora: true = pode tocar.
  bool podeTocar(size_t usado, bool fim, uint32_t agoraMs) {
    if (juntando_) {
      if (usado < alvo_ && !(fim && usado >= 2)) return false;
      juntando_ = false;
      if (emFalta_) {
        uint32_t duracao = agoraMs - faltaDesde_;
        if (duracao >= PAUSA_MS) {
          est_.pausas++;
        } else {
          est_.engasgos++;
          est_.msEngasgos += duracao;
          if (duracao > est_.maiorEngasgo) est_.maiorEngasgo = duracao;
        }
        emFalta_ = false;
      }
      return true;
    }
    if (usado >= 2) return true;
    if (!fim) {  // acabou no meio: junta a folga de novo antes de voltar
      juntando_ = true;
      emFalta_ = true;
      faltaDesde_ = agoraMs;
    }
    return false;
  }

  void tocou(size_t bytes) {
    bytesTocados_ += bytes;
    est_.msTocados = (uint32_t)(bytesTocados_ / bytesPorMs_);
  }

  const EstatisticasFala& estatisticas() const { return est_; }

 private:
  float bytesPorMs_ = 44.1f;
  size_t alvo_ = 0;
  bool juntando_ = true;
  bool emFalta_ = false;
  uint32_t faltaDesde_ = 0;
  uint64_t bytesTocados_ = 0;
  EstatisticasFala est_;
};

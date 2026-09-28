// O LED da placa mostra o que o Jarvis está fazendo. Só acende e apaga, então cada estado é um jeito de piscar.
#pragma once

#include <cstdint>

#include "mensagens.h"

enum class Luz {
  Apagada,      // pronto, esperando o botão
  Acesa,        // ouvindo (botão apertado)
  PiscaRapido,  // transcrevendo ou pensando
  PiscaLento,   // falando
  PiscaDuplo,   // aguardando: outra sala está usando o modelo
  Erro,         // algo deu errado: pisca bem rápido por um instante
  SemConexao,   // sem WiFi ou sem a ponte: uma piscada curta a cada 2 s
};

inline Luz escolherLuz(bool conectado, bool gravando, bool tocando, bool erroRecente, Estado estado) {
  if (!conectado) return Luz::SemConexao;
  if (gravando) return Luz::Acesa;
  if (erroRecente) return Luz::Erro;
  if (tocando) return Luz::PiscaLento;
  switch (estado) {
    case Estado::Ouvindo:
      return Luz::Acesa;
    case Estado::Transcrevendo:
    case Estado::Pensando:
      return Luz::PiscaRapido;
    case Estado::Aguardando:
      return Luz::PiscaDuplo;
    case Estado::Falando:
      return Luz::PiscaLento;
    default:
      return Luz::Apagada;
  }
}

inline bool luzAcesa(Luz luz, uint32_t ms) {
  switch (luz) {
    case Luz::Acesa:
      return true;
    case Luz::PiscaRapido:
      return (ms / 125) % 2 == 0;
    case Luz::PiscaLento:
      return (ms / 500) % 2 == 0;
    case Luz::PiscaDuplo: {
      uint32_t fase = ms % 1200;
      return fase < 100 || (fase >= 250 && fase < 350);
    }
    case Luz::Erro:
      return (ms / 60) % 2 == 0;
    case Luz::SemConexao:
      return ms % 2000 < 80;
    default:
      return false;
  }
}

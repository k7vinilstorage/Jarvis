// WiFi e a conexão WebSocket com a ponte jarvis-voz: conecta, reconecta sozinho, lê e manda os quadros.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <string>

namespace rede {

struct Eventos {
  void (*mensagem)(const char* json, size_t n, bool cortada);  // mensagem de texto da ponte
  void (*caiu)(const char* motivo);                            // a conexão com a ponte fechou
  size_t (*espacoAudio)();                                     // quanto áudio cabe agora
  void (*audio)(const uint8_t* dados, size_t n);               // pedaço de áudio da resposta
};

void comecar(const Eventos& eventos);
void manter();  // no loop: WiFi, (re)conexão com a ponte e leitura
bool conectada();
bool enviarTexto(const std::string& json);
bool enviarAudio(const int16_t* pcm, size_t amostras);
void mostrarEstado();

}  // namespace rede

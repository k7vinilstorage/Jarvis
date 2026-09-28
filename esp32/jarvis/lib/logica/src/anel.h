// Anel de bytes entre duas tarefas: a rede escreve o áudio que chega, o alto-falante lê e toca.
// Sem trava: um produtor e um consumidor, cada um só mexe no seu índice. Cabem tamanho - 1 bytes.
#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>

class Anel {
 public:
  Anel(uint8_t* memoria, size_t tamanho) : buf_(memoria), tam_(tamanho) {}

  size_t usado() const {
    size_t e = escrita_.load(std::memory_order_acquire);
    size_t l = leitura_.load(std::memory_order_acquire);
    return (e + tam_ - l) % tam_;
  }

  size_t livre() const { return tam_ - 1 - usado(); }

  // Só o produtor. Escreve o que couber e devolve quanto escreveu.
  size_t escrever(const uint8_t* dados, size_t n) {
    size_t e = escrita_.load(std::memory_order_relaxed);
    size_t l = leitura_.load(std::memory_order_acquire);
    size_t cabe = (l + tam_ - e - 1) % tam_;
    if (n > cabe) n = cabe;
    size_t primeiro = n < tam_ - e ? n : tam_ - e;
    memcpy(buf_ + e, dados, primeiro);
    memcpy(buf_, dados + primeiro, n - primeiro);
    escrita_.store((e + n) % tam_, std::memory_order_release);
    return n;
  }

  // Só o consumidor. Lê até n bytes e devolve quanto leu.
  size_t ler(uint8_t* destino, size_t n) {
    size_t l = leitura_.load(std::memory_order_relaxed);
    size_t e = escrita_.load(std::memory_order_acquire);
    size_t tem = (e + tam_ - l) % tam_;
    if (n > tem) n = tem;
    size_t primeiro = n < tam_ - l ? n : tam_ - l;
    memcpy(destino, buf_ + l, primeiro);
    memcpy(destino + primeiro, buf_, n - primeiro);
    leitura_.store((l + n) % tam_, std::memory_order_release);
    return n;
  }

  // Só o consumidor: descarta o que ainda não foi lido.
  void limpar() { leitura_.store(escrita_.load(std::memory_order_acquire), std::memory_order_release); }

 private:
  uint8_t* buf_;
  size_t tam_;
  std::atomic<size_t> escrita_{0};
  std::atomic<size_t> leitura_{0};
};

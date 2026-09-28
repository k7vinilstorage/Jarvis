#include "websocket.h"

#include <cstring>

namespace ws {

size_t montarCabecalho(uint8_t* saida, uint8_t op, size_t tamanho, const uint8_t mascara[4]) {
  saida[0] = 0x80 | (op & 0x0F);
  size_t i;
  if (tamanho < 126) {
    saida[1] = 0x80 | (uint8_t)tamanho;
    i = 2;
  } else if (tamanho <= 0xFFFF) {
    saida[1] = 0x80 | 126;
    saida[2] = (uint8_t)(tamanho >> 8);
    saida[3] = (uint8_t)tamanho;
    i = 4;
  } else {
    saida[1] = 0x80 | 127;
    uint64_t t = tamanho;
    for (int k = 0; k < 8; k++) saida[2 + k] = (uint8_t)(t >> (8 * (7 - k)));
    i = 10;
  }
  memcpy(saida + i, mascara, 4);
  return i + 4;
}

void mascarar(uint8_t* dados, size_t n, const uint8_t mascara[4], size_t deslocamento) {
  for (size_t i = 0; i < n; i++) dados[i] ^= mascara[(deslocamento + i) & 3];
}

std::string base64(const uint8_t* dados, size_t n) {
  static const char tabela[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string saida;
  saida.reserve((n + 2) / 3 * 4);
  for (size_t i = 0; i < n; i += 3) {
    uint32_t v = (uint32_t)dados[i] << 16;
    if (i + 1 < n) v |= (uint32_t)dados[i + 1] << 8;
    if (i + 2 < n) v |= dados[i + 2];
    saida += tabela[(v >> 18) & 63];
    saida += tabela[(v >> 12) & 63];
    saida += i + 1 < n ? tabela[(v >> 6) & 63] : '=';
    saida += i + 2 < n ? tabela[v & 63] : '=';
  }
  return saida;
}

std::string pedidoDeAbertura(const char* host, uint16_t porta, const std::string& caminho, const std::string& chave,
                             const char* token) {
  std::string p = "GET " + caminho + " HTTP/1.1\r\n";
  p += "Host: " + std::string(host) + ":" + std::to_string(porta) + "\r\n";
  p += "Upgrade: websocket\r\nConnection: Upgrade\r\n";
  p += "Sec-WebSocket-Key: " + chave + "\r\nSec-WebSocket-Version: 13\r\n";
  p += "Authorization: Bearer " + std::string(token) + "\r\n\r\n";
  return p;
}

int statusHttp(const std::string& linha) {
  if (linha.compare(0, 5, "HTTP/") != 0) return -1;
  size_t espaco = linha.find(' ');
  if (espaco == std::string::npos || espaco + 4 > linha.size()) return -1;
  int status = 0;
  for (size_t i = espaco + 1; i < espaco + 4; i++) {
    if (linha[i] < '0' || linha[i] > '9') return -1;
    status = status * 10 + (linha[i] - '0');
  }
  return status;
}

Leitor::Leitor(char* bufTexto, size_t tamTexto, Ouvinte& ouvinte)
    : txt_(bufTexto), txtCap_(tamTexto), ouvinte_(ouvinte) {
  reiniciar();
}

void Leitor::reiniciar() {
  fase_ = Cabecalho;
  cabLidos_ = 0;
  cabPrecisa_ = 2;
  opQuadro_ = 0;
  fin_ = false;
  restante_ = 0;
  opMensagem_ = 0;
  txtN_ = 0;
  txtCortada_ = false;
  ctrlN_ = 0;
  erro_ = nullptr;
}

size_t Leitor::quantoAceita(size_t espacoBinario) const {
  if (erro_) return 0;
  if (fase_ == Cabecalho) return cabPrecisa_ - cabLidos_;
  size_t resto = restante_ > 65536 ? 65536 : (size_t)restante_;
  if (opQuadro_ < 8 && opMensagem_ == Binario && espacoBinario < resto) return espacoBinario;
  return resto;
}

void Leitor::alimentar(const uint8_t* dados, size_t n) {
  while (n > 0 && !erro_) {
    if (fase_ == Cabecalho) {
      size_t k = cabPrecisa_ - cabLidos_;
      if (k > n) k = n;
      memcpy(cab_ + cabLidos_, dados, k);
      cabLidos_ += k;
      dados += k;
      n -= k;
      if (cabLidos_ == 2) {
        if (cab_[0] & 0x70) return falhar("o servidor usou uma extensão que não foi pedida");
        if (cab_[1] & 0x80) return falhar("o servidor mascarou um quadro");
        uint8_t len7 = cab_[1] & 0x7F;
        cabPrecisa_ = 2 + (len7 == 126 ? 2 : len7 == 127 ? 8 : 0);
      }
      if (cabLidos_ == cabPrecisa_) cabecalhoCompleto();
      continue;
    }
    size_t k = restante_ < n ? (size_t)restante_ : n;
    if (opQuadro_ >= 8) {
      memcpy(ctrl_ + ctrlN_, dados, k);
      ctrlN_ += k;
    } else if (opMensagem_ == Binario) {
      if (k) ouvinte_.binario(dados, k);
    } else {
      size_t cabe = txtCap_ - 1 - txtN_;
      size_t c = k < cabe ? k : cabe;
      memcpy(txt_ + txtN_, dados, c);
      txtN_ += c;
      if (c < k) txtCortada_ = true;
    }
    dados += k;
    n -= k;
    restante_ -= k;
    if (restante_ == 0) terminarQuadro();
  }
}

void Leitor::cabecalhoCompleto() {
  fin_ = cab_[0] & 0x80;
  opQuadro_ = cab_[0] & 0x0F;
  uint8_t len7 = cab_[1] & 0x7F;
  if (len7 == 126) {
    restante_ = ((uint64_t)cab_[2] << 8) | cab_[3];
  } else if (len7 == 127) {
    restante_ = 0;
    for (int k = 0; k < 8; k++) restante_ = (restante_ << 8) | cab_[2 + k];
  } else {
    restante_ = len7;
  }

  if (opQuadro_ == Fechar || opQuadro_ == Ping || opQuadro_ == Pong) {
    if (!fin_ || restante_ > sizeof(ctrl_)) return falhar("quadro de controle inválido");
    ctrlN_ = 0;
  } else if (opQuadro_ == Continuacao) {
    if (!opMensagem_) return falhar("continuação sem mensagem aberta");
  } else if (opQuadro_ == Texto || opQuadro_ == Binario) {
    if (opMensagem_) return falhar("mensagem nova antes do fim da anterior");
    opMensagem_ = opQuadro_;
    txtN_ = 0;
    txtCortada_ = false;
  } else {
    return falhar("tipo de quadro desconhecido");
  }

  fase_ = Carga;
  cabLidos_ = 0;
  cabPrecisa_ = 2;
  if (restante_ == 0) terminarQuadro();
}

void Leitor::terminarQuadro() {
  fase_ = Cabecalho;
  if (opQuadro_ >= 8) {
    ouvinte_.controle(opQuadro_, ctrl_, ctrlN_);
    return;
  }
  if (!fin_) return;
  if (opMensagem_ == Texto) {
    txt_[txtN_] = '\0';
    ouvinte_.texto(txt_, txtN_, txtCortada_);
  }
  opMensagem_ = 0;
}

}  // namespace ws

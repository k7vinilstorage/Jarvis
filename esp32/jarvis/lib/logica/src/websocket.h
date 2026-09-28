// O pedaço do WebSocket (RFC 6455) que o Jarvis usa, sem depender da rede: montar os quadros do cliente e ler
// os do servidor aos poucos. A conexão em si (WiFiClient) fica em src/rede.cpp.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace ws {

enum Op : uint8_t { Continuacao = 0x0, Texto = 0x1, Binario = 0x2, Fechar = 0x8, Ping = 0x9, Pong = 0xA };

const size_t MAX_CABECALHO = 14;

// Cabeçalho de um quadro do cliente (sempre mascarado, sempre com FIN). Devolve quantos bytes usou (6 a 14).
size_t montarCabecalho(uint8_t* saida, uint8_t op, size_t tamanho, const uint8_t mascara[4]);

// Aplica a máscara no lugar. deslocamento: posição do primeiro byte dentro da carga (para mascarar em pedaços).
void mascarar(uint8_t* dados, size_t n, const uint8_t mascara[4], size_t deslocamento = 0);

std::string base64(const uint8_t* dados, size_t n);

// Pedido HTTP que abre o WebSocket. chave: 16 bytes aleatórios em base64.
std::string pedidoDeAbertura(const char* host, uint16_t porta, const std::string& caminho, const std::string& chave,
                             const char* token);

// "HTTP/1.1 101 Switching Protocols" -> 101; linha inválida -> -1
int statusHttp(const std::string& linha);

class Ouvinte {
 public:
  virtual ~Ouvinte() = default;
  // Mensagem de texto completa (terminada em '\0'). cortada: passou do buffer; só o começo chegou.
  virtual void texto(const char* dados, size_t n, bool cortada) = 0;
  // Pedaço de uma mensagem binária (o áudio), entregue assim que chega.
  virtual void binario(const uint8_t* dados, size_t n) = 0;
  // Quadro de controle completo: Fechar, Ping ou Pong.
  virtual void controle(uint8_t op, const uint8_t* dados, size_t n) = 0;
};

// Lê os quadros do servidor aos poucos, sem guardar o áudio: cada pedaço binário vai direto para o Ouvinte.
class Leitor {
 public:
  Leitor(char* bufTexto, size_t tamTexto, Ouvinte& ouvinte);

  void reiniciar();

  // Quantos bytes ler agora. No meio de uma mensagem binária, no máximo espacoBinario: assim o leitor nunca
  // recebe áudio que não cabe, e o que sobra espera no TCP (o servidor manda no ritmo em que a gente lê).
  size_t quantoAceita(size_t espacoBinario) const;

  void alimentar(const uint8_t* dados, size_t n);

  bool comErro() const { return erro_ != nullptr; }
  const char* erro() const { return erro_; }

 private:
  enum Fase { Cabecalho, Carga };

  void cabecalhoCompleto();
  void terminarQuadro();
  void falhar(const char* motivo) { erro_ = motivo; }

  char* txt_;
  size_t txtCap_;
  Ouvinte& ouvinte_;

  Fase fase_;
  uint8_t cab_[MAX_CABECALHO];
  size_t cabLidos_;
  size_t cabPrecisa_;
  uint8_t opQuadro_;     // op do quadro atual
  bool fin_;
  uint64_t restante_;    // bytes da carga do quadro atual que faltam
  uint8_t opMensagem_;   // Texto ou Binario enquanto uma mensagem (talvez em pedaços) está aberta; 0 fora dela
  size_t txtN_;
  bool txtCortada_;
  uint8_t ctrl_[125];
  size_t ctrlN_;
  const char* erro_;
};

}  // namespace ws

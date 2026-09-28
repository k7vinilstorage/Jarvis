// Cliente de teste (teste-ponte/rodar.sh): usa o mesmo código do firmware (ws::, mensagens) contra a ponte
// jarvis-voz de verdade, rodando com Whisper, Piper e Hermes falsos. Imita a leitura do firmware: só lê o áudio que cabe num anel de
// 48 KB que esvazia no ritmo em que tocaria.
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <random>
#include <string>
#include <vector>

#include "mensagens.h"
#include "websocket.h"

static int falhas = 0;
#define CONFERE(cond, ...)                              \
  do {                                                  \
    if (cond) {                                         \
      printf("  ok: " __VA_ARGS__);                     \
    } else {                                            \
      printf("  FALHOU (%s): ", #cond);                 \
      printf(__VA_ARGS__);                              \
      falhas++;                                         \
    }                                                   \
    printf("\n");                                       \
  } while (0)

static double agora() {
  using namespace std::chrono;
  return duration<double>(steady_clock::now().time_since_epoch()).count();
}

static std::mt19937 aleatorio(1234);

static bool enviarQuadro(int fd, uint8_t op, const uint8_t* dados, size_t n) {
  uint8_t mascara[4];
  for (auto& b : mascara) b = (uint8_t)aleatorio();
  std::vector<uint8_t> q(ws::MAX_CABECALHO + n);
  size_t h = ws::montarCabecalho(q.data(), op, n, mascara);
  memcpy(q.data() + h, dados, n);
  ws::mascarar(q.data() + h, n, mascara);
  return send(fd, q.data(), h + n, 0) == (ssize_t)(h + n);
}

static bool enviarTexto(int fd, const std::string& s) {
  return enviarQuadro(fd, ws::Texto, (const uint8_t*)s.data(), s.size());
}

struct Coletor : ws::Ouvinte {
  int fd = -1;
  std::vector<Evento> eventos;
  size_t bytesAudio = 0;
  size_t maiorPedaco = 0;
  bool fechou = false;
  uint16_t codigo = 0;
  // anel simulado: enche com o áudio, esvazia a 22050 Hz x 2 bytes enquanto "toca"
  double nivel = 0, ultimo = agora();
  bool tocando = false;

  size_t espaco() {
    double t = agora();
    if (tocando) nivel = std::max(0.0, nivel - (t - ultimo) * 44100);
    ultimo = t;
    return (size_t)(48 * 1024 - 1 - nivel);
  }
  void texto(const char* dados, size_t n, bool cortada) override {
    Evento ev;
    if (cortada) ev.tipo = tipoDoComeco(dados, n);
    else interpretar(dados, n, ev);
    eventos.push_back(ev);
  }
  void binario(const uint8_t*, size_t n) override {
    bytesAudio += n;
    nivel += n;
    maiorPedaco = std::max(maiorPedaco, n);
    tocando = true;
  }
  void controle(uint8_t op, const uint8_t* dados, size_t n) override {
    if (op == ws::Ping) enviarQuadro(fd, ws::Pong, dados, n);
    if (op == ws::Fechar) {
      fechou = true;
      codigo = n >= 2 ? (dados[0] << 8) | dados[1] : 1005;
    }
  }
};

struct Conexao {
  int fd = -1;
  int status = -1;
  char buf[8192];
  Coletor col;
  ws::Leitor leitor{buf, sizeof(buf), col};
  size_t lidoDesde = 0;

  ~Conexao() {
    if (fd >= 0) close(fd);
  }

  bool lerLinha(std::string& linha) {
    linha.clear();
    char c;
    while (recv(fd, &c, 1, 0) == 1) {
      if (c == '\n') {
        if (!linha.empty() && linha.back() == '\r') linha.pop_back();
        return true;
      }
      linha += c;
    }
    return false;
  }

  int abrir(int porta, const char* token, const char* sala) {
    fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in end{};
    end.sin_family = AF_INET;
    end.sin_port = htons(porta);
    inet_pton(AF_INET, "127.0.0.1", &end.sin_addr);
    if (connect(fd, (sockaddr*)&end, sizeof(end)) != 0) return -1;
    int um = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &um, sizeof(um));
    timeval tv{5, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    uint8_t chave[16];
    for (auto& b : chave) b = (uint8_t)aleatorio();
    std::string pedido = ws::pedidoDeAbertura("127.0.0.1", porta, std::string("/voz?sala=") + sala,
                                              ws::base64(chave, 16), token);
    send(fd, pedido.data(), pedido.size(), 0);
    std::string linha;
    status = lerLinha(linha) ? ws::statusHttp(linha) : -1;
    while (lerLinha(linha) && !linha.empty()) {
    }
    return status;
  }

  // Lê como o firmware, até cond() ficar verdadeira ou o tempo acabar
  bool ate(const std::function<bool()>& cond, double segundos) {
    double fim = agora() + segundos;
    while (agora() < fim) {
      if (cond()) return true;
      pollfd p{fd, POLLIN, 0};
      size_t quero = leitor.quantoAceita(col.espaco());
      if (quero == 0) {  // anel cheio: espera tocar um pouco
        usleep(5000);
        continue;
      }
      if (poll(&p, 1, 20) <= 0) continue;
      uint8_t tmp[1024];
      if (quero > sizeof(tmp)) quero = sizeof(tmp);
      ssize_t n = recv(fd, tmp, quero, 0);
      if (n <= 0) return cond();
      leitor.alimentar(tmp, (size_t)n);
      if (leitor.comErro()) {
        printf("  erro no leitor: %s\n", leitor.erro());
        return false;
      }
    }
    return cond();
  }

  const Evento* achar(Tipo t, size_t desde = 0) const {
    for (size_t i = desde; i < col.eventos.size(); i++) {
      if (col.eventos[i].tipo == t) return &col.eventos[i];
    }
    return nullptr;
  }
};

static std::vector<uint8_t> tom(double segundos) {
  std::vector<uint8_t> pcm;
  for (int i = 0; i < (int)(segundos * 16000); i++) {
    int16_t v = (int16_t)(8000 * sin(2 * M_PI * 300 * i / 16000.0));
    pcm.push_back((uint8_t)(v & 0xFF));
    pcm.push_back((uint8_t)((uint16_t)v >> 8));
  }
  return pcm;
}

int main(int argc, char** argv) {
  if (argc < 3) {
    fprintf(stderr, "uso: cliente PORTA TOKEN (use o teste-ponte/rodar.sh)\n");
    return 2;
  }
  int porta = atoi(argv[1]);
  const char* token = argv[2];

  printf("1. token errado\n");
  {
    Conexao c;
    int s = c.abrir(porta, "errado", "quarto");
    CONFERE(s == 401, "HTTP %d", s);
  }
  printf("2. sala inválida\n");
  {
    Conexao c;
    int s = c.abrir(porta, token, "sala!");
    CONFERE(s == 400, "HTTP %d", s);
  }

  Conexao c;
  printf("3. abertura com o token no cabeçalho\n");
  int s = c.abrir(porta, token, "quarto");
  CONFERE(s == 101, "HTTP %d", s);
  c.col.fd = c.fd;
  CONFERE(c.ate([&] { return c.achar(Tipo::Pronto) != nullptr; }, 3), "chegou o pronto");

  printf("4. ping da aplicação\n");
  enviarTexto(c.fd, mensagemSimples("ping"));
  CONFERE(c.ate([&] { return c.achar(Tipo::Pong) != nullptr; }, 3), "chegou o pong");

  printf("5. pergunta escrita, com acento, e a resposta falada\n");
  size_t desde = c.col.eventos.size();
  enviarTexto(c.fd, mensagemComTexto("texto", "que horas são amanhã às 7h?"));
  bool fim = c.ate([&] { return c.achar(Tipo::Fim, desde) != nullptr; }, 20);
  CONFERE(fim, "chegou o fim");
  const Evento* t = c.achar(Tipo::Transcricao, desde);
  CONFERE(t && t->texto == "que horas são amanhã às 7h?", "transcrição: %s", t ? t->texto.c_str() : "-");
  const Evento* f = c.achar(Tipo::Ferramenta, desde);
  CONFERE(f && f->texto == "hora", "ferramenta: %s", f ? f->texto.c_str() : "-");
  const Evento* ai = c.achar(Tipo::AudioInicio, desde);
  CONFERE(ai && ai->taxa == 22050 && ai->formato == "s16le" && ai->canais == 1, "audio_inicio: %u Hz %s %d canal",
          ai ? ai->taxa : 0, ai ? ai->formato.c_str() : "-", ai ? ai->canais : 0);
  CONFERE(c.achar(Tipo::AudioFim, desde) != nullptr, "audio_fim antes do fim");
  CONFERE(c.col.bytesAudio > 0 && c.col.bytesAudio % 2 == 0, "%zu bytes de áudio (maior pedaço %zu)",
          c.col.bytesAudio, c.col.maiorPedaco);
  for (size_t i = desde; i < c.col.eventos.size(); i++) {
    if (c.col.eventos[i].tipo == Tipo::Frase) printf("     frase: %s\n", c.col.eventos[i].texto.c_str());
  }
  const Evento* fe = c.achar(Tipo::Fim, desde);
  CONFERE(fe && fe->erro.empty() && !fe->tempos.empty(), "fim sem erro, tempos: %s", fe ? fe->tempos.c_str() : "-");

  printf("6. pergunta falada: inicio, 1,5 s de áudio em quadros de 1 KB, fim\n");
  desde = c.col.eventos.size();
  c.col.bytesAudio = 0;
  enviarTexto(c.fd, mensagemSimples("inicio"));
  std::vector<uint8_t> pcm = tom(1.5);
  for (size_t i = 0; i < pcm.size(); i += 1024) {
    enviarQuadro(c.fd, ws::Binario, pcm.data() + i, std::min((size_t)1024, pcm.size() - i));
  }
  enviarTexto(c.fd, mensagemSimples("fim"));
  fim = c.ate([&] { return c.achar(Tipo::Fim, desde) != nullptr; }, 20);
  CONFERE(fim, "chegou o fim");
  t = c.achar(Tipo::Transcricao, desde);
  CONFERE(t && t->texto == "Que horas são?", "o Whisper falso ouviu: %s", t ? t->texto.c_str() : "-");
  CONFERE(c.col.bytesAudio > 0, "%zu bytes de áudio", c.col.bytesAudio);

  printf("7. falar por cima: 'inicio' no meio da resposta corta a fala\n");
  desde = c.col.eventos.size();
  enviarTexto(c.fd, mensagemComTexto("texto", "e amanhã?"));
  CONFERE(c.ate([&] { return c.achar(Tipo::AudioInicio, desde) != nullptr; }, 20), "a resposta começou a tocar");
  size_t antes = c.col.eventos.size();
  enviarTexto(c.fd, mensagemSimples("inicio"));
  c.ate([&] { return c.achar(Tipo::Fim, antes) != nullptr; }, 5);
  fe = c.achar(Tipo::Fim, antes);
  CONFERE(fe && fe->erro == "interrompido", "fim com erro: %s", fe ? fe->erro.c_str() : "-");
  CONFERE(c.achar(Tipo::AudioFim, antes) != nullptr, "audio_fim fechou o par");
  size_t antesCancelar = c.col.eventos.size();
  enviarTexto(c.fd, mensagemSimples("cancelar"));  // o toque curto do botão
  auto pronto = [&] {
    for (size_t i = antesCancelar; i < c.col.eventos.size(); i++) {
      if (c.col.eventos[i].tipo == Tipo::Estado && c.col.eventos[i].estado == Estado::Pronto) return true;
    }
    return false;
  };
  CONFERE(c.ate(pronto, 3), "depois do cancelar, a ponte voltou a pronto");

  printf("8. falar (teste do alto-falante) e conversa nova\n");
  desde = c.col.eventos.size();
  enviarTexto(c.fd, mensagemComTexto("falar", "Teste do alto-falante."));
  CONFERE(c.ate([&] { return c.achar(Tipo::Fim, desde) != nullptr; }, 10), "fim do falar");
  enviarTexto(c.fd, mensagemSimples("nova"));
  CONFERE(c.ate([&] { return c.achar(Tipo::Nova, desde) != nullptr; }, 3), "conversa nova");
  CONFERE(!c.col.fechou, "a conexão continua aberta");

  printf("%s\n", falhas ? "HOUVE FALHAS" : "TUDO CERTO");
  return falhas ? 1 : 0;
}

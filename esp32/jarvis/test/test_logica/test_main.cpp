// Testes da lógica do firmware, no PC (pio test -e nativo). Não testam o hardware nem a rede de verdade.
#include <unity.h>

#include <cstring>
#include <string>
#include <vector>

#include "anel.h"
#include "fala.h"
#include "led.h"
#include "mensagens.h"
#include "pcm.h"
#include "websocket.h"

void setUp() {}
void tearDown() {}

// ---------------------------------------------------------------- anel

void test_anel_escreve_e_le_dando_a_volta() {
  uint8_t memoria[8];
  Anel anel(memoria, sizeof(memoria));
  TEST_ASSERT_EQUAL(7, anel.livre());
  const uint8_t a[] = {1, 2, 3, 4, 5};
  TEST_ASSERT_EQUAL(5, anel.escrever(a, 5));
  uint8_t lido[8];
  TEST_ASSERT_EQUAL(3, anel.ler(lido, 3));
  TEST_ASSERT_EQUAL_UINT8_ARRAY(a, lido, 3);
  const uint8_t b[] = {6, 7, 8, 9, 10};  // passa do fim da memória e volta ao começo
  TEST_ASSERT_EQUAL(5, anel.escrever(b, 5));
  TEST_ASSERT_EQUAL(7, anel.usado());
  TEST_ASSERT_EQUAL(0, anel.livre());
  TEST_ASSERT_EQUAL(7, anel.ler(lido, 8));
  const uint8_t esperado[] = {4, 5, 6, 7, 8, 9, 10};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(esperado, lido, 7);
  TEST_ASSERT_EQUAL(0, anel.usado());
}

void test_anel_cheio_escreve_so_o_que_cabe() {
  uint8_t memoria[4];
  Anel anel(memoria, sizeof(memoria));
  const uint8_t a[] = {1, 2, 3, 4, 5};
  TEST_ASSERT_EQUAL(3, anel.escrever(a, 5));
  TEST_ASSERT_EQUAL(0, anel.escrever(a, 1));
}

void test_anel_limpar_descarta_o_que_falta() {
  uint8_t memoria[16];
  Anel anel(memoria, sizeof(memoria));
  const uint8_t a[] = {1, 2, 3};
  anel.escrever(a, 3);
  anel.limpar();
  TEST_ASSERT_EQUAL(0, anel.usado());
  anel.escrever(a, 2);
  uint8_t lido[4];
  TEST_ASSERT_EQUAL(2, anel.ler(lido, 4));
  TEST_ASSERT_EQUAL_UINT8_ARRAY(a, lido, 2);
}

// ---------------------------------------------------------------- websocket: quadros do cliente

void test_cabecalho_curto_com_mascara_da_rfc() {
  // RFC 6455, 5.7: "Hello" mascarado com 37 fa 21 3d
  const uint8_t mascara[4] = {0x37, 0xfa, 0x21, 0x3d};
  uint8_t quadro[ws::MAX_CABECALHO + 5];
  size_t n = ws::montarCabecalho(quadro, ws::Texto, 5, mascara);
  memcpy(quadro + n, "Hello", 5);
  ws::mascarar(quadro + n, 5, mascara);
  const uint8_t esperado[] = {0x81, 0x85, 0x37, 0xfa, 0x21, 0x3d, 0x7f, 0x9f, 0x4d, 0x51, 0x58};
  TEST_ASSERT_EQUAL(sizeof(esperado), n + 5);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(esperado, quadro, sizeof(esperado));
}

void test_cabecalho_de_16_e_64_bits() {
  const uint8_t mascara[4] = {1, 2, 3, 4};
  uint8_t cab[ws::MAX_CABECALHO];
  TEST_ASSERT_EQUAL(8, ws::montarCabecalho(cab, ws::Binario, 1024, mascara));
  const uint8_t esperado16[] = {0x82, 0xFE, 0x04, 0x00, 1, 2, 3, 4};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(esperado16, cab, 8);
  TEST_ASSERT_EQUAL(14, ws::montarCabecalho(cab, ws::Binario, 70000, mascara));
  const uint8_t esperado64[] = {0x82, 0xFF, 0, 0, 0, 0, 0, 0x01, 0x11, 0x70, 1, 2, 3, 4};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(esperado64, cab, 14);
  // 125 ainda cabe no byte curto; 126 já usa 16 bits
  TEST_ASSERT_EQUAL(6, ws::montarCabecalho(cab, ws::Texto, 125, mascara));
  TEST_ASSERT_EQUAL(8, ws::montarCabecalho(cab, ws::Texto, 126, mascara));
}

void test_mascarar_em_pedacos_da_o_mesmo_resultado() {
  const uint8_t mascara[4] = {0x37, 0xfa, 0x21, 0x3d};
  uint8_t inteiro[] = {'a', 'b', 'c', 'd', 'e', 'f', 'g'};
  uint8_t pedacos[] = {'a', 'b', 'c', 'd', 'e', 'f', 'g'};
  ws::mascarar(inteiro, 7, mascara);
  ws::mascarar(pedacos, 3, mascara, 0);
  ws::mascarar(pedacos + 3, 4, mascara, 3);
  TEST_ASSERT_EQUAL_UINT8_ARRAY(inteiro, pedacos, 7);
}

void test_base64() {
  const uint8_t foo[] = {'f', 'o', 'o', 'b', 'a', 'r'};
  TEST_ASSERT_EQUAL_STRING("", ws::base64(foo, 0).c_str());
  TEST_ASSERT_EQUAL_STRING("Zg==", ws::base64(foo, 1).c_str());
  TEST_ASSERT_EQUAL_STRING("Zm8=", ws::base64(foo, 2).c_str());
  TEST_ASSERT_EQUAL_STRING("Zm9v", ws::base64(foo, 3).c_str());
  TEST_ASSERT_EQUAL_STRING("Zm9vYmFy", ws::base64(foo, 6).c_str());
  // A chave de exemplo da RFC 6455 (16 bytes: "the sample nonce")
  TEST_ASSERT_EQUAL_STRING("dGhlIHNhbXBsZSBub25jZQ==",
                           ws::base64((const uint8_t*)"the sample nonce", 16).c_str());
}

void test_pedido_de_abertura_leva_o_token_no_cabecalho() {
  std::string p = ws::pedidoDeAbertura("10.0.0.2", 10800, "/voz?sala=quarto", "CHAVE", "TOKEN-FALSO");
  TEST_ASSERT_EQUAL(0, p.find("GET /voz?sala=quarto HTTP/1.1\r\n"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, p.find("Host: 10.0.0.2:10800\r\n"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, p.find("Authorization: Bearer TOKEN-FALSO\r\n"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, p.find("Sec-WebSocket-Key: CHAVE\r\n"));
  TEST_ASSERT_EQUAL(std::string::npos, p.find("token="));  // nunca na URL
  TEST_ASSERT_EQUAL(p.size() - 4, p.find("\r\n\r\n"));
}

void test_status_http() {
  TEST_ASSERT_EQUAL(101, ws::statusHttp("HTTP/1.1 101 Switching Protocols"));
  TEST_ASSERT_EQUAL(401, ws::statusHttp("HTTP/1.1 401 Unauthorized"));
  TEST_ASSERT_EQUAL(-1, ws::statusHttp("SSH-2.0-OpenSSH"));
  TEST_ASSERT_EQUAL(-1, ws::statusHttp("HTTP/1.1 1"));
  TEST_ASSERT_EQUAL(-1, ws::statusHttp(""));
}

// ---------------------------------------------------------------- websocket: leitura dos quadros do servidor

struct Gravador : ws::Ouvinte {
  std::vector<std::string> textos;
  std::vector<bool> cortadas;
  std::vector<uint8_t> binario_;
  std::vector<size_t> pedacos;
  std::vector<uint8_t> ops;
  std::vector<std::string> cargas;

  void texto(const char* dados, size_t n, bool cortada) override {
    TEST_ASSERT_EQUAL('\0', dados[n]);
    textos.emplace_back(dados, n);
    cortadas.push_back(cortada);
  }
  void binario(const uint8_t* dados, size_t n) override {
    binario_.insert(binario_.end(), dados, dados + n);
    pedacos.push_back(n);
  }
  void controle(uint8_t op, const uint8_t* dados, size_t n) override {
    ops.push_back(op);
    cargas.emplace_back((const char*)dados, n);
  }
};

std::vector<uint8_t> quadroDoServidor(uint8_t op, const std::vector<uint8_t>& carga, bool fin = true) {
  std::vector<uint8_t> q;
  q.push_back((fin ? 0x80 : 0) | op);
  size_t n = carga.size();
  if (n < 126) {
    q.push_back((uint8_t)n);
  } else if (n <= 0xFFFF) {
    q.push_back(126);
    q.push_back((uint8_t)(n >> 8));
    q.push_back((uint8_t)n);
  } else {
    q.push_back(127);
    for (int k = 7; k >= 0; k--) q.push_back((uint8_t)((uint64_t)n >> (8 * k)));
  }
  q.insert(q.end(), carga.begin(), carga.end());
  return q;
}

std::vector<uint8_t> bytesDe(const std::string& s) { return std::vector<uint8_t>(s.begin(), s.end()); }

// Alimenta como o firmware faz: pede ao leitor quanto ele aceita e entrega em pedaços de até `pedaco` bytes
void alimentarAosPoucos(ws::Leitor& leitor, const std::vector<uint8_t>& dados, size_t pedaco, size_t espaco = 1 << 20) {
  size_t i = 0;
  while (i < dados.size()) {
    size_t quero = leitor.quantoAceita(espaco);
    TEST_ASSERT_TRUE_MESSAGE(quero > 0, "o leitor parou de aceitar dados");
    if (quero > pedaco) quero = pedaco;
    if (quero > dados.size() - i) quero = dados.size() - i;
    leitor.alimentar(dados.data() + i, quero);
    i += quero;
  }
}

void test_leitor_texto_da_rfc() {
  char buf[64];
  Gravador g;
  ws::Leitor leitor(buf, sizeof(buf), g);
  const uint8_t hello[] = {0x81, 0x05, 0x48, 0x65, 0x6c, 0x6c, 0x6f};
  leitor.alimentar(hello, sizeof(hello));
  TEST_ASSERT_FALSE(leitor.comErro());
  TEST_ASSERT_EQUAL(1, g.textos.size());
  TEST_ASSERT_EQUAL_STRING("Hello", g.textos[0].c_str());
  TEST_ASSERT_FALSE(g.cortadas[0]);
}

void test_leitor_byte_a_byte_e_varios_quadros_juntos() {
  char buf[64];
  Gravador g;
  ws::Leitor leitor(buf, sizeof(buf), g);
  std::vector<uint8_t> tudo = quadroDoServidor(ws::Texto, bytesDe("{\"tipo\": \"pong\"}"));
  std::vector<uint8_t> dois = quadroDoServidor(ws::Texto, bytesDe("ok"));
  tudo.insert(tudo.end(), dois.begin(), dois.end());
  alimentarAosPoucos(leitor, tudo, 1);
  leitor.alimentar(tudo.data(), tudo.size());  // e de uma vez só
  TEST_ASSERT_EQUAL(4, g.textos.size());
  TEST_ASSERT_EQUAL_STRING("{\"tipo\": \"pong\"}", g.textos[0].c_str());
  TEST_ASSERT_EQUAL_STRING("ok", g.textos[1].c_str());
  TEST_ASSERT_EQUAL_STRING("ok", g.textos[3].c_str());
}

void test_leitor_binario_de_4096_respeita_o_espaco() {
  char buf[16];
  Gravador g;
  ws::Leitor leitor(buf, sizeof(buf), g);
  std::vector<uint8_t> audio(4096);
  for (size_t i = 0; i < audio.size(); i++) audio[i] = (uint8_t)(i * 7);
  std::vector<uint8_t> q = quadroDoServidor(ws::Binario, audio);
  alimentarAosPoucos(leitor, q, 1000, 300);  // só cabem 300 bytes por vez no anel
  TEST_ASSERT_FALSE(leitor.comErro());
  TEST_ASSERT_EQUAL(4096, g.binario_.size());
  TEST_ASSERT_TRUE(g.binario_ == audio);
  for (size_t n : g.pedacos) TEST_ASSERT_TRUE(n <= 300);
  TEST_ASSERT_EQUAL(2, leitor.quantoAceita(0));  // de volta ao cabeçalho: aceita mesmo sem espaço no anel
}

void test_leitor_sem_espaco_no_anel_nao_aceita_audio() {
  char buf[16];
  Gravador g;
  ws::Leitor leitor(buf, sizeof(buf), g);
  std::vector<uint8_t> q = quadroDoServidor(ws::Binario, std::vector<uint8_t>(200, 1));
  leitor.alimentar(q.data(), 4);  // só o cabeçalho (200 usa 16 bits: 4 bytes)
  TEST_ASSERT_EQUAL(0, leitor.quantoAceita(0));
  TEST_ASSERT_EQUAL(50, leitor.quantoAceita(50));
  TEST_ASSERT_EQUAL(200, leitor.quantoAceita(1000));
}

void test_leitor_texto_grande_demais_vem_cortado() {
  char buf[8];
  Gravador g;
  ws::Leitor leitor(buf, sizeof(buf), g);
  std::vector<uint8_t> q = quadroDoServidor(ws::Texto, bytesDe("{\"tipo\": \"fim\"}"));
  alimentarAosPoucos(leitor, q, 3);
  TEST_ASSERT_EQUAL(1, g.textos.size());
  TEST_ASSERT_TRUE(g.cortadas[0]);
  TEST_ASSERT_EQUAL_STRING("{\"tipo\"", g.textos[0].c_str());
  // a próxima mensagem chega inteira
  std::vector<uint8_t> q2 = quadroDoServidor(ws::Texto, bytesDe("ok"));
  leitor.alimentar(q2.data(), q2.size());
  TEST_ASSERT_EQUAL_STRING("ok", g.textos[1].c_str());
  TEST_ASSERT_FALSE(g.cortadas[1]);
}

void test_leitor_mensagem_em_pedacos_com_ping_no_meio() {
  char buf[64];
  Gravador g;
  ws::Leitor leitor(buf, sizeof(buf), g);
  std::vector<uint8_t> tudo = quadroDoServidor(ws::Texto, bytesDe("Hel"), false);
  std::vector<uint8_t> ping = quadroDoServidor(ws::Ping, bytesDe("abc"));
  std::vector<uint8_t> resto = quadroDoServidor(ws::Continuacao, bytesDe("lo"));
  tudo.insert(tudo.end(), ping.begin(), ping.end());
  tudo.insert(tudo.end(), resto.begin(), resto.end());
  alimentarAosPoucos(leitor, tudo, 2);
  TEST_ASSERT_FALSE(leitor.comErro());
  TEST_ASSERT_EQUAL(1, g.ops.size());
  TEST_ASSERT_EQUAL(ws::Ping, g.ops[0]);
  TEST_ASSERT_EQUAL_STRING("abc", g.cargas[0].c_str());
  TEST_ASSERT_EQUAL(1, g.textos.size());
  TEST_ASSERT_EQUAL_STRING("Hello", g.textos[0].c_str());
}

void test_leitor_quadros_vazios_e_fechar() {
  char buf[16];
  Gravador g;
  ws::Leitor leitor(buf, sizeof(buf), g);
  std::vector<uint8_t> vazio = quadroDoServidor(ws::Texto, {});
  leitor.alimentar(vazio.data(), vazio.size());
  TEST_ASSERT_EQUAL(1, g.textos.size());
  TEST_ASSERT_EQUAL_STRING("", g.textos[0].c_str());
  std::vector<uint8_t> fechar = quadroDoServidor(ws::Fechar, {0x11, 0x31});  // 4401
  leitor.alimentar(fechar.data(), fechar.size());
  TEST_ASSERT_EQUAL(ws::Fechar, g.ops[0]);
  TEST_ASSERT_EQUAL(2, g.cargas[0].size());
}

void test_leitor_recusa_quadros_invalidos() {
  char buf[16];
  Gravador g;
  {
    ws::Leitor leitor(buf, sizeof(buf), g);
    const uint8_t mascarado[] = {0x81, 0x85, 1, 2, 3, 4, 0, 0, 0, 0, 0};
    leitor.alimentar(mascarado, sizeof(mascarado));
    TEST_ASSERT_TRUE(leitor.comErro());
    TEST_ASSERT_EQUAL(0, leitor.quantoAceita(1000));
  }
  {
    ws::Leitor leitor(buf, sizeof(buf), g);
    const uint8_t rsv[] = {0xC1, 0x00};  // RSV1: compressão que não foi pedida
    leitor.alimentar(rsv, sizeof(rsv));
    TEST_ASSERT_TRUE(leitor.comErro());
  }
  {
    ws::Leitor leitor(buf, sizeof(buf), g);
    const uint8_t continuacao[] = {0x80, 0x00};
    leitor.alimentar(continuacao, sizeof(continuacao));
    TEST_ASSERT_TRUE(leitor.comErro());
  }
  {
    ws::Leitor leitor(buf, sizeof(buf), g);
    const uint8_t pingGrande[] = {0x89, 126, 0x00, 0x80};
    leitor.alimentar(pingGrande, sizeof(pingGrande));
    TEST_ASSERT_TRUE(leitor.comErro());
    leitor.reiniciar();
    TEST_ASSERT_FALSE(leitor.comErro());
  }
  TEST_ASSERT_EQUAL(0, g.textos.size());
}

// ---------------------------------------------------------------- mensagens

void test_interpretar_estado_e_audio() {
  Evento ev;
  const char* estado = "{\"tipo\": \"estado\", \"estado\": \"pensando\"}";
  TEST_ASSERT_TRUE(interpretar(estado, strlen(estado), ev));
  TEST_ASSERT_TRUE(ev.tipo == Tipo::Estado);
  TEST_ASSERT_TRUE(ev.estado == Estado::Pensando);

  const char* inicio = "{\"tipo\": \"audio_inicio\", \"taxa\": 22050, \"formato\": \"s16le\", \"canais\": 1}";
  TEST_ASSERT_TRUE(interpretar(inicio, strlen(inicio), ev));
  TEST_ASSERT_TRUE(ev.tipo == Tipo::AudioInicio);
  TEST_ASSERT_EQUAL(22050, ev.taxa);
  TEST_ASSERT_EQUAL_STRING("s16le", ev.formato.c_str());
  TEST_ASSERT_EQUAL(1, ev.canais);
}

void test_interpretar_frase_e_fim_com_acentos() {
  Evento ev;
  const char* frase = "{\"tipo\": \"frase\", \"texto\": \"São 20 e 16.\", \"falada\": false}";
  TEST_ASSERT_TRUE(interpretar(frase, strlen(frase), ev));
  TEST_ASSERT_TRUE(ev.tipo == Tipo::Frase);
  TEST_ASSERT_EQUAL_STRING("São 20 e 16.", ev.texto.c_str());
  TEST_ASSERT_FALSE(ev.falada);

  const char* fim = "{\"tipo\": \"fim\", \"pergunta\": \"Que horas são?\", \"resposta\": \"São 20 e 16.\", "
                    "\"ferramentas\": [\"hora\"], \"tempos\": {\"stt\": 1.4, \"primeiro_audio\": 2.55}, \"erro\": \"\"}";
  TEST_ASSERT_TRUE(interpretar(fim, strlen(fim), ev));
  TEST_ASSERT_TRUE(ev.tipo == Tipo::Fim);
  TEST_ASSERT_EQUAL_STRING("Que horas são?", ev.pergunta.c_str());
  TEST_ASSERT_EQUAL_STRING("São 20 e 16.", ev.texto.c_str());
  TEST_ASSERT_EQUAL_STRING("stt=1.40 primeiro_audio=2.55", ev.tempos.c_str());
  TEST_ASSERT_EQUAL_STRING("", ev.erro.c_str());

  const char* interrompido = "{\"tipo\": \"fim\", \"pergunta\": \"\", \"resposta\": \"\", \"ferramentas\": [], "
                             "\"tempos\": {}, \"erro\": \"interrompido\"}";
  TEST_ASSERT_TRUE(interpretar(interrompido, strlen(interrompido), ev));
  TEST_ASSERT_EQUAL_STRING("interrompido", ev.erro.c_str());
  TEST_ASSERT_EQUAL_STRING("", ev.tempos.c_str());
}

void test_interpretar_invalidos_e_desconhecidos() {
  Evento ev;
  TEST_ASSERT_FALSE(interpretar("não é json", 11, ev));
  TEST_ASSERT_TRUE(ev.tipo == Tipo::Invalida);
  TEST_ASSERT_FALSE(interpretar("{\"sem\": 1}", 10, ev));
  const char* novo = "{\"tipo\": \"coisa_nova\"}";
  TEST_ASSERT_TRUE(interpretar(novo, strlen(novo), ev));
  TEST_ASSERT_TRUE(ev.tipo == Tipo::Desconhecido);
  const char* erro = "{\"tipo\": \"erro\", \"mensagem\": \"fala longa demais\"}";
  TEST_ASSERT_TRUE(interpretar(erro, strlen(erro), ev));
  TEST_ASSERT_TRUE(ev.tipo == Tipo::Erro);
  TEST_ASSERT_EQUAL_STRING("fala longa demais", ev.texto.c_str());
}

void test_tipo_do_comeco_de_mensagem_cortada() {
  const char* cortada = "{\"tipo\": \"fim\", \"pergunta\": \"uma pergunta que não coube";
  TEST_ASSERT_TRUE(tipoDoComeco(cortada, strlen(cortada)) == Tipo::Fim);
  const char* junto = "{\"tipo\":\"frase\",\"texto\":\"x";
  TEST_ASSERT_TRUE(tipoDoComeco(junto, strlen(junto)) == Tipo::Frase);
  TEST_ASSERT_TRUE(tipoDoComeco("{\"tip", 5) == Tipo::Invalida);
  TEST_ASSERT_TRUE(tipoDoComeco("{\"tipo\": \"fi", 12) == Tipo::Invalida);
}

void test_montar_mensagens() {
  TEST_ASSERT_EQUAL_STRING("{\"tipo\":\"inicio\"}", mensagemSimples("inicio").c_str());
  TEST_ASSERT_EQUAL_STRING("{\"tipo\":\"config\",\"saida_taxa\":16000,\"saida_formato\":\"s16le\"}",
                           mensagemConfig(16000).c_str());
  TEST_ASSERT_EQUAL_STRING("{\"tipo\":\"texto\",\"texto\":\"diga \\\"olá\\\"\"}",
                           mensagemComTexto("texto", "diga \"olá\"").c_str());
}

void test_limpar_utf8() {
  TEST_ASSERT_EQUAL_STRING("amanhã às 7h", limparUtf8("amanhã às 7h").c_str());
  TEST_ASSERT_EQUAL_STRING("a?b", limparUtf8("a\xE3" "b").c_str());          // "ã" em Latin-1
  TEST_ASSERT_EQUAL_STRING("x?", limparUtf8("x\xC3").c_str());              // cortado no fim
  TEST_ASSERT_EQUAL_STRING("??", limparUtf8("\xC0\xAF").c_str());           // forma longa
  TEST_ASSERT_EQUAL_STRING("???", limparUtf8("\xED\xA0\x80").c_str());      // substituto
  TEST_ASSERT_EQUAL_STRING("ok \xF0\x9F\x99\x82", limparUtf8("ok \xF0\x9F\x99\x82").c_str());  // emoji
  TEST_ASSERT_EQUAL_STRING("{\"tipo\":\"texto\",\"texto\":\"a?b\"}", mensagemComTexto("texto", "a\xE3" "b").c_str());
}

// ---------------------------------------------------------------- áudio e LED

void test_filtro_do_microfone_tira_o_dc_e_limita() {
  FiltroMic filtro(12);
  int16_t ultimo = 0;
  for (int i = 0; i < 4000; i++) ultimo = filtro.converter(1000 << 12);  // nível constante: vai a zero
  TEST_ASSERT_INT_WITHIN(5, 0, ultimo);
  FiltroMic forte(0);
  TEST_ASSERT_EQUAL(32767, forte.converter(1 << 30));
  FiltroMic negativo(0);
  TEST_ASSERT_EQUAL(-32768, negativo.converter(-(1 << 30)));
}

void test_para_estereo_com_volume() {
  const int16_t mono[] = {1000, -2000, 32767};
  int16_t estereo[6];
  paraEstereo(mono, 3, estereo, 50);
  const int16_t esperado[] = {500, 500, -1000, -1000, 16383, 16383};
  TEST_ASSERT_EQUAL_INT16_ARRAY(esperado, estereo, 6);
  TEST_ASSERT_EQUAL(32767, picoAbsoluto(mono, 3));
  const int16_t minimo[] = {-32768};
  TEST_ASSERT_EQUAL(32768, picoAbsoluto(minimo, 1));
}

void test_luz_por_estado() {
  TEST_ASSERT_TRUE(escolherLuz(false, true, true, true, Estado::Falando) == Luz::SemConexao);
  TEST_ASSERT_TRUE(escolherLuz(true, true, true, true, Estado::Falando) == Luz::Acesa);
  TEST_ASSERT_TRUE(escolherLuz(true, false, true, true, Estado::Falando) == Luz::Erro);
  TEST_ASSERT_TRUE(escolherLuz(true, false, true, false, Estado::Pronto) == Luz::PiscaLento);
  TEST_ASSERT_TRUE(escolherLuz(true, false, false, false, Estado::Pensando) == Luz::PiscaRapido);
  TEST_ASSERT_TRUE(escolherLuz(true, false, false, false, Estado::Transcrevendo) == Luz::PiscaRapido);
  TEST_ASSERT_TRUE(escolherLuz(true, false, false, false, Estado::Aguardando) == Luz::PiscaDuplo);
  TEST_ASSERT_TRUE(escolherLuz(true, false, false, false, Estado::Pronto) == Luz::Apagada);
  TEST_ASSERT_TRUE(luzAcesa(Luz::Acesa, 12345));
  TEST_ASSERT_FALSE(luzAcesa(Luz::Apagada, 12345));
  TEST_ASSERT_TRUE(luzAcesa(Luz::SemConexao, 2010));
  TEST_ASSERT_FALSE(luzAcesa(Luz::SemConexao, 2100));
  TEST_ASSERT_TRUE(luzAcesa(Luz::PiscaDuplo, 300));
  TEST_ASSERT_FALSE(luzAcesa(Luz::PiscaDuplo, 200));
}

// ---------------------------------------------------------------- quando tocar

void test_fala_espera_a_folga_no_comeco() {
  ControleDeFala c;
  c.comecar(22050, 250);  // 250 ms a 22050 Hz, 16 bits = 11024 bytes
  TEST_ASSERT_FALSE(c.podeTocar(4000, false, 0));
  TEST_ASSERT_FALSE(c.podeTocar(11000, false, 5));
  TEST_ASSERT_TRUE(c.podeTocar(11024, false, 10));
  TEST_ASSERT_TRUE(c.podeTocar(500, false, 20));  // já tocando: qualquer coisa serve
  TEST_ASSERT_EQUAL(0, c.estatisticas().engasgos);
}

void test_fala_junta_de_novo_depois_de_uma_falta() {
  // O defeito: depois de faltar áudio no meio, tocava cada pedacinho que chegava (voz picotada)
  ControleDeFala c;
  c.comecar(22050, 250);
  TEST_ASSERT_TRUE(c.podeTocar(20000, false, 0));
  TEST_ASSERT_FALSE(c.podeTocar(0, false, 100));     // acabou no meio
  TEST_ASSERT_FALSE(c.podeTocar(3000, false, 150));  // chegou um pedaço: ainda não
  TEST_ASSERT_TRUE(c.podeTocar(12000, false, 300));  // juntou a folga
  TEST_ASSERT_EQUAL(1, c.estatisticas().engasgos);
  TEST_ASSERT_EQUAL(200, c.estatisticas().msEngasgos);
  TEST_ASSERT_EQUAL(200, c.estatisticas().maiorEngasgo);
}

void test_fala_pausa_longa_nao_e_engasgo() {
  ControleDeFala c;
  c.comecar(22050, 250);
  TEST_ASSERT_TRUE(c.podeTocar(20000, false, 0));
  TEST_ASSERT_FALSE(c.podeTocar(0, false, 1000));  // "Um momento." acabou; o Hermes está pensando
  TEST_ASSERT_TRUE(c.podeTocar(20000, false, 4000));
  TEST_ASSERT_EQUAL(1, c.estatisticas().pausas);
  TEST_ASSERT_EQUAL(0, c.estatisticas().engasgos);
}

void test_fala_no_fim_toca_o_resto_sem_esperar() {
  ControleDeFala c;
  c.comecar(22050, 250);
  TEST_ASSERT_TRUE(c.podeTocar(100, true, 0));   // frase curtinha: não espera a folga
  TEST_ASSERT_FALSE(c.podeTocar(0, true, 10));   // acabou de verdade
  TEST_ASSERT_FALSE(c.podeTocar(1, true, 20));
  TEST_ASSERT_EQUAL(0, c.estatisticas().engasgos);
  c.tocou(44100);
  TEST_ASSERT_EQUAL(1000, c.estatisticas().msTocados);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_anel_escreve_e_le_dando_a_volta);
  RUN_TEST(test_anel_cheio_escreve_so_o_que_cabe);
  RUN_TEST(test_anel_limpar_descarta_o_que_falta);
  RUN_TEST(test_cabecalho_curto_com_mascara_da_rfc);
  RUN_TEST(test_cabecalho_de_16_e_64_bits);
  RUN_TEST(test_mascarar_em_pedacos_da_o_mesmo_resultado);
  RUN_TEST(test_base64);
  RUN_TEST(test_pedido_de_abertura_leva_o_token_no_cabecalho);
  RUN_TEST(test_status_http);
  RUN_TEST(test_leitor_texto_da_rfc);
  RUN_TEST(test_leitor_byte_a_byte_e_varios_quadros_juntos);
  RUN_TEST(test_leitor_binario_de_4096_respeita_o_espaco);
  RUN_TEST(test_leitor_sem_espaco_no_anel_nao_aceita_audio);
  RUN_TEST(test_leitor_texto_grande_demais_vem_cortado);
  RUN_TEST(test_leitor_mensagem_em_pedacos_com_ping_no_meio);
  RUN_TEST(test_leitor_quadros_vazios_e_fechar);
  RUN_TEST(test_leitor_recusa_quadros_invalidos);
  RUN_TEST(test_interpretar_estado_e_audio);
  RUN_TEST(test_interpretar_frase_e_fim_com_acentos);
  RUN_TEST(test_interpretar_invalidos_e_desconhecidos);
  RUN_TEST(test_tipo_do_comeco_de_mensagem_cortada);
  RUN_TEST(test_montar_mensagens);
  RUN_TEST(test_limpar_utf8);
  RUN_TEST(test_filtro_do_microfone_tira_o_dc_e_limita);
  RUN_TEST(test_para_estereo_com_volume);
  RUN_TEST(test_luz_por_estado);
  RUN_TEST(test_fala_espera_a_folga_no_comeco);
  RUN_TEST(test_fala_junta_de_novo_depois_de_uma_falta);
  RUN_TEST(test_fala_pausa_longa_nao_e_engasgo);
  RUN_TEST(test_fala_no_fim_toca_o_resto_sem_esperar);
  return UNITY_END();
}

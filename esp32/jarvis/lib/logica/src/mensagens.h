// As mensagens JSON do protocolo do jarvis-voz (docs/fase2.md): ler as que chegam e montar as que saem.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

enum class Tipo {
  Desconhecido, Invalida, Pronto, Estado, Transcricao, Ferramenta, Frase, AudioInicio, AudioFim, Fim, Erro,
  Config, Nova, Pong
};

enum class Estado { Desconhecido, Pronto, Ouvindo, Aguardando, Transcrevendo, Pensando, Falando };

struct Evento {
  Tipo tipo = Tipo::Desconhecido;
  Estado estado = Estado::Desconhecido;  // "estado"
  std::string texto;     // transcricao, frase, ferramenta (o nome), erro (a mensagem), fim (a resposta)
  bool falada = true;    // frase: false quando passou do limite de fala e só aparece como texto
  std::string pergunta;  // fim
  std::string erro;      // fim: vazio, "interrompido" ou o motivo
  std::string tempos;    // fim: "stt=1.40 primeira_palavra=3.60 ..."
  uint32_t taxa = 0;     // audio_inicio
  std::string formato;   // audio_inicio
  int canais = 0;        // audio_inicio
};

// JSON inteiro -> Evento. Devolve false (e tipo Invalida) se não for um JSON com "tipo".
bool interpretar(const char* json, size_t n, Evento& ev);

// Para uma mensagem que não coube no buffer: o tipo, lido do começo ({"tipo": "fim", ...).
Tipo tipoDoComeco(const char* inicio, size_t n);

Estado estadoDoNome(const char* nome);
const char* nomeDoEstado(Estado e);

// {"tipo": "inicio"}
std::string mensagemSimples(const char* tipo);
// {"tipo": "config", "saida_taxa": 16000, "saida_formato": "s16le"}
std::string mensagemConfig(uint32_t saidaTaxa);
// {"tipo": "texto", "texto": "..."}; o texto já passa por limparUtf8
std::string mensagemComTexto(const char* tipo, const std::string& texto);

// Troca por '?' os bytes que não formam UTF-8 válido: o servidor fecha a conexão se um texto não for UTF-8.
std::string limparUtf8(const std::string& s);

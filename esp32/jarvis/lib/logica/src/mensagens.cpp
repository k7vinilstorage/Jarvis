#include "mensagens.h"

#include <ArduinoJson.h>

#include <cstdio>
#include <cstring>

namespace {

struct NomeTipo {
  const char* nome;
  Tipo tipo;
};

const NomeTipo TIPOS[] = {
    {"pronto", Tipo::Pronto},       {"estado", Tipo::Estado},          {"transcricao", Tipo::Transcricao},
    {"ferramenta", Tipo::Ferramenta}, {"frase", Tipo::Frase},          {"audio_inicio", Tipo::AudioInicio},
    {"audio_fim", Tipo::AudioFim},  {"fim", Tipo::Fim},                {"erro", Tipo::Erro},
    {"config", Tipo::Config},       {"nova", Tipo::Nova},              {"pong", Tipo::Pong},
};

struct NomeEstado {
  const char* nome;
  Estado estado;
};

const NomeEstado ESTADOS[] = {
    {"pronto", Estado::Pronto},         {"ouvindo", Estado::Ouvindo},   {"aguardando", Estado::Aguardando},
    {"transcrevendo", Estado::Transcrevendo}, {"pensando", Estado::Pensando}, {"falando", Estado::Falando},
};

Tipo tipoDoNome(const char* nome) {
  for (const NomeTipo& t : TIPOS) {
    if (strcmp(t.nome, nome) == 0) return t.tipo;
  }
  return Tipo::Desconhecido;
}

std::string serializar(const JsonDocument& doc) {
  std::string saida;
  saida.resize(measureJson(doc) + 1);
  size_t n = serializeJson(doc, &saida[0], saida.size());
  saida.resize(n);
  return saida;
}

}  // namespace

Estado estadoDoNome(const char* nome) {
  for (const NomeEstado& e : ESTADOS) {
    if (strcmp(e.nome, nome) == 0) return e.estado;
  }
  return Estado::Desconhecido;
}

const char* nomeDoEstado(Estado e) {
  for (const NomeEstado& n : ESTADOS) {
    if (n.estado == e) return n.nome;
  }
  return "?";
}

bool interpretar(const char* json, size_t n, Evento& ev) {
  ev = Evento();
  JsonDocument doc;
  if (deserializeJson(doc, json, n) != DeserializationError::Ok || !doc["tipo"].is<const char*>()) {
    ev.tipo = Tipo::Invalida;
    return false;
  }
  ev.tipo = tipoDoNome(doc["tipo"]);
  switch (ev.tipo) {
    case Tipo::Estado:
      ev.estado = estadoDoNome(doc["estado"] | "");
      break;
    case Tipo::Transcricao:
      ev.texto = doc["texto"] | "";
      break;
    case Tipo::Ferramenta:
      ev.texto = doc["nome"] | "";
      break;
    case Tipo::Frase:
      ev.texto = doc["texto"] | "";
      ev.falada = doc["falada"] | true;
      break;
    case Tipo::AudioInicio:
      ev.taxa = doc["taxa"] | 0;
      ev.formato = doc["formato"] | "s16le";
      ev.canais = doc["canais"] | 1;
      break;
    case Tipo::Erro:
      ev.texto = doc["mensagem"] | "";
      break;
    case Tipo::Fim: {
      ev.pergunta = doc["pergunta"] | "";
      ev.texto = doc["resposta"] | "";
      ev.erro = doc["erro"] | "";
      for (JsonPairConst kv : doc["tempos"].as<JsonObjectConst>()) {
        char item[64];
        snprintf(item, sizeof(item), "%s%s=%.2f", ev.tempos.empty() ? "" : " ", kv.key().c_str(),
                 kv.value().as<float>());
        ev.tempos += item;
      }
      break;
    }
    default:
      break;
  }
  return true;
}

Tipo tipoDoComeco(const char* inicio, size_t n) {
  std::string cab(inicio, n < 200 ? n : 200);
  size_t p = cab.find("\"tipo\"");
  if (p == std::string::npos) return Tipo::Invalida;
  p += 6;
  while (p < cab.size() && cab[p] == ' ') p++;
  if (p >= cab.size() || cab[p] != ':') return Tipo::Invalida;
  p++;
  while (p < cab.size() && cab[p] == ' ') p++;
  if (p >= cab.size() || cab[p] != '"') return Tipo::Invalida;
  size_t fim = cab.find('"', p + 1);
  if (fim == std::string::npos) return Tipo::Invalida;
  return tipoDoNome(cab.substr(p + 1, fim - p - 1).c_str());
}

std::string mensagemSimples(const char* tipo) {
  JsonDocument doc;
  doc["tipo"] = tipo;
  return serializar(doc);
}

std::string mensagemComTexto(const char* tipo, const std::string& texto) {
  std::string limpo = limparUtf8(texto);
  JsonDocument doc;
  doc["tipo"] = tipo;
  doc["texto"] = limpo.c_str();
  return serializar(doc);
}

std::string limparUtf8(const std::string& s) {
  std::string saida;
  saida.reserve(s.size());
  size_t i = 0;
  while (i < s.size()) {
    uint8_t c = (uint8_t)s[i];
    size_t tam = 0;
    uint8_t min2 = 0x80, max2 = 0xBF;  // faixa do segundo byte (tira as formas longas e os substitutos)
    if (c < 0x80) {
      tam = 1;
    } else if (c >= 0xC2 && c <= 0xDF) {
      tam = 2;
    } else if (c >= 0xE0 && c <= 0xEF) {
      tam = 3;
      if (c == 0xE0) min2 = 0xA0;
      if (c == 0xED) max2 = 0x9F;
    } else if (c >= 0xF0 && c <= 0xF4) {
      tam = 4;
      if (c == 0xF0) min2 = 0x90;
      if (c == 0xF4) max2 = 0x8F;
    }
    bool valido = tam > 0 && i + tam <= s.size();
    for (size_t k = 1; valido && k < tam; k++) {
      uint8_t b = (uint8_t)s[i + k];
      if (k == 1 ? (b < min2 || b > max2) : (b & 0xC0) != 0x80) valido = false;
    }
    if (valido) {
      saida.append(s, i, tam);
      i += tam;
    } else {
      saida += '?';
      i++;
    }
  }
  return saida;
}

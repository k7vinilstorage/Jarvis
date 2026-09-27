#!/usr/bin/env python3
"""Mede a Fase 0 do Jarvis de uma vez e gera um relatório com a tabela preenchida.

Uso, na pasta do projeto e com tudo rodando:
    python3 scripts/medicoes.py --nome SEU_NOME
    python3 scripts/medicoes.py --nome SEU_NOME --rotulo com-honcho

O que ele faz, em ordem:
  1. confere se o Ollama e o Hermes respondem
  2. tira o modelo da VRAM e faz o teste 1 (frio) pelo Hermes
  3. anota VRAM, GPU e o "ollama ps" com o modelo carregado
  4. testes 2 (quente), 3 (hora) e 4 (memória) pelo Hermes, e o 2b: o teste 2 de novo depois de o
     minuto virar, para ver se o cache do prompt sobrevive ao relógio
  5. mede a velocidade do modelo direto no Ollama (com e sem raciocínio, leitura de prompt)
  6. anota o prompt fixo do Hermes, a RAM e o uso de cada contêiner

Leva de 3 a 6 minutos. Só usa a biblioteca padrão do Python (3.8 ou mais novo).
O relatório vai para medicoes/medicao-AAAAMMDD-HHMM-<rotulo>.md.
A chave da API é lida do .env e nunca aparece na tela nem no relatório.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SEM_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PERGUNTA_1 = "Olá! Quem é você? Responda em uma frase."
PERGUNTA_3 = "Que horas são agora e que dia da semana é hoje?"
PERGUNTA_4A = "Meu nome é {nome}. Guarde isso na sua memória."
PERGUNTA_4B = "Qual é o meu nome? Responda só com o nome."
PERGUNTA_GERAR = "Explique em dois parágrafos como a chuva se forma."

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

VOCABULARIO = (
    "casa agenda tempo chuva sol reunião prova aula trabalho projeto servidor rede cidade rua carro "
    "livro música janela porta mesa cadeira computador telefone mensagem relatório semana manhã tarde "
    "noite amigo família professor aluno disciplina laboratório energia bateria cabo placa sensor "
    "microfone alto-falante voz resposta pergunta sistema memória arquivo pasta nuvem local rápido "
    "lento grande pequeno novo antigo claro escuro quente frio verde azul vermelho amarelo simples "
    "difícil fácil importante urgente calmo feliz cansado pronto ocupado livre aberto fechado primeiro "
    "último próximo anterior sempre nunca hoje amanhã ontem cedo tarde perto longe dentro fora acima "
    "abaixo porque quando onde como então também ainda muito pouco mais menos cada todo nenhum algum "
    "escreve lê fala ouve pensa corre anda para volta chega sai entra abre fecha liga desliga envia "
    "recebe guarda procura encontra perde ganha começa termina muda fica deixa leva traz mostra"
).split()


# ---------------------------------------------------------------- utilidades

def log(msg: str) -> None:
    print(msg, flush=True)


def num(x, casas: int = 1) -> str:
    """Número no formato brasileiro: 12,3."""
    if x is None:
        return "?"
    return ("%." + str(casas) + "f") % x if casas else "%d" % round(x)


def br(texto: str) -> str:
    return texto.replace(".", ",")


def seg(x) -> str:
    return "?" if x is None else br(num(x, 1)) + " s"


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def sem_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)


def celula(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def ler_env(caminho: Path) -> dict:
    valores = {}
    try:
        linhas = caminho.read_text(encoding="utf-8").splitlines()
    except OSError:
        return valores
    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave = chave.strip()
        if chave.startswith("export "):
            chave = chave[7:].strip()
        valor = valor.strip()
        if valor[:1] in ("'", '"') and valor.find(valor[0], 1) > 0:
            valor = valor[1:valor.find(valor[0], 1)]
        else:
            valor = re.split(r"\s+#", valor, maxsplit=1)[0]
        valores[chave] = valor
    return valores


class Contexto:
    """Configuração e resultados da rodada."""

    def __init__(self, args, env: dict):
        self.args = args
        self.env = env
        self.ollama = args.ollama.rstrip("/")
        self.hermes = args.hermes.rstrip("/")
        self.chave = os.environ.get("HERMES_API_KEY") or env.get("HERMES_API_KEY", "")
        self.modelo = env.get("JARVIS_MODEL") or "jarvis-qwen"
        self.num_ctx = env.get("JARVIS_NUM_CTX") or ""
        self.fuso = env.get("TZ") or "America/Sao_Paulo"
        self.timeout = args.timeout
        self.erros: list = []
        self.dados: dict = {}

    def limpa(self, texto: str) -> str:
        """Garante que a chave nunca vaze numa mensagem de erro."""
        if self.chave and len(self.chave) >= 8:
            texto = texto.replace(self.chave, "***")
        return texto

    def erro(self, etapa: str, msg) -> str:
        msg = self.limpa(str(msg))
        self.erros.append("%s: %s" % (etapa, msg))
        log("      erro: " + msg)
        return "erro: " + msg


def descrever_erro(e: BaseException) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            corpo = e.read().decode("utf-8", "replace").strip()[:300]
        except Exception:
            corpo = ""
        return ("HTTP %d %s" % (e.code, corpo)).strip()
    if isinstance(e, urllib.error.URLError):
        return "sem conexão (%s)" % (e.reason,)
    return "%s: %s" % (type(e).__name__, e)


def http_json(url: str, corpo=None, cabecalhos=None, timeout: float = 60):
    dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(url, data=dados, method="GET" if dados is None else "POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (cabecalhos or {}).items():
        req.add_header(k, v)
    with SEM_PROXY.open(req, timeout=timeout) as r:
        bruto = r.read().decode("utf-8", "replace")
    return json.loads(bruto) if bruto.strip() else None


def rodar(cmd: list, timeout: float = 90):
    """Roda um comando na pasta do projeto. Devolve (saída, erro)."""
    try:
        p = subprocess.run(cmd, cwd=str(RAIZ), capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return None, "comando não encontrado: " + cmd[0]
    except subprocess.TimeoutExpired:
        return None, "tempo esgotado (%ds): %s" % (timeout, " ".join(cmd))
    saida = sem_ansi(p.stdout.decode("utf-8", "replace"))
    if p.returncode != 0:
        erro = sem_ansi(p.stderr.decode("utf-8", "replace")).strip() or saida.strip()
        return None, (erro or "código de saída %d" % p.returncode)[:400]
    return saida, None


def agora_local(fuso: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo  # novermin (Python 3.9+; no 3.8 cai no fuso fixo abaixo)
        return datetime.now(ZoneInfo(fuso))
    except Exception:
        if fuso == "America/Sao_Paulo":
            return datetime.now(timezone(timedelta(hours=-3)))
        return datetime.now()


# ---------------------------------------------------------------- Ollama

def base_nome(nome: str) -> str:
    return nome if ":" in nome else nome + ":latest"


def ollama_ps(ctx: Contexto) -> list:
    d = http_json(ctx.ollama + "/api/ps", timeout=30) or {}
    return d.get("models") or []


def modelo_carregado(ctx: Contexto, modelos: list):
    for m in modelos:
        if base_nome(m.get("name") or m.get("model") or "") == base_nome(ctx.modelo):
            return m
    return None


def processador(m: dict) -> str:
    total, vram = m.get("size") or 0, m.get("size_vram") or 0
    if not total:
        return "?"
    pct = int(round(100.0 * vram / total))
    if pct >= 100:
        return "100% GPU"
    if pct <= 0:
        return "100% CPU"
    return "%d%%/%d%% CPU/GPU" % (100 - pct, pct)


def descarregar(ctx: Contexto) -> str:
    if not modelo_carregado(ctx, ollama_ps(ctx)):
        return "o modelo já estava fora da VRAM"
    http_json(ctx.ollama + "/api/generate", {"model": ctx.modelo, "keep_alive": 0}, timeout=120)
    limite = time.monotonic() + 60
    while time.monotonic() < limite:
        if not modelo_carregado(ctx, ollama_ps(ctx)):
            return "modelo retirado da VRAM"
        time.sleep(0.5)
    raise RuntimeError("o modelo não saiu da VRAM em 60 s")


def ollama_chat(ctx: Contexto, conteudo: str, think=None, num_predict: int = 300) -> dict:
    corpo = {
        "model": ctx.modelo,
        "messages": [{"role": "user", "content": conteudo}],
        "stream": False,
        "options": {"num_predict": num_predict},  # não mexe no num_ctx para não recarregar o modelo
    }
    if think is not None:
        corpo["think"] = think
    t0 = time.monotonic()
    d = http_json(ctx.ollama + "/api/chat", corpo, timeout=ctx.timeout) or {}
    d["_parede"] = time.monotonic() - t0
    msg = d.get("message") or {}
    pensamento = msg.get("thinking") or ""
    texto = msg.get("content") or ""
    if not pensamento and "<think>" in texto:
        pensamento = texto.split("<think>", 1)[1].split("</think>", 1)[0]
    d["_pensou"] = bool(pensamento.strip())
    d["_pensamento_chars"] = len(pensamento)
    d["_texto"] = texto
    ev, dur = d.get("eval_count"), d.get("eval_duration")
    d["_tps"] = ev / (dur / 1e9) if ev and dur else None
    pev, pdur = d.get("prompt_eval_count"), d.get("prompt_eval_duration")
    d["_prompt_tps"] = pev / (pdur / 1e9) if pev and pdur else None
    return d


def texto_longo(n_palavras: int, rnd: random.Random) -> str:
    frases, total = [], 0
    while total < n_palavras:
        k = rnd.randint(6, 14)
        frases.append(" ".join(rnd.choice(VOCABULARIO) for _ in range(k)).capitalize() + ".")
        total += k
    return " ".join(frases)


# ---------------------------------------------------------------- Hermes

def nome_ferramenta(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    for k in ("tool", "name", "tool_name", "label", "title"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    for k in ("data", "payload"):
        if isinstance(obj.get(k), dict):
            return nome_ferramenta(obj[k])
    return ""


def perguntar_hermes(ctx: Contexto, pergunta: str, com_uso: bool = True) -> dict:
    """Faz uma pergunta em streaming e mede os tempos. Cada chamada é uma sessão nova."""
    corpo = {"model": "hermes-agent", "messages": [{"role": "user", "content": pergunta}], "stream": True}
    if com_uso:
        corpo["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(ctx.hermes + "/v1/chat/completions",
                                 data=json.dumps(corpo).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    req.add_header("Authorization", "Bearer " + ctx.chave)

    r = {"pergunta": pergunta, "texto": "", "raciocinio_chars": 0, "ferramentas": [], "eventos": {},
         "uso": None, "t_byte": None, "t_raciocinio": None, "t_token": None, "t_resposta": None,
         "t_total": None, "erro": None}
    inicio = time.monotonic()
    try:
        resp = SEM_PROXY.open(req, timeout=ctx.timeout)
    except urllib.error.HTTPError as e:
        if com_uso and e.code in (400, 422):
            return perguntar_hermes(ctx, pergunta, com_uso=False)
        r["erro"] = ctx.limpa(descrever_erro(e))
        r["t_total"] = time.monotonic() - inicio
        return r
    except Exception as e:
        r["erro"] = ctx.limpa(descrever_erro(e))
        r["t_total"] = time.monotonic() - inicio
        return r

    partes, sobra, evento = [], [], None
    try:
        with resp:
            for bruto in resp:
                agora = time.monotonic() - inicio
                if r["t_byte"] is None:
                    r["t_byte"] = agora
                if agora > ctx.timeout:
                    r["erro"] = "tempo esgotado (%ds)" % ctx.timeout
                    break
                linha = bruto.decode("utf-8", "replace").rstrip("\r\n")
                if not linha:
                    evento = None
                    continue
                if linha.startswith(":"):
                    continue  # comentário SSE, por exemplo ": keepalive"
                if linha.startswith("event:"):
                    evento = linha[6:].strip()
                    continue
                if not linha.startswith("data:"):
                    if sum(len(s) for s in sobra) < 200000:
                        sobra.append(linha)
                    continue
                dado = linha[5:].strip()
                if dado == "[DONE]":
                    break
                try:
                    obj = json.loads(dado)
                except ValueError:
                    continue
                if evento and evento != "message":
                    r["eventos"][evento] = r["eventos"].get(evento, 0) + 1
                    if "tool" in evento:
                        nome = nome_ferramenta(obj) or json.dumps(obj, ensure_ascii=False)[:60]
                        r["ferramentas"].append(nome)
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("error"):
                    r["erro"] = ctx.limpa(json.dumps(obj["error"], ensure_ascii=False)[:300])
                if obj.get("usage"):
                    r["uso"] = obj["usage"]
                for escolha in obj.get("choices") or []:
                    delta = escolha.get("delta") or escolha.get("message") or {}
                    rac = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if rac:
                        r["raciocinio_chars"] += len(rac)
                        if r["t_raciocinio"] is None:
                            r["t_raciocinio"] = agora
                        if r["t_token"] is None:
                            r["t_token"] = agora
                    txt = delta.get("content") or ""
                    if txt:
                        partes.append(txt)
                        if r["t_token"] is None:
                            r["t_token"] = agora
                        if r["t_resposta"] is None and txt.strip():
                            r["t_resposta"] = agora
                    for chamada in delta.get("tool_calls") or []:
                        nome = ((chamada or {}).get("function") or {}).get("name")
                        if nome:
                            r["ferramentas"].append(nome)
    except Exception as e:
        r["erro"] = ctx.limpa(descrever_erro(e))
    r["t_total"] = time.monotonic() - inicio

    if not partes and sobra:
        # O servidor respondeu sem streaming: aproveita o JSON inteiro.
        try:
            d = json.loads("\n".join(sobra))
            partes.append(d["choices"][0]["message"].get("content") or "")
            r["uso"] = d.get("usage") or r["uso"]
            r["t_resposta"] = r["t_total"]
        except Exception:
            if not r["erro"]:
                r["erro"] = ctx.limpa("resposta inesperada: " + " ".join(sobra)[:300])
    r["texto"] = "".join(partes).strip()
    return r


def unicos(itens: list) -> list:
    vistos, saida = set(), []
    for i in itens:
        if i not in vistos:
            vistos.add(i)
            saida.append(i)
    return saida


def resumo_hermes(r: dict) -> str:
    if r.get("erro") and not r.get("texto"):
        return "erro: " + r["erro"]
    if not r.get("texto"):
        return "sem resposta em texto, %s no total" % seg(r.get("t_total"))
    return "%s até a 1ª palavra, %s no total" % (seg(r.get("t_resposta")), seg(r.get("t_total")))


# ---------------------------------------------------------------- conferências

RE_HORA = re.compile(r"\b([01]?\d|2[0-3])\s*(?:h\s*(\d{2})?\b|:(\d{2})\b|horas?\b)")


def conferir_hora(texto: str, antes: datetime, depois: datetime) -> dict:
    t = sem_acento(texto)
    esperado = "%s, %02dh%02d" % (DIAS[antes.weekday()], antes.hour, antes.minute)
    dias_ok = {sem_acento(DIAS[antes.weekday()]).split("-")[0], sem_acento(DIAS[depois.weekday()]).split("-")[0]}
    dia_ok = any(re.search(r"\b" + d + r"\b", t) for d in dias_ok)

    hora = minuto = None
    m = RE_HORA.search(t)
    if m:
        hora = int(m.group(1))
        mm = m.group(2) or m.group(3)
        minuto = int(mm) if mm and int(mm) < 60 else None
    elif "meio-dia" in t or "meio dia" in t:
        hora = 12
    elif "meia-noite" in t or "meia noite" in t:
        hora = 0

    hora_ok = None
    if hora is not None:
        melhor = None
        for h24 in {hora % 24, (hora + 12) % 24}:
            for ref in (antes, depois):
                alvo = ref.replace(hour=h24, minute=minuto if minuto is not None else ref.minute,
                                   second=0, microsecond=0)
                for ajuste in (timedelta(0), timedelta(days=1), timedelta(days=-1)):
                    dif = abs((alvo + ajuste - ref).total_seconds()) / 60
                    melhor = dif if melhor is None else min(melhor, dif)
        hora_ok = melhor is not None and melhor <= (5 if minuto is not None else 0.5)
        if minuto is None and not hora_ok:
            # Só a hora cheia: aceita se for a hora atual.
            hora_ok = hora % 12 in {antes.hour % 12, depois.hour % 12}

    if hora is None:
        veredito = "confira (não achei a hora na resposta)"
    elif hora_ok and dia_ok:
        veredito = "certa"
    elif hora_ok:
        veredito = "hora certa, dia da semana errado ou ausente"
    else:
        veredito = "errada"
    achado = None if hora is None else ("%dh%02d" % (hora, minuto) if minuto is not None else "%dh" % hora)
    return {"veredito": veredito, "esperado": esperado, "achado": achado, "dia_ok": dia_ok}


def nome_na_memoria(nome: str) -> str:
    pasta = RAIZ / "data" / "hermes" / "memories"
    if not pasta.is_dir():
        return "pasta data/hermes/memories não encontrada"
    alvo = sem_acento(nome.split()[0])
    achados = []
    try:
        for arq in sorted(pasta.glob("*.md")):
            try:
                if alvo in sem_acento(arq.read_text(encoding="utf-8", errors="replace")):
                    achados.append(arq.name)
            except OSError:
                return "sem permissão para ler " + arq.name
    except OSError as e:
        return "não consegui ler a pasta (%s)" % e
    return ("gravado em " + ", ".join(achados)) if achados else "não aparece nos arquivos de memória"


# ---------------------------------------------------------------- sistema

def nvidia(ctx: Contexto) -> dict:
    q_gpu = ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,driver_version",
             "--format=csv,noheader,nounits"]
    q_apps = ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
              "--format=csv,noheader,nounits"]
    prefixo, via = [], "servidor"
    saida, erro = rodar(q_gpu, 30)
    if saida is None:
        prefixo, via = ["docker", "exec", "ollama"], "contêiner ollama"
        saida, erro2 = rodar(prefixo + q_gpu, 30)
        if saida is None:
            raise RuntimeError("nvidia-smi falhou (%s / %s)" % (erro, erro2))
    partes = [p.strip() for p in saida.strip().splitlines()[0].split(",")]
    info = {"via": via, "nome": partes[0], "usada": float(partes[1]), "total": float(partes[2]),
            "driver": partes[3] if len(partes) > 3 else "?", "apps": []}
    apps, _ = rodar(prefixo + q_apps, 30)
    for linha in (apps or "").strip().splitlines():
        p = [x.strip() for x in linha.split(",")]
        if len(p) >= 3:
            info["apps"].append({"pid": p[0], "processo": p[1], "mib": p[2]})
    return info


def ram() -> dict:
    valores = {}
    with open("/proc/meminfo") as f:
        for linha in f:
            chave, resto = linha.split(":", 1)
            valores[chave] = int(resto.split()[0]) * 1024
    return valores


def bytes_de(texto: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", texto.strip(), re.I)
    if not m:
        return 0.0
    fator = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12,
             "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}
    return float(m.group(1)) * fator.get(m.group(2).upper(), 1)


def docker_stats() -> list:
    saida, erro = rodar(["docker", "stats", "--no-stream", "--format",
                         "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"], 90)
    if saida is None:
        raise RuntimeError(erro)
    linhas = []
    for linha in saida.strip().splitlines():
        p = linha.split("\t")
        if len(p) >= 3:
            linhas.append((p[0], p[1].split("/")[0].strip(), p[2].strip(), bytes_de(p[1].split("/")[0])))
    return sorted(linhas, key=lambda x: -x[3])


def gib(x) -> str:
    return br("%.1f GB" % (x / 1024 ** 3))


def honcho_ativo(env: dict) -> bool:
    if "docker-compose.honcho.yml" in env.get("COMPOSE_FILE", ""):
        return True
    saida, _ = rodar(["docker", "ps", "--format", "{{.Names}}"], 30)
    return bool(saida) and any(n.startswith("jarvis-honcho") for n in saida.split())


def hermes_cli(*args, timeout: float = 120):
    return rodar(["docker", "compose", "exec", "-T", "hermes", "hermes"] + list(args), timeout)


def resumo_prompt_size(saida: str) -> str:
    """Resume a saída do prompt-size: tamanho do prompt de sistema e dos esquemas de ferramentas."""
    partes = []
    for rotulo, padrao in (("sistema", r"System prompt total.*?\(([\d.,]+ ?KB)"),
                           ("ferramentas", r"Tool schemas.*?\(([\d.,]+ ?KB)(?:.*?(\d+) tools)?")):
        m = re.search(padrao, saida)
        if m:
            texto = "%s %s" % (rotulo, m.group(1))
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                texto = "%s ferramentas %s" % (m.group(2), m.group(1))
            partes.append(texto)
    return " + ".join(partes) or linha_total(saida)


def linha_total(saida: str) -> str:
    candidatas = []
    for linha in saida.splitlines():
        limpa = re.sub(r"[│┃║|╎─━═┌┐└┘├┤┬┴┼╭╮╰╯]+", " ", linha)
        limpa = re.sub(r"\s+", " ", limpa).strip()
        if re.search(r"total", limpa, re.I) and re.search(r"\d", limpa):
            candidatas.append(limpa)
    return candidatas[-1][:90] if candidatas else ""


# ---------------------------------------------------------------- rodada

def etapa(n: int, total: int, titulo: str) -> None:
    log("[%d/%d] %s" % (n, total, titulo))


def executar(ctx: Contexto) -> None:
    a = ctx.args
    D = ctx.dados
    TOTAL = 8

    # 1. saúde
    etapa(1, TOTAL, "Conferindo Ollama, Hermes e ambiente")
    try:
        D["ollama_versao"] = (http_json(ctx.ollama + "/api/version", timeout=10) or {}).get("version", "?")
    except Exception as e:
        raise SystemExit("O Ollama não respondeu em %s (%s). Ele está rodando?" % (ctx.ollama, descrever_erro(e)))
    try:
        show = http_json(ctx.ollama + "/api/show", {"model": ctx.modelo}, timeout=30) or {}
        D["parametros"] = " ".join((show.get("parameters") or "").split())
        D["capacidades"] = ", ".join(show.get("capabilities") or []) or "?"
        det = show.get("details") or {}
        D["detalhes_modelo"] = "%s, %s, %s" % (det.get("family", "?"), det.get("parameter_size", "?"),
                                               det.get("quantization_level", "?"))
    except Exception as e:
        D["parametros"] = ctx.erro("ollama show " + ctx.modelo, descrever_erro(e))

    D["hermes_ok"] = False
    if not ctx.chave:
        ctx.erro("Hermes", "HERMES_API_KEY vazia no .env; os testes pelo Hermes serão pulados")
    else:
        try:
            http_json(ctx.hermes + "/health", timeout=10)
            modelos = http_json(ctx.hermes + "/v1/models",
                                cabecalhos={"Authorization": "Bearer " + ctx.chave}, timeout=10) or {}
            D["hermes_ok"] = True
            D["hermes_modelos"] = ", ".join(m.get("id", "?") for m in modelos.get("data") or [])
        except Exception as e:
            ctx.erro("Hermes", descrever_erro(e) + " (os testes pelo Hermes serão pulados)")

    saida, erro = hermes_cli("version", timeout=60)
    if saida is None:
        saida, erro = hermes_cli("--version", timeout=60)
    if saida:
        linhas = [l.strip() for l in saida.splitlines() if l.strip()] or ["?"]
        D["hermes_versao"] = next((l for l in linhas if re.search(r"\d+\.\d+", l)), linhas[0])[:100]
    else:
        D["hermes_versao"] = "erro: " + str(erro)[:120]
    D["honcho"] = honcho_ativo(ctx.env)
    if D["honcho"]:
        saida, erro = hermes_cli("honcho", "status", timeout=60)
        D["honcho_status"] = saida.strip() if saida else "erro: " + str(erro)
    log("      Ollama %s | Hermes: %s | Honcho: %s" % (D["ollama_versao"], D["hermes_versao"],
                                                       "ativo" if D["honcho"] else "inativo"))

    # 2. teste 1 (frio)
    etapa(2, TOTAL, "Teste 1: pergunta simples com o modelo fora da VRAM (frio)")
    if a.pular_frio:
        D["frio_nota"] = "pulado a pedido (--pular-frio): o teste 1 não foi frio"
    else:
        try:
            D["frio_nota"] = descarregar(ctx)
        except Exception as e:
            D["frio_nota"] = ctx.erro("descarregar o modelo", descrever_erro(e))
    if D["hermes_ok"]:
        D["t1"] = perguntar_hermes(ctx, PERGUNTA_1)
        log("      " + resumo_hermes(D["t1"]))
    else:
        # Sem o Hermes, carrega o modelo direto para as medições seguintes.
        try:
            ollama_chat(ctx, "Oi", think=False, num_predict=1)
        except Exception as e:
            ctx.erro("carregar o modelo", descrever_erro(e))

    # 3. GPU com o modelo carregado
    etapa(3, TOTAL, "VRAM e ollama ps com o modelo carregado")
    try:
        D["ps"] = ollama_ps(ctx)
    except Exception as e:
        D["ps"] = None
        ctx.erro("ollama ps", descrever_erro(e))
    try:
        D["gpu"] = nvidia(ctx)
        g = D["gpu"]
        log("      %s: %s de %s MiB usados" % (g["nome"], num(g["usada"], 0), num(g["total"], 0)))
    except Exception as e:
        D["gpu"] = None
        ctx.erro("nvidia-smi", e)
    m = modelo_carregado(ctx, D["ps"] or [])
    if m:
        log("      %s: %s, contexto %s" % (ctx.modelo, processador(m), m.get("context_length", "?")))

    # 4. testes 2, 3 e 4 pelo Hermes
    etapa(4, TOTAL, "Testes 2 (quente), 3 (hora) e 4 (memória) pelo Hermes")
    if D["hermes_ok"]:
        time.sleep(a.pausa)
        D["t2"] = perguntar_hermes(ctx, PERGUNTA_1)
        log("      teste 2: " + resumo_hermes(D["t2"]))
        if not D["t2"].get("uso") and not D["t2"].get("erro"):
            # O streaming não trouxe a contagem de tokens: pede de novo sem streaming.
            try:
                d = http_json(ctx.hermes + "/v1/chat/completions",
                              {"model": "hermes-agent", "messages": [{"role": "user", "content": PERGUNTA_1}]},
                              cabecalhos={"Authorization": "Bearer " + ctx.chave}, timeout=ctx.timeout) or {}
                D["t2"]["uso"] = d.get("usage")
            except Exception as e:
                ctx.erro("uso de tokens do teste 2", descrever_erro(e))

        time.sleep(a.pausa)
        antes = agora_local(ctx.fuso)
        D["t3"] = perguntar_hermes(ctx, PERGUNTA_3)
        depois = agora_local(ctx.fuso)
        D["t3_conf"] = conferir_hora(D["t3"].get("texto", ""), antes, depois)
        if not D["t3"].get("texto"):
            D["t3_conf"]["veredito"] = "sem resposta"
        log("      teste 3: %s | hora %s (esperado %s)" % (resumo_hermes(D["t3"]), D["t3_conf"]["veredito"],
                                                        D["t3_conf"]["esperado"]))

        if a.nome:
            time.sleep(a.pausa)
            D["t4a"] = perguntar_hermes(ctx, PERGUNTA_4A.format(nome=a.nome))
            log("      teste 4a: " + resumo_hermes(D["t4a"]))
            time.sleep(a.pausa)
            D["t4b"] = perguntar_hermes(ctx, PERGUNTA_4B)
            if D["t4b"].get("texto"):
                D["t4_lembrou"] = "sim" if sem_acento(a.nome.split()[0]) in sem_acento(D["t4b"]["texto"]) else "não"
            else:
                D["t4_lembrou"] = "sem resposta"
            D["t4_arquivo"] = nome_na_memoria(a.nome)
            log("      teste 4b: %s | lembrou: %s | %s" % (resumo_hermes(D["t4b"]), D["t4_lembrou"], D["t4_arquivo"]))
        else:
            log("      teste 4 pulado (sem --nome)")

        if not a.rapido:
            # 2b: o teste 2 de novo depois de o minuto virar. Se o prompt tiver o horário, o cache se perde.
            espera = max(a.pausa, 63 - agora_local(ctx.fuso).second)
            log("      teste 2b: esperando %d s o minuto virar..." % espera)
            time.sleep(espera)
            D["t2b"] = perguntar_hermes(ctx, PERGUNTA_1)
            log("      teste 2b: " + resumo_hermes(D["t2b"]))
    else:
        log("      pulado: o Hermes não está acessível")

    # 5. velocidade direto no Ollama
    etapa(5, TOTAL, "Velocidade do modelo direto no Ollama")
    bench = D["bench"] = {}
    casos = [
        ("sem_raciocinio", "sem raciocínio (think=false)", PERGUNTA_GERAR, False, 300),
        ("com_raciocinio", "com raciocínio (think=true)", PERGUNTA_GERAR, True, 800),
        ("padrao", "sem o parâmetro think (padrão do modelo)", PERGUNTA_1, None, 100),
    ]
    for chave, titulo, pergunta, think, limite in casos:
        try:
            bench[chave] = ollama_chat(ctx, pergunta, think=think, num_predict=limite)
            bench[chave]["_titulo"] = titulo
            log("      %s: %s tok/s" % (titulo, br(num(bench[chave]["_tps"]))))
        except Exception as e:
            bench[chave] = {"_titulo": titulo, "_erro": ctx.erro("Ollama " + titulo, descrever_erro(e))}
    rnd = random.Random()
    for chave, palavras in (("leitura_2k", 1000), ("leitura_8k", 4000)):
        titulo = "leitura de prompt (~%sk tokens)" % chave[-2]
        nonce = "%08x" % rnd.getrandbits(32)  # começo diferente a cada rodada: nada vem do cache
        texto = "Código %s. Leia o texto abaixo e responda apenas OK.\n\n%s" % (nonce, texto_longo(palavras, rnd))
        try:
            bench[chave] = ollama_chat(ctx, texto, think=False, num_predict=1)
            bench[chave]["_titulo"] = titulo
            log("      %s: %s tokens a %s tok/s" % (titulo, bench[chave].get("prompt_eval_count", "?"),
                                                    br(num(bench[chave]["_prompt_tps"], 0))))
        except Exception as e:
            bench[chave] = {"_titulo": titulo, "_erro": ctx.erro("Ollama " + titulo, descrever_erro(e))}

    # 6. prompt fixo
    etapa(6, TOTAL, "Tamanho do prompt fixo do Hermes na API (prompt-size)")
    saida, erro = hermes_cli("prompt-size", "--platform", "api_server", timeout=180)
    if saida is None:
        saida, erro = hermes_cli("prompt-size", timeout=180)
    D["prompt_size"] = saida.strip() if saida else None
    if saida is None:
        ctx.erro("hermes prompt-size", erro)

    # 7. RAM e contêineres
    etapa(7, TOTAL, "RAM e uso de cada contêiner")
    try:
        D["ram"] = ram()
        log("      disponível: %s de %s" % (gib(D["ram"]["MemAvailable"]), gib(D["ram"]["MemTotal"])))
    except Exception as e:
        D["ram"] = None
        ctx.erro("RAM", e)
    try:
        D["stats"] = docker_stats()
    except Exception as e:
        D["stats"] = []
        ctx.erro("docker stats", e)

    etapa(8, TOTAL, "Gerando o relatório")


# ---------------------------------------------------------------- relatório

def valor_hermes(D: dict, chave: str) -> str:
    r = D.get(chave)
    if not r:
        return "não medido"
    return resumo_hermes(r)


def montar_tabela(ctx: Contexto) -> list:
    D, a = ctx.dados, ctx.args
    linhas = []

    g = D.get("gpu")
    if g:
        ollama_mib = sum(float(x["mib"]) for x in g["apps"] if "ollama" in x["processo"].lower()
                         and x["mib"].replace(".", "").isdigit())
        v = "%s de %s MiB (livres: %s MiB)" % (num(g["usada"], 0), num(g["total"], 0), num(g["total"] - g["usada"], 0))
        if ollama_mib:
            v += "; Ollama: %s MiB" % num(ollama_mib, 0)
    else:
        v = "não medido"
    linhas.append(("VRAM total usada com o modelo carregado (`nvidia-smi`)", v))

    m = modelo_carregado(ctx, D.get("ps") or [])
    if m:
        v = "%s, contexto %s" % (processador(m), m.get("context_length", "?"))
    elif D.get("ps") is None:
        v = "não medido"
    else:
        v = "%s não estava carregado" % ctx.modelo
    linhas.append(("`ollama ps`, coluna PROCESSOR", v))

    b = D.get("bench", {})

    def tps(chave):
        x = b.get(chave) or {}
        if x.get("_erro"):
            return x["_erro"]
        return "%s tokens/s" % br(num(x.get("_tps"))) if x.get("_tps") else "não medido"

    linhas.append(("Geração, com raciocínio (`eval rate`, tokens/s)", tps("com_raciocinio")))
    linhas.append(("Geração, sem raciocínio (`--think=false`)", tps("sem_raciocinio")))

    partes = []
    for chave in ("leitura_2k", "leitura_8k"):
        x = b.get(chave) or {}
        if x.get("_prompt_tps"):
            partes.append("%s tokens/s (prompt de %s tokens)" % (br(num(x["_prompt_tps"], 0)), x.get("prompt_eval_count")))
    linhas.append(("Leitura do prompt (`prompt eval rate`, tokens/s)", "; ".join(partes) or "não medido"))

    padrao = b.get("padrao") or {}
    if padrao.get("_erro"):
        v = padrao["_erro"]
    elif "_pensou" in padrao:
        v = "Ollama sem o parâmetro think: %s" % ("sim" if padrao["_pensou"] else "não")
    else:
        v = "não medido"
    rac = D.get("hermes_raciocinio")
    if rac:
        v += "; pelo Hermes (teste 2): " + rac
    linhas.append(("O modelo raciocina antes de responder?", v))

    v = resumo_prompt_size(D.get("prompt_size") or "") or ("ver detalhes" if D.get("prompt_size") else "não medido")
    uso = (D.get("t2") or {}).get("uso") or {}
    if uso.get("prompt_tokens"):
        v = "%s tokens por pergunta (%s)" % (uso["prompt_tokens"], v)
    linhas.append(("Prompt fixo do Hermes (`prompt-size`)", v))

    v = valor_hermes(D, "t1")
    if D.get("frio_nota", "").startswith(("pulado", "erro")):
        v += " (%s)" % D["frio_nota"]
    linhas.append(("Teste 1: tempo frio", v))
    linhas.append(("Teste 2: tempo quente", valor_hermes(D, "t2")))
    if D.get("t2b"):
        linhas.append(("Teste 2b: quente, depois de o minuto virar", valor_hermes(D, "t2b")))

    if D.get("t3"):
        c = D["t3_conf"]
        v = "%s (esperado %s, resposta %s); %s" % (c["veredito"], c["esperado"], c["achado"] or "sem hora",
                                                   resumo_hermes(D["t3"]))
        ferr = unicos(D["t3"].get("ferramentas") or [])
        v += "; ferramentas: " + (", ".join(ferr) if ferr else "nenhuma")
    else:
        v = "não medido"
    linhas.append(("Teste 3: hora certa? tempo", v))

    if D.get("t4b"):
        v = "%s; %s" % (D["t4_lembrou"], D["t4_arquivo"])
        if D["t4b"].get("erro"):
            v += "; " + resumo_hermes(D["t4b"])
    elif a.nome:
        v = "não medido"
    else:
        v = "pulado (rode com --nome)"
    linhas.append(("Teste 4: lembrou o nome?", v))

    r = D.get("ram")
    if r:
        v = "%s disponíveis de %s" % (gib(r["MemAvailable"]), gib(r["MemTotal"]))
        swap = r.get("SwapTotal", 0) - r.get("SwapFree", 0)
        if swap > 50 * 1024 ** 2:
            v += "; swap em uso: %s" % gib(swap)
    else:
        v = "não medido"
    linhas.append(("RAM livre com tudo rodando (`free -h`)", v))
    return linhas


def observacoes(ctx: Contexto) -> list:
    D = ctx.dados
    obs = []
    m = modelo_carregado(ctx, D.get("ps") or [])
    if D.get("ps") is not None and not m and (D.get("t1") or {}).get("texto"):
        outros = ", ".join(x.get("name", "?") for x in D.get("ps") or []) or "nenhum"
        obs.append("Depois do teste 1, o `%s` não estava carregado (carregados: %s). "
                   "Confira se o Hermes usa esse modelo: `docker compose exec hermes hermes config get model`." %
                   (ctx.modelo, outros))
    if m:
        if processador(m) != "100% GPU":
            obs.append("O modelo não está 100%% na GPU (%s). Veja a linha do `num_gpu` em "
                       "\"Problemas comuns\" do docs/fase0.md." % processador(m))
        ctxlen = m.get("context_length")
        if ctxlen and ctx.num_ctx and str(ctxlen) != str(ctx.num_ctx):
            obs.append("O contexto carregado (%s) é diferente do JARVIS_NUM_CTX (%s)." % (ctxlen, ctx.num_ctx))
    g = D.get("gpu")
    if g and g["total"] - g["usada"] < 400:
        obs.append("Sobram menos de 400 MiB de VRAM. O Jellyfin pode falhar ao transcodificar.")

    t1, t2 = D.get("t1") or {}, D.get("t2") or {}
    if t2.get("t_resposta") and t2["t_resposta"] > 5:
        obs.append("A primeira palavra do teste 2 (quente) passou de 5 s. Primeira ação da Fase 1: "
                   "enxugar as ferramentas (o `prompt-size` mostra o peso de cada parte).")
    if (t1.get("texto") and t2.get("texto") and not t1.get("erro") and not t2.get("erro")
            and not D.get("frio_nota", "").startswith(("pulado", "erro"))):
        leitura = (D.get("bench", {}).get("leitura_8k") or {}).get("_prompt_tps")
        tokens1 = (t1.get("uso") or {}).get("prompt_tokens")
        if leitura and tokens1 and t1.get("t_token") and t2.get("t_token"):
            ler = tokens1 / leitura
            carga = max(0.0, t1["t_token"] - t2["t_token"] - ler)
            obs.append("O teste frio (%s até o 1º token) soma carregar o modelo (~%s) e ler o prompt inteiro "
                       "do zero (~%s para %s tokens). Sempre que o cache do prompt se perde (a Open WebUI usa "
                       "a GPU, o Honcho processa algo), a próxima pergunta paga de novo essa leitura."
                       % (seg(t1["t_token"]), seg(carga), seg(ler), tokens1))
        else:
            obs.append("O teste frio levou %s a mais que o quente: carregar o modelo e ler o prompt do zero." %
                       seg(max(0.0, t1["t_total"] - t2["t_total"])))

    t2b = D.get("t2b") or {}
    if t2b.get("t_token") and t2.get("t_token"):
        if t2b["t_token"] > 2 * t2["t_token"] + 1.5:
            obs.append("Depois de o minuto virar, o 1º token levou %s, contra %s no teste 2: o prompt foi relido "
                       "do zero. Provavelmente o horário no prompt muda a cada minuto (ou outro uso da GPU apagou "
                       "o cache). É o ponto mais importante para a Fase 1." % (seg(t2b["t_token"]), seg(t2["t_token"])))
        else:
            obs.append("O cache do prompt sobreviveu à virada do minuto (teste 2b: %s até o 1º token)." %
                       seg(t2b["t_token"]))

    for chave, titulo in (("t1", "1"), ("t2", "2"), ("t2b", "2b"), ("t3", "3"), ("t4a", "4a"), ("t4b", "4b")):
        r = D.get(chave) or {}
        if r and not r.get("texto") and not r.get("erro"):
            ferr = unicos(r.get("ferramentas") or [])
            obs.append("No teste %s o Jarvis terminou sem responder em texto%s. Na voz, isso vira silêncio." %
                       (titulo, (" (usou: %s)" % ", ".join(ferr)) if ferr else ""))

    # Raciocínio pelo Hermes
    if t2 and not t2.get("erro"):
        if t2.get("raciocinio_chars"):
            D["hermes_raciocinio"] = "sim, %d caracteres de raciocínio antes de responder" % t2["raciocinio_chars"]
        else:
            uso = t2.get("uso") or {}
            saida = uso.get("completion_tokens")
            estimado = len(t2.get("texto", "")) / 3.5
            if saida and saida > 3 * estimado + 50:
                D["hermes_raciocinio"] = ("provavelmente sim: %s tokens gerados para uma resposta de ~%d "
                                          "(o raciocínio não aparece no stream)" % (saida, estimado))
            elif saida:
                D["hermes_raciocinio"] = "não (%s tokens gerados)" % saida
            else:
                D["hermes_raciocinio"] = "não apareceu raciocínio no stream"
        if D["hermes_raciocinio"].startswith(("sim", "provavelmente")):
            obs.append("O modelo raciocina nas respostas pelo Hermes, o que atrasa a voz. Confira se "
                       "`docker compose exec hermes hermes config get agent.reasoning_effort` mostra `false`.")

    # Cache de prefixo: a leitura do prompt deveria ser quase instantânea no teste 2.
    leitura = (D.get("bench", {}).get("leitura_8k") or {}).get("_prompt_tps")
    uso = t2.get("uso") or {}
    primeiro = t2.get("t_token") or t2.get("t_resposta")
    if leitura and uso.get("prompt_tokens") and primeiro:
        estimado = uso["prompt_tokens"] / leitura
        if estimado > 1.5 and primeiro >= 0.8 * estimado:
            obs.append("No teste 2, o primeiro token levou %s; ler o prompt inteiro (%s tokens) leva ~%s. "
                       "Parece que o prompt é relido do zero a cada pergunta, sem aproveitar o cache. "
                       "Vale investigar na Fase 1." % (seg(primeiro), uso["prompt_tokens"], seg(estimado)))

    t3 = D.get("t3") or {}
    if t3.get("texto") and not t3.get("ferramentas") and D["t3_conf"]["veredito"] != "certa":
        obs.append("No teste 3 o Jarvis não usou ferramenta e a hora não bateu: ele pode estar inventando a hora.")

    padrao = D.get("bench", {}).get("com_raciocinio") or {}
    if padrao.get("_pensou") and not (padrao.get("_texto") or "").strip():
        obs.append("Com think=true, o modelo gastou os 800 tokens raciocinando sem chegar à resposta.")

    r = D.get("ram")
    if r:
        disp = r["MemAvailable"] / 1024 ** 3
        if D.get("honcho") and disp < 1.5:
            obs.append("Menos de 1,5 GB de RAM disponível com o Honcho ligado. Risco de o sistema usar swap.")
        elif not D.get("honcho") and disp < 3.5:
            obs.append("Menos de 3,5 GB de RAM disponível: pode faltar memória para o Honcho (ele usa 2,5 a 3 GB).")
    if D.get("honcho"):
        obs.append("Com o Honcho ativo, o deriver pode ter usado a GPU durante as medições e atrasado alguma resposta.")
    return obs


def bloco(texto: str) -> str:
    return "```\n" + texto.replace("```", "'''") + "\n```"


def citar(texto: str) -> str:
    return "\n".join("> " + l for l in (texto or "(vazio)").splitlines())


def relatorio(ctx: Contexto, rotulo: str, quando: datetime, tabela: list, obs: list) -> str:
    D, a = ctx.dados, ctx.args
    L = []
    L.append("# Medições do Jarvis: %s (%s)" % (quando.strftime("%d/%m/%Y %H:%M"), rotulo))
    L.append("")
    L.append("Gerado por `scripts/medicoes.py`. Tempos medidos pela API, do envio da pergunta até a "
             "primeira palavra da resposta e até o fim.")
    L.append("")
    L.append("## Tabela")
    L.append("")
    L.append("| Medida | Valor |")
    L.append("|---|---|")
    for k, v in tabela:
        L.append("| %s | %s |" % (celula(k), celula(v)))
    L.append("")
    L.append("## Observações")
    L.append("")
    L.extend(["- " + o for o in obs] or ["- Nada fora do esperado."])
    L.append("")
    L.append("## Ambiente")
    L.append("")
    L.append("- Ollama %s; Hermes: %s" % (D.get("ollama_versao", "?"), D.get("hermes_versao", "?")))
    L.append("- Modelo `%s` (%s)" % (ctx.modelo, D.get("detalhes_modelo", "?")))
    L.append("- Parâmetros: `%s`" % (D.get("parametros") or "?"))
    L.append("- Capacidades: %s" % D.get("capacidades", "?"))
    L.append("- Honcho: %s" % ("ativo" if D.get("honcho") else "inativo"))
    g = D.get("gpu")
    if g:
        L.append("- GPU: %s, driver %s (lido no %s)" % (g["nome"], g["driver"], g["via"]))
    L.append("- Teste frio: %s" % D.get("frio_nota", "?"))
    L.append("")

    L.append("## Testes pelo Hermes")
    L.append("")
    L.append("| Teste | 1º byte | 1º token | 1ª palavra | Total | Raciocínio (caracteres) | Ferramentas | Tokens entrada/saída |")
    L.append("|---|---|---|---|---|---|---|---|")
    nomes = [("t1", "1 (frio)"), ("t2", "2 (quente)"), ("t2b", "2b (quente, minuto virado)"), ("t3", "3 (hora)"), ("t4a", "4a (guardar nome)"),
             ("t4b", "4b (lembrar nome)")]
    for chave, titulo in nomes:
        r = D.get(chave)
        if not r:
            continue
        uso = r.get("uso") or {}
        ferr = unicos(r.get("ferramentas") or [])
        L.append("| %s | %s | %s | %s | %s | %d | %s | %s/%s |" % (
            titulo, seg(r.get("t_byte")), seg(r.get("t_token")), seg(r.get("t_resposta")), seg(r.get("t_total")),
            r.get("raciocinio_chars", 0), celula(", ".join(ferr) or "nenhuma"),
            uso.get("prompt_tokens", "?"), uso.get("completion_tokens", "?")))
    L.append("")
    for chave, titulo in nomes:
        r = D.get(chave)
        if not r:
            continue
        L.append("**Teste %s.** Pergunta: \"%s\"" % (titulo, r["pergunta"]))
        L.append("")
        if r.get("erro"):
            L.append("Erro: `%s`" % r["erro"])
            L.append("")
        L.append(citar(r.get("texto", "")[:1500]))
        if r.get("eventos"):
            L.append("")
            L.append("Eventos extras no stream: " + ", ".join("%s (%d)" % kv for kv in r["eventos"].items()))
        L.append("")

    L.append("## Velocidade direto no Ollama")
    L.append("")
    L.append("| Caso | Tokens gerados | Geração (tok/s) | Tokens de prompt | Leitura (tok/s) | Raciocinou? | Tempo total |")
    L.append("|---|---|---|---|---|---|---|")
    for chave in ("sem_raciocinio", "com_raciocinio", "padrao", "leitura_2k", "leitura_8k"):
        x = D.get("bench", {}).get(chave)
        if not x:
            continue
        if x.get("_erro"):
            L.append("| %s | %s | | | | | |" % (x["_titulo"], celula(x["_erro"])))
            continue
        geracao = br(num(x.get("_tps"))) if (x.get("eval_count") or 0) > 1 else "-"
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            x["_titulo"], x.get("eval_count", "?"), geracao, x.get("prompt_eval_count", "?"),
            br(num(x.get("_prompt_tps"), 0)), "sim" if x.get("_pensou") else "não", seg(x.get("_parede"))))
    L.append("")

    if g and g["apps"]:
        L.append("## Processos na GPU")
        L.append("")
        L.append("| PID | Processo | MiB |")
        L.append("|---|---|---|")
        for p in g["apps"]:
            L.append("| %s | %s | %s |" % (p["pid"], celula(p["processo"]), p["mib"]))
        L.append("")

    if D.get("stats"):
        L.append("## Contêineres (`docker stats`)")
        L.append("")
        L.append("| Contêiner | RAM | CPU |")
        L.append("|---|---|---|")
        for nome, mem, cpu, _ in D["stats"]:
            L.append("| %s | %s | %s |" % (nome, mem, cpu))
        L.append("")

    if D.get("prompt_size"):
        L.append("## `hermes prompt-size`")
        L.append("")
        L.append(bloco(D["prompt_size"][:6000]))
        L.append("")
    if D.get("honcho_status"):
        L.append("## `hermes honcho status`")
        L.append("")
        L.append(bloco(D["honcho_status"][:3000]))
        L.append("")
    if ctx.erros:
        L.append("## Erros durante a medição")
        L.append("")
        L.extend("- " + celula(e) for e in ctx.erros)
        L.append("")
    return ctx.limpa("\n".join(L))


def imprimir_tabela(tabela: list) -> None:
    largura = max(len(k) for k, _ in tabela)
    log("")
    for k, v in tabela:
        log("%s  %s" % (k.replace("`", "").ljust(largura), v.replace("`", "")))
    log("")


# ---------------------------------------------------------------- main

def main() -> int:
    p = argparse.ArgumentParser(description="Mede a Fase 0 do Jarvis e gera um relatório.")
    p.add_argument("--nome", help="seu nome, para o teste de memória (sem ele, o teste 4 é pulado)")
    p.add_argument("--rotulo", help="rótulo do relatório, por exemplo sem-honcho ou com-honcho")
    p.add_argument("--pular-frio", action="store_true", help="não tira o modelo da VRAM antes do teste 1")
    p.add_argument("--sem-memoria", action="store_true", help="pula o teste 4 (memória)")
    p.add_argument("--rapido", action="store_true", help="pula o teste 2b (economiza até 1 minuto de espera)")
    p.add_argument("--pausa", type=float, default=5.0, help="segundos entre as perguntas ao Hermes (padrão 5)")
    p.add_argument("--timeout", type=float, default=600.0, help="limite por pergunta, em segundos (padrão 600)")
    p.add_argument("--ollama", default="http://127.0.0.1:11434", help=argparse.SUPPRESS)
    p.add_argument("--hermes", default="http://127.0.0.1:8642", help=argparse.SUPPRESS)
    a = p.parse_args()

    env = ler_env(RAIZ / ".env")
    if not env:
        log("Aviso: não encontrei %s. Rode o script de dentro da pasta do projeto." % (RAIZ / ".env"))
    if a.sem_memoria:
        a.nome = None
    elif not a.nome and sys.stdin.isatty():
        try:
            a.nome = input("Seu nome, para o teste de memória (Enter para pular): ").strip()
        except EOFError:
            a.nome = ""
    a.nome = (a.nome or "").strip()[:60] or None
    if a.nome and re.fullmatch(r"[A-Z0-9]+_[A-Z0-9_]+", a.nome):
        log("Aviso: \"%s\" parece um exemplo, não um nome. O teste 4 foi pulado; use --nome com o seu nome." % a.nome)
        a.nome = None

    ctx = Contexto(a, env)
    quando = agora_local(ctx.fuso)
    log("Medições do Jarvis. Leva de 3 a 6 minutos; não use o Jarvis nem a Open WebUI enquanto isso.")
    log("")
    inicio = time.monotonic()
    try:
        executar(ctx)
    except KeyboardInterrupt:
        log("\nInterrompido. Gerando o relatório com o que foi medido até aqui.")
        ctx.erros.append("medição interrompida com Ctrl+C")

    rotulo = a.rotulo or ("com-honcho" if ctx.dados.get("honcho") else "sem-honcho")
    rotulo = re.sub(r"[^a-z0-9]+", "-", sem_acento(rotulo)).strip("-") or "medicao"
    pasta = RAIZ / "medicoes"
    pasta.mkdir(exist_ok=True)
    base = "medicao-%s-%s" % (quando.strftime("%Y%m%d-%H%M"), rotulo)
    try:
        obs = observacoes(ctx)  # antes da tabela: preenche o diagnóstico de raciocínio
        tabela = montar_tabela(ctx)
        texto = relatorio(ctx, rotulo, quando, tabela, obs)
    except Exception:
        import traceback
        bruto = pasta / (base + "-dados.json")
        conteudo = {"dados": ctx.dados, "erros": ctx.erros, "falha": traceback.format_exc()}
        bruto.write_text(ctx.limpa(json.dumps(conteudo, indent=2, ensure_ascii=False, default=str)), encoding="utf-8")
        log("\nFalha ao montar o relatório. Os dados medidos estão em %s; mande esse arquivo." % bruto.relative_to(RAIZ))
        return 1
    arquivo = pasta / (base + ".md")
    arquivo.write_text(texto + "\n", encoding="utf-8")

    imprimir_tabela(tabela)
    for o in obs:
        log("- " + o.replace("`", ""))
    if ctx.erros:
        log("\n%d etapa(s) com erro; os detalhes estão no relatório." % len(ctx.erros))
    log("\nConcluído em %s. Relatório: %s" % (seg(time.monotonic() - inicio), arquivo.relative_to(RAIZ)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

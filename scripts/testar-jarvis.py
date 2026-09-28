#!/usr/bin/env python3
"""Teste prático do Jarvis: faz perguntas de verdade pela API do Hermes e confere as respostas.

Uso, na pasta do projeto e com tudo rodando:
    python3 scripts/testar-jarvis.py                      todos os casos que só leem dados
    python3 scripts/testar-jarvis.py --listar             mostra os casos e sai
    python3 scripts/testar-jarvis.py --caso hora --caso dia
    python3 scripts/testar-jarvis.py --categoria clima
    python3 scripts/testar-jarvis.py --incluir-escrita    inclui os casos que criam dados (agenda, memória)
    python3 scripts/testar-jarvis.py --repetir 3          roda cada caso 3 vezes e mostra a consistência

Os casos ficam em testes/jarvis-casos.json (o formato está explicado no próprio arquivo).
Cada caso é uma conversa nova; um caso com vários turnos é uma conversa só.
Para cada resposta, confere: ferramentas usadas e proibidas, trechos que devem ou não aparecer, hora e data,
formato para voz e o tempo até a primeira palavra.

O relatório vai para medicoes/teste-AAAAMMDD-HHMM.md, com as perguntas e as respostas completas.
Sai com código 0 se todos os casos que rodaram passaram, 1 se não.
Só usa a biblioteca padrão do Python (3.8 ou mais novo). A chave da API nunca aparece na tela nem no relatório.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.dont_write_bytecode = True  # não deixa __pycache__ na pasta scripts
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_cliente as jc  # noqa: E402

CASOS_PADRAO = jc.RAIZ / "testes" / "jarvis-casos.json"
LIMITE_SIMPLES = 3.0     # segundos até a 1ª palavra numa pergunta sem ferramenta
LIMITE_FERRAMENTA = 10.0  # ... e numa pergunta que usa ferramenta (várias chamadas: Moodle, e-mail)
META_LISTA, TOTAL_LISTA = 8, 10
SEMPRE_PROIBIDAS = ["agenda_criar"]  # criar evento sem o turno pedir é sempre falha
VERIFICACOES = ("hora", "data", "sem_markdown", "fim_de_semana")  # sem_markdown: hoje vale para todo turno
INTEGRACOES = {"moodle": ("Moodle", "Moodle não configurado"),
               "google": ("Google", "Google não configurado"),
               "busca": ("busca", "busca na web não configurada")}
CAMPOS_LISTA = ("ferramentas", "ferramentas_proibidas", "deve_conter", "nao_deve_conter", "nao_deve_afirmar")
# Um trecho com negação ou condição não conta como afirmação ("não sei se estão online").
NEGACAO = re.compile(r"\b(nao|nunca|nem|sem|se|caso|talvez|quando|poderia|podera|poderei|saber)\b")
CLAUSULAS = re.compile(r"[.!?;:,\n]|\bmas\b|\bporem\b|\bentretanto\b|\bcontudo\b")
DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
DIAS_FALADOS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
MESES_FALADOS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
                 "outubro", "novembro", "dezembro"]

# Oferecer o que nenhuma ferramenta faz: avisos, lembretes, criar skills
PROMESSAS = [
    r"\b(quer|deseja|gostaria) que eu (te |lhe )?(avise|lembre)\b",
    r"\b(posso|vou|irei) (te |lhe )?avisar\b",
    r"\b(posso|vou|irei) (te |lhe )lembrar\b",  # "vou lembrar disso" é a memória, e vale
    r"\b(concorda|aprova|autoriza)\w* (com )?a criacao\b",
    r"\bquer que eu (crie|implemente)\b[^.?!]*\bskill",  # criar evento existe (agenda_criar); skill, não
]
_DIA = r"(\d{1,2}|primeiro)(?:º|°|o)?"
_SEMANA = r"(segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?"
_MES = r"(%s)" % "|".join(MESES)
# "sábado, 30 de setembro" e "30 de setembro, sábado" (mas não "3 de outubro, domingo, 4", que é outro dia)
DATA_COM_SEMANA = [
    (re.compile(r"\b%s,?\s+(?:dia\s+)?%s\s+de\s+%s(?:\s+de\s+(\d{4}))?" % (_SEMANA, _DIA, _MES)), (0, 1, 2, 3)),
    (re.compile(r"\b(?:dia\s+)?%s\s+de\s+%s(?:\s+de\s+(\d{4}))?,?\s+%s(?!,?\s*(?:dia\s+)?\d)"
                % (_DIA, _MES, _SEMANA)), (3, 0, 1, 2)),
]

log = jc.log


# ---------------------------------------------------------------- casos

def lista(turno: dict, campo: str) -> list:
    v = turno.get(campo)
    if v is None:
        return []
    return [v] if isinstance(v, str) else list(v)


def verificacoes(turno: dict) -> list:
    return lista(turno, "verificar")


def buscar(padrao: str, texto_normalizado: str):
    return re.search(jc.sem_acento_regex(padrao), texto_normalizado, re.I)


def validar(casos) -> list:
    """Confere o arquivo de casos e devolve a lista de problemas (vazia se está tudo certo)."""
    if not isinstance(casos, list) or not casos:
        return ["o arquivo precisa ter uma lista de casos (em \"casos\")"]
    problemas, vistos = [], []
    for n, c in enumerate(casos, 1):
        if not isinstance(c, dict):
            problemas.append("caso %d: não é um objeto JSON" % n)
            continue
        nome = c.get("id")
        onde = "caso %s" % (nome or n)
        if not isinstance(nome, str) or not nome.strip():
            problemas.append("caso %d: falta o id" % n)
        elif nome in vistos:
            problemas.append("%s: id repetido" % onde)
        if c.get("requer") not in (None,) + tuple(INTEGRACOES):
            problemas.append("%s: requer deve ser null, %s" % (onde, ", ".join('"%s"' % i for i in INTEGRACOES)))
        dep = c.get("requer_caso")
        if dep is not None and dep not in vistos:
            problemas.append("%s: requer_caso \"%s\" precisa ser um caso que vem antes dele no arquivo" % (onde, dep))
        for campo in ("pausa_antes",):
            if c.get(campo) is not None and (not isinstance(c[campo], (int, float)) or c[campo] < 0):
                problemas.append("%s: %s deve ser um número de segundos" % (onde, campo))
        turnos = c.get("turnos")
        if not isinstance(turnos, list) or not turnos:
            problemas.append("%s: precisa de pelo menos um turno em \"turnos\"" % onde)
            turnos = []
        for i, t in enumerate(turnos, 1):
            ot = "%s, turno %d" % (onde, i)
            if not isinstance(t, dict):
                problemas.append("%s: não é um objeto JSON" % ot)
                continue
            if not isinstance(t.get("pergunta"), str) or not t["pergunta"].strip():
                problemas.append("%s: falta a pergunta" % ot)
            for campo in CAMPOS_LISTA + ("verificar",):
                v = t.get(campo)
                if v is not None and not isinstance(v, str) and not (
                        isinstance(v, list) and all(isinstance(x, str) for x in v)):
                    problemas.append("%s: %s deve ser uma lista de textos" % (ot, campo))
            for campo in ("deve_conter", "nao_deve_conter", "nao_deve_afirmar"):
                for padrao in lista(t, campo):
                    if not isinstance(padrao, str):
                        continue
                    try:
                        re.compile(jc.sem_acento_regex(padrao))
                    except re.error as e:
                        problemas.append("%s: regex inválida em %s: %s (%s)" % (ot, campo, padrao, e))
            for v in lista(t, "verificar"):
                if v not in VERIFICACOES:
                    problemas.append("%s: verificar \"%s\" não existe (use %s)" % (ot, v, ", ".join(VERIFICACOES)))
            maximo = t.get("max_chamadas")
            if maximo is not None and (not isinstance(maximo, int) or isinstance(maximo, bool) or maximo < 0):
                problemas.append("%s: max_chamadas deve ser um número inteiro" % ot)
            lim = t.get("max_primeira_palavra")
            if lim is not None and (not isinstance(lim, (int, float)) or lim <= 0):
                problemas.append("%s: max_primeira_palavra deve ser um número de segundos" % ot)
        if isinstance(nome, str):
            vistos.append(nome)
    return problemas


def carregar_casos(caminho: Path) -> list:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except OSError as e:
        raise SystemExit("Não consegui ler %s (%s)." % (caminho, e.strerror or e))
    except ValueError as e:
        raise SystemExit("%s não é um JSON válido: %s" % (caminho, e))
    casos = dados.get("casos") if isinstance(dados, dict) else dados
    problemas = validar(casos)
    if problemas:
        raise SystemExit("Problemas em %s:\n  - %s" % (caminho, "\n  - ".join(problemas)))
    return casos


def selecionar(casos: list, ids: list, categorias: list):
    """Filtra por --caso e --categoria e inclui os casos de que os escolhidos dependem."""
    if not ids and not categorias:
        return casos, []
    por_id = {c["id"]: c for c in casos}
    desconhecidos = [i for i in ids if i not in por_id]
    if desconhecidos:
        raise SystemExit("Caso desconhecido: %s. Veja os ids com --listar." % ", ".join(desconhecidos))
    existentes = {jc.normalizar(c.get("categoria") or "") for c in casos}
    cats = {jc.normalizar(x) for x in categorias}
    faltam = sorted(cats - existentes)
    if faltam:
        raise SystemExit("Categoria sem casos: %s. Existem: %s." % (", ".join(faltam), ", ".join(sorted(existentes - {""}))))
    escolhidos = set(ids) | {c["id"] for c in casos if jc.normalizar(c.get("categoria") or "") in cats}
    extras, mudou = [], True
    while mudou:
        mudou = False
        for c in casos:
            dep = c.get("requer_caso")
            if c["id"] in escolhidos and dep and dep not in escolhidos:
                escolhidos.add(dep)
                extras.append((dep, c["id"]))
                mudou = True
    return [c for c in casos if c["id"] in escolhidos], extras


def rotulo_lista(caso: dict) -> str:
    if not caso.get("lista_usuario"):
        return ""
    return "P%s" % caso["numero_usuario"] if caso.get("numero_usuario") is not None else "P?"


def perguntas_do_caso(caso: dict) -> str:
    return " / ".join(t["pergunta"] for t in caso["turnos"])


def listar(casos: list) -> None:
    largura = max(len(c["id"]) for c in casos)
    log("%s  %-13s %-7s %-5s %s" % ("id".ljust(largura), "categoria", "requer", "lista", "pergunta"))
    for c in casos:
        log("%s  %-13s %-7s %-5s %s%s" % (c["id"].ljust(largura), c.get("categoria") or "-", c.get("requer") or "-",
                                          rotulo_lista(c) or "-", perguntas_do_caso(c),
                                          "  [altera dados: só com --incluir-escrita]" if c.get("altera") else ""))
    log("")
    log("P1 a P10: as 10 perguntas da sua lista (meta da Fase 1: acertar %d de %d)." % (META_LISTA, TOTAL_LISTA))


# ---------------------------------------------------------------- contexto

class Contexto:
    def __init__(self, args, env: dict, raiz: Path):
        self.args = args
        self.raiz = raiz
        self.url = args.hermes.rstrip("/")
        self.chave = jc.chave_api(env)
        self.fuso = env.get("TZ") or "America/Sao_Paulo"
        self.timeout = args.timeout
        self.recursos = None
        self.recursos_erro = None
        self.conferir_hora = None
        self.conferir_erro = None
        self.aquecimento = None
        self.estilo = jc.Estilo()
        self.modelo = descrever_modelo(env)
        self.modelo_base = env.get("BASE_MODEL") or "qwen3.5:4b"

    def integracao(self, nome: str):
        """True (configurada), False (não configurada) ou None (não deu para saber)."""
        if self.recursos is None or nome not in self.recursos:
            return None
        v = self.recursos[nome]
        if isinstance(v, (list, tuple, dict)):
            return len(v) > 0
        if isinstance(v, str):
            return jc.normalizar(v).strip() not in ("", "false", "nao", "0", "no")
        return bool(v)

    def descrever_recursos(self) -> str:
        if self.recursos is None:
            return "desconhecidas (%s); os casos que dependem delas rodam assim mesmo" % self.recursos_erro
        partes = []
        for nome, (titulo, _) in INTEGRACOES.items():
            estado = self.integracao(nome)
            v = self.recursos.get(nome)
            if estado is None:
                partes.append("%s: ?" % titulo)
            elif estado and isinstance(v, (list, tuple)):
                partes.append("%s: %s" % (titulo, ", ".join(str(x) for x in v)))
            else:
                partes.append("%s: %s" % (titulo, "sim" if estado else "não"))
        return " · ".join(partes)


def descrever_modelo(env: dict) -> str:
    """'jarvis-qwen = qwen3.5:4b · temperatura 0.5 · ...', com os mesmos padrões do docker-compose.yml."""
    def valor(nome, padrao):
        return env[nome] if nome in env else padrao
    partes = ["%s = %s" % (valor("JARVIS_MODEL", "jarvis-qwen") or "jarvis-qwen",
                           valor("BASE_MODEL", "qwen3.5:4b") or "qwen3.5:4b")]
    for nome, rotulo, padrao in (("JARVIS_TEMPERATURE", "temperatura", "0.5"), ("JARVIS_TOP_P", "top_p", "0.9"),
                                 ("JARVIS_TOP_K", "top_k", "20"),
                                 ("JARVIS_PRESENCE_PENALTY", "presence_penalty", "0.3"),
                                 ("JARVIS_NUM_GPU", "camadas na GPU", "")):
        v = valor(nome, padrao)
        partes.append("%s %s" % (rotulo, v if v != "" else "padrão"))
    return " · ".join(partes)


def nome_de_arquivo(texto: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", jc.normalizar(texto)).strip("-.")[:40]


# ---------------------------------------------------------------- conferências

def unicos(itens: list) -> list:
    vistos, saida = set(), []
    for i in itens:
        if i not in vistos:
            vistos.add(i)
            saida.append(i)
    return saida


def curtas(r: dict) -> list:
    return unicos([jc.nome_curto(f) for f in r.get("ferramentas") or []])


def trecho(original: str, normalizado: str, m) -> str:
    # Sem acentos o texto costuma ter o mesmo tamanho: dá para mostrar o trecho original.
    if len(original) == len(normalizado):
        return original[m.start():m.end()]
    return m.group(0)


def clausulas(texto_norm: str):
    """Posições (início, fim) de cada trecho entre pontuação ou "mas": a negação vale só dentro do trecho."""
    pos = 0
    for m in CLAUSULAS.finditer(texto_norm):
        yield pos, m.start()
        pos = m.end()
    yield pos, len(texto_norm)


def conferir_data(texto_norm: str, antes, depois) -> bool:
    for ref in {antes.date(), depois.date()}:
        dia = re.search(r"(?<![\d:h])0?%d(?:º|°|o)?(?!\d)" % ref.day, texto_norm) or (
            ref.day == 1 and "primeiro" in texto_norm)
        mes = MESES[ref.month - 1] in texto_norm or re.search(
            r"(?<!\d)0?%d/0?%d(?!\d)" % (ref.day, ref.month), texto_norm)
        semana = re.search(r"\b%s\b" % DIAS[ref.weekday()], texto_norm)
        if dia and (mes or semana):
            return True
    return False


def _data_perto(dia: int, mes: int, ano, hoje: date) -> date | None:
    """A data com esse dia e mês mais perto de hoje (ou no ano dito). None se não existe (31 de setembro)."""
    candidatas = []
    for a in ([int(ano)] if ano else [hoje.year - 1, hoje.year, hoje.year + 1]):
        try:
            candidatas.append(date(a, mes, dia))
        except ValueError:
            pass
    return min(candidatas, key=lambda d: abs((d - hoje).days)) if candidatas else None


def datas_incoerentes(texto_norm: str, hoje: date) -> list:
    """Dias da semana que não batem com a data ao lado ("sábado, 30 de setembro" em 2026 é quarta)."""
    erradas = []
    for padrao, ordem in DATA_COM_SEMANA:
        for m in padrao.finditer(texto_norm):
            semana, dia, mes, ano = (m.group(i + 1) for i in ordem)
            d = _data_perto(1 if dia == "primeiro" else int(dia), MESES.index(mes) + 1, ano, hoje)
            if d is None:
                erradas.append((m.start(), "\"%s\" não existe" % m.group(0).strip()))
            elif DIAS[d.weekday()] != semana:
                erradas.append((m.start(), "\"%s\" (%s é %s)" % (m.group(0).strip(), d.strftime("%d/%m/%Y"),
                                                                 DIAS_FALADOS[d.weekday()])))
    return [texto for _, texto in sorted(erradas)]  # na ordem em que aparecem


def promessas(texto_norm: str) -> list:
    return [m.group(0) for p in PROMESSAS for m in [re.search(p, texto_norm)] if m]


def proximo_fim_de_semana(hoje: date) -> tuple:
    """O mesmo do jarvis-tools: no sábado, este; no domingo, o da semana que vem."""
    sabado = hoje + timedelta(days=(5 - hoje.weekday()) % 7)
    return sabado, sabado + timedelta(days=1)


def conferir_fim_de_semana(texto_norm: str, hoje: date) -> bool:
    for d in proximo_fim_de_semana(hoje):
        if re.search(r"(?<!\d)0?%d(?:º|°|o)?\s+de\s+%s\b" % (d.day, MESES[d.month - 1]), texto_norm) or \
                re.search(r"(?<!\d)0?%d/0?%d(?!\d)" % (d.day, d.month), texto_norm) or \
                (d.day == 1 and re.search(r"\bprimeiro de %s\b" % MESES[d.month - 1], texto_norm)):
            return True
    return False


def avaliar(ctx: Contexto, caso: dict, turno: dict, r: dict, antes, depois):
    """Confere um turno. Devolve (falhas, avisos, falhas_de_tempo, parar)."""
    falhas, avisos, tempo = [], [], []
    texto = r.get("texto") or ""
    norm = jc.normalizar(texto)
    usadas = r.get("ferramentas") or []
    parar = False

    if r.get("erro"):
        falhas.append("erro no stream: %s" % r["erro"])
    if not texto:
        falhas.append("resposta vazia (na voz, silêncio)%s" %
                      ((" depois de usar " + ", ".join(curtas(r))) if usadas else ""))
        parar = True

    esperadas = lista(turno, "ferramentas")
    if esperadas and not any(jc.ferramenta_bate(u, e) for u in usadas for e in esperadas):
        motivo = "não usou %s (usou: %s)" % (" ou ".join(esperadas), ", ".join(curtas(r)) or "nenhuma")
        if caso.get("requer") and ctx.integracao(caso["requer"]) is None:
            motivo += " (ferramenta indisponível?)"
        falhas.append(motivo)

    proibidas = lista(turno, "ferramentas_proibidas")
    proibidas += [p for p in SEMPRE_PROIBIDAS if p not in proibidas and not any(jc.ferramenta_bate(p, e) for e in esperadas)]
    for u in unicos(usadas):
        if any(jc.ferramenta_bate(u, p) for p in proibidas):
            falhas.append("usou ferramenta proibida: %s" % jc.nome_curto(u))
            parar = True

    if texto:
        for padrao in lista(turno, "deve_conter"):
            if not buscar(padrao, norm):
                falhas.append("faltou mencionar: %s" % padrao)
        for padrao in lista(turno, "nao_deve_conter"):
            m = buscar(padrao, norm)
            if m:
                falhas.append("disse \"%s\"" % trecho(texto, norm, m))
        ja_citadas = set()
        for padrao in lista(turno, "nao_deve_afirmar"):
            for ini, fim in clausulas(norm):
                clausula = norm[ini:fim]
                if (ini, fim) in ja_citadas or NEGACAO.search(clausula) or not buscar(padrao, clausula):
                    continue
                ja_citadas.add((ini, fim))
                original = texto[ini:fim] if len(texto) == len(norm) else clausula
                falhas.append("afirmou \"%s\"" % original.strip()[:80])

        conferir = verificacoes(turno)
        if "hora" in conferir:
            if ctx.conferir_hora is None:
                avisos.append("hora não conferida: não consegui usar o scripts/medicoes.py (%s)" % ctx.conferir_erro)
            else:
                c = ctx.conferir_hora(texto, antes, depois)
                esperado = "%dh%02d" % (antes.hour, antes.minute)
                if c.get("achado") is None:
                    falhas.append("não achei a hora na resposta (esperado %s)" % esperado)
                elif c.get("veredito") == "errada":
                    falhas.append("hora errada: disse %s, esperado %s" % (c["achado"], esperado))
        if "data" in conferir and not conferir_data(norm, antes, depois):
            falhas.append("data errada ou incompleta (esperado: %s, %d de %s)" % (
                DIAS_FALADOS[antes.weekday()], antes.day, MESES_FALADOS[antes.month - 1]))
        if "fim_de_semana" in conferir and not conferir_fim_de_semana(norm, antes.date()):
            sabado, domingo = proximo_fim_de_semana(antes.date())
            falhas.append("não falou do próximo fim de semana (sábado, %d, e domingo, %d de %s)" % (
                sabado.day, domingo.day, MESES_FALADOS[domingo.month - 1]))
        md = jc.detectar_markdown(texto)
        if md:  # tudo pode virar voz: markdown reprova em todo turno
            falhas.append("formato ruim para voz: %s" % ", ".join(md))
        for errada in datas_incoerentes(norm, antes.date()):
            falhas.append("dia da semana errado: %s" % errada)
        for promessa in promessas(norm):
            falhas.append("ofereceu o que nenhuma ferramenta faz: \"%s\"" % promessa)

        maximo = turno.get("max_chamadas")
        if maximo is not None and len(usadas) > maximo:
            falhas.append("chamou ferramentas %d vezes (máximo %d): %s" % (
                len(usadas), maximo, ", ".join(jc.nome_curto(u) for u in usadas)))

        limite = turno.get("max_primeira_palavra")
        if limite is None:
            limite = LIMITE_FERRAMENTA if (esperadas or usadas) else LIMITE_SIMPLES
        tp = r.get("t_primeira_palavra")
        if tp is not None and tp > limite:
            msg = "1ª palavra em %s (limite %s)" % (jc.seg(tp), jc.seg(limite))
            falhas.append(msg)
            tempo.append(msg)

    if not r.get("completo") and not r.get("erro"):
        avisos.append("o stream terminou sem [DONE]: a resposta pode estar incompleta")
    return falhas, avisos, tempo, parar


def rodar_caso(ctx: Contexto, caso: dict) -> dict:
    """Roda os turnos de um caso numa conversa nova. Ctrl+C sobe como KeyboardInterrupt."""
    ex = {"status": None, "turnos": [], "falhas": [], "avisos": [], "tempo": [], "nao_rodou": None}
    mensagens = []
    varios = len(caso["turnos"]) > 1
    for i, turno in enumerate(caso["turnos"], 1):
        prefixo = ("turno %d: " % i) if varios else ""
        mensagens.append({"role": "user", "content": turno["pergunta"]})
        antes = jc.agora_local(ctx.fuso)
        r = jc.conversar(ctx.url, ctx.chave, mensagens, timeout=ctx.timeout)
        depois = jc.agora_local(ctx.fuso)
        if r.get("cancelado"):
            raise KeyboardInterrupt
        t = {"pergunta": turno["pergunta"], "r": r, "falhas": [], "avisos": []}
        ex["turnos"].append(t)
        if r.get("erro") and not r.get("texto"):
            ex["status"] = "ERRO"
            t["falhas"] = ["erro: " + r["erro"]]
            ex["falhas"].append(prefixo + "erro: " + r["erro"])
            if i < len(caso["turnos"]):
                ex["nao_rodou"] = "os turnos seguintes não rodaram"
            break
        falhas, avisos, tempo, parar = avaliar(ctx, caso, turno, r, antes, depois)
        t["falhas"], t["avisos"] = falhas, avisos
        ex["falhas"] += [prefixo + f for f in falhas]
        ex["avisos"] += [prefixo + a for a in avisos]
        ex["tempo"] += tempo
        mensagens.append({"role": "assistant", "content": r.get("texto") or ""})
        if parar and i < len(caso["turnos"]):
            ex["nao_rodou"] = "o turno %d não rodou, porque o turno %d falhou" % (i + 1, i)
            ex["falhas"].append(ex["nao_rodou"])
            break
    if ex["status"] is None:
        ex["status"] = "FALHOU" if ex["falhas"] else "PASSOU"
    return ex


def motivo_para_pular(ctx: Contexto, caso: dict, status: dict):
    req = caso.get("requer")
    if req and ctx.integracao(req) is False:
        return INTEGRACOES[req][1]
    if caso.get("altera") and not ctx.args.incluir_escrita:
        return "altera dados (use --incluir-escrita)"
    dep = caso.get("requer_caso")
    if dep:
        st, motivo = status.get(dep, (None, None))
        if st not in ("PASSOU", "FALHOU"):
            return "depende do caso %s, que não rodou%s" % (dep, (": " + motivo) if motivo else "")
    return None


# ---------------------------------------------------------------- saída na tela

def cor_status(ctx: Contexto, st: str) -> str:
    e = ctx.estilo
    return {"PASSOU": e.verde, "FALHOU": e.vermelho, "ERRO": e.vermelho, "PULADO": e.amarelo}.get(st, str)(st)


def resumo_turno(r: dict) -> str:
    ferr = curtas(r)
    return "1ª palavra %s · total %s · %s" % (jc.seg(r.get("t_primeira_palavra")), jc.seg(r.get("t_total")),
                                              ", ".join(ferr) if ferr else "nenhuma ferramenta")


def uma_linha(texto: str, limite: int = 220) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= limite else t[:limite - 1].rstrip() + "…"


def mostrar_execucao(ctx: Contexto, caso: dict, ex: dict, rotulo: str) -> None:
    e = ctx.estilo
    recuo = "        "
    varios = len(caso["turnos"]) > 1
    cabeca = "%s%s%s" % (recuo, rotulo, cor_status(ctx, ex["status"]))
    if not varios and ex["turnos"]:
        cabeca += " · " + resumo_turno(ex["turnos"][0]["r"])
    log(cabeca)
    for i, t in enumerate(ex["turnos"], 1):
        if varios:
            log("%s(%d) %s" % (recuo, i, e.fraco(resumo_turno(t["r"]))))
        if t["r"].get("texto"):
            log(recuo + e.fraco("> " + uma_linha(t["r"]["texto"])))
    for f in ex["falhas"]:
        log(recuo + e.vermelho("falha: ") + f)
    for a in ex["avisos"]:
        log(recuo + e.amarelo("aviso: ") + a)


def tempos_caso(res: dict):
    """Maior tempo até a 1ª palavra e maior tempo total entre os turnos de todas as execuções."""
    pp, tt = [], []
    for ex in res["execucoes"]:
        for t in ex["turnos"]:
            if t["r"].get("t_primeira_palavra") is not None:
                pp.append(t["r"]["t_primeira_palavra"])
            if t["r"].get("t_total") is not None:
                tt.append(t["r"]["t_total"])
    return (max(pp) if pp else None), (max(tt) if tt else None)


def ferramentas_caso(res: dict) -> str:
    nomes = []
    for ex in res["execucoes"]:
        for t in ex["turnos"]:
            nomes += curtas(t["r"])
    return ", ".join(unicos(nomes))


def imprimir_resumo(ctx: Contexto, resultados: list, repetir: int) -> None:
    e = ctx.estilo
    largura_id = max([len("Caso")] + [len(r["caso"]["id"]) for r in resultados])
    colunas = [("Caso", largura_id, "<"), ("Lista", 5, "<"), ("Situação", 8, "<")]
    if repetir > 1:
        colunas.append(("Vezes", 5, ">"))
    colunas += [("1ª palavra", 10, ">"), ("Total", 7, ">"), ("Ferramentas", 18, "<")]
    usada = sum(w for _, w, _ in colunas) + 2 * len(colunas) + 2
    # No terminal, corta o motivo na largura da tela; num arquivo (| tee), mostra inteiro.
    livre = max(30, shutil.get_terminal_size((120, 24)).columns - usada) if sys.stdout.isatty() else 400

    def celulas(valores):
        partes = []
        for (_, w, alin), v in zip(colunas, valores):
            v = str(v)
            if len(v) > w:
                v = v[:w - 1] + "…"
            partes.append(v.rjust(w) if alin == ">" else v.ljust(w))
        return partes

    log("")
    log(e.negrito("Resumo"))
    log("  " + "  ".join(celulas([c for c, _, _ in colunas])) + "  Motivo")
    for res in resultados:
        pp, tt = tempos_caso(res)
        valores = [res["caso"]["id"], rotulo_lista(res["caso"]) or "", res["status"]]
        if repetir > 1:
            valores.append(res.get("vezes") or "")
        valores += [jc.seg(pp) if pp is not None else "", jc.seg(tt) if tt is not None else "",
                    ferramentas_caso(res)]
        partes = celulas(valores)
        partes[2] = cor_status(ctx, res["status"]) + " " * (colunas[2][1] - len(res["status"]))
        log(("  " + "  ".join(partes) + "  " + uma_linha(res.get("motivo") or "", livre)).rstrip())


def respostas(resultados: list) -> list:
    return [t["r"] for res in resultados for ex in res["execucoes"] for t in ex["turnos"]]


def frase_raciocinio(resultados: list) -> str:
    todas = respostas(resultados)
    com = sum(1 for r in todas if r.get("raciocinio_chars"))
    if not com:
        return ""
    return ("O modelo raciocinou antes de responder em %d de %d respostas, o que atrasa a voz. O raciocínio deveria "
            "estar desligado: docker compose exec hermes hermes config get agent.reasoning_effort deve mostrar false."
            % (com, len(todas)))


def placar(resultados: list) -> dict:
    rodaram = [r for r in resultados if r["status"] != "PULADO"]
    p = {"aprovados": sum(1 for r in rodaram if r["status"] == "PASSOU"), "rodaram": len(rodaram),
         "pulados": len(resultados) - len(rodaram),
         "so_tempo": sum(1 for r in rodaram if r["status"] == "FALHOU" and r.get("so_tempo"))}
    grupos = {}
    for r in resultados:
        c = r["caso"]
        if c.get("lista_usuario"):
            chave = c.get("numero_usuario") if c.get("numero_usuario") is not None else c["id"]
            grupos.setdefault(chave, []).append(r)
    aprovadas = sum(1 for g in grupos.values() if all(x["status"] == "PASSOU" for x in g))
    puladas = sum(1 for g in grupos.values() if all(x["status"] == "PULADO" for x in g))
    motivos_pulo = unicos([x["motivo"] for g in grupos.values() for x in g if x["status"] == "PULADO"])
    p.update({"lista_total": len(grupos), "lista_aprovadas": aprovadas, "lista_puladas": puladas,
              "lista_motivos_pulo": motivos_pulo})
    return p


def frase_placar(p: dict) -> str:
    return "Aprovados: %d de %d (pulados: %d)" % (p["aprovados"], p["rodaram"], p["pulados"])


def frase_meta(p: dict) -> str:
    if not p["lista_total"]:
        return "Sua lista: nenhuma das %d perguntas rodou nesta seleção." % TOTAL_LISTA
    texto = "Sua lista (meta da Fase 1: %d de %d): %d de %d perguntas aprovadas" % (
        META_LISTA, TOTAL_LISTA, p["lista_aprovadas"], p["lista_total"])
    if p["lista_puladas"]:
        texto += " (puladas: %d; %s)" % (p["lista_puladas"], "; ".join(p["lista_motivos_pulo"]))
    texto += "."
    if p["lista_total"] < TOTAL_LISTA:
        texto += " Esta rodada tem só parte da lista; a meta vale para as %d." % TOTAL_LISTA
    elif p["lista_aprovadas"] >= META_LISTA:
        texto += " Meta da Fase 1 atingida!"
    else:
        falta = META_LISTA - p["lista_aprovadas"]
        texto += " Falta%s %d pergunta%s para a meta." % ("m" if falta > 1 else "", falta, "s" if falta > 1 else "")
    return texto


# ---------------------------------------------------------------- relatório

def celula(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def citar(texto: str) -> str:
    return "\n".join("> " + l if l.strip() else ">" for l in (texto or "(vazio)").splitlines())


def detalhes_turno(t: dict) -> str:
    r = t["r"]
    partes = ["ferramentas: %s" % (", ".join(r.get("ferramentas") or []) or "nenhuma"),
              "1ª palavra %s" % jc.seg(r.get("t_primeira_palavra")), "total %s" % jc.seg(r.get("t_total"))]
    uso = r.get("uso") or {}
    if uso.get("prompt_tokens") is not None:
        partes.append("tokens: %s de entrada, %s de saída" % (uso.get("prompt_tokens"), uso.get("completion_tokens", "?")))
    if r.get("raciocinio_chars"):
        partes.append("raciocínio: %d caracteres" % r["raciocinio_chars"])
    return " · ".join(partes)


def relatorio(ctx: Contexto, resultados: list, quando, p: dict, interrompido: bool, extras: list) -> str:
    a = ctx.args
    L = ["# Teste do Jarvis: %s" % quando.strftime("%d/%m/%Y %H:%M"), ""]
    L.append("Gerado por `scripts/testar-jarvis.py` com `%s`. Cada caso é uma conversa nova pela API do Hermes; "
             "os turnos de um caso formam uma conversa só. Os tempos vão do envio da pergunta até a primeira "
             "palavra da resposta e até o fim." % a.arquivo_rel)
    L.append("")
    L.append("## Resultado")
    L.append("")
    L.append("- **%s**" % frase_placar(p))
    L.append("- **%s**" % frase_meta(p))
    if p["so_tempo"]:
        L.append("- Falharam só por tempo (resposta certa, mas lenta): %d" % p["so_tempo"])
    if frase_raciocinio(resultados):
        L.append("- " + frase_raciocinio(resultados).replace("docker compose exec hermes hermes config get "
                                                             "agent.reasoning_effort", "`docker compose exec hermes "
                                                             "hermes config get agent.reasoning_effort`"))
    if interrompido:
        L.append("- Rodada interrompida com Ctrl+C: os casos que faltavam não aparecem aqui.")
    L.append("- Modelo: %s" % ctx.modelo)
    L.append("- Integrações: %s" % ctx.descrever_recursos())
    opcoes = []
    if a.caso:
        opcoes.append("--caso " + " --caso ".join(a.caso))
    if a.categoria:
        opcoes.append("--categoria " + " --categoria ".join(a.categoria))
    if a.incluir_escrita:
        opcoes.append("--incluir-escrita")
    if a.repetir > 1:
        opcoes.append("--repetir %d" % a.repetir)
    L.append("- Opções: %s" % (" ".join(opcoes) or "nenhuma (todos os casos que só leem dados)"))
    for dep, quem in extras:
        L.append("- Incluí o caso `%s` porque `%s` depende dele." % (dep, quem))
    if ctx.aquecimento:
        L.append("- Aquecimento (não conta): %s" % ctx.aquecimento)
    L.append("")
    cab = "| Caso | Lista | Situação |" + (" Vezes |" if a.repetir > 1 else "") + \
          " 1ª palavra | Total | Ferramentas | Motivo |"
    L.append(cab)
    L.append("|" + "---|" * (cab.count("|") - 1))
    for res in resultados:
        pp, tt = tempos_caso(res)
        linha = "| %s | %s | %s |" % (res["caso"]["id"], rotulo_lista(res["caso"]), res["status"])
        if a.repetir > 1:
            linha += " %s |" % (res.get("vezes") or "")
        linha += " %s | %s | %s | %s |" % (jc.seg(pp) if pp is not None else "", jc.seg(tt) if tt is not None else "",
                                           celula(ferramentas_caso(res)), celula(res.get("motivo") or ""))
        L.append(linha)
    L.append("")
    L.append("## Casos")
    L.append("")
    for res in resultados:
        c = res["caso"]
        L.append("### %s: %s" % (c["id"], res["status"]))
        L.append("")
        info = []
        if c.get("lista_usuario"):
            info.append("Pergunta %s da sua lista." % c.get("numero_usuario", "?"))
        info.append("Categoria: %s." % (c.get("categoria") or "-"))
        if c.get("requer"):
            info.append("Requer: %s." % INTEGRACOES[c["requer"]][0])
        if c.get("altera"):
            info.append("Altera dados.")
        if c.get("nota"):
            info.append(c["nota"])
        L.append(" ".join(info))
        L.append("")
        if res["status"] == "PULADO":
            L.append("Pergunta: \"%s\"" % perguntas_do_caso(c))
            L.append("")
            L.append("Pulado: %s." % res["motivo"])
            L.append("")
            continue
        n = len(res["execucoes"])
        for k, ex in enumerate(res["execucoes"], 1):
            if n > 1:
                L.append("#### Execução %d de %d: %s" % (k, n, ex["status"]))
                L.append("")
            for i, t in enumerate(ex["turnos"], 1):
                rot = ("Turno %d. " % i) if len(c["turnos"]) > 1 else ""
                L.append("**%sPergunta:** %s" % (rot, t["pergunta"]))
                L.append("")
                L.append(citar((t["r"].get("texto") or "(sem resposta em texto)")[:4000]))
                L.append("")
                L.append(detalhes_turno(t))
                L.append("")
            if ex["falhas"]:
                L.append("Falhas:")
                L.extend("- " + f for f in ex["falhas"])
                L.append("")
            if ex["avisos"]:
                L.append("Avisos:")
                L.extend("- " + x for x in ex["avisos"])
                L.append("")
    return jc.limpar("\n".join(L), ctx.chave)


def salvar_relatorio(ctx: Contexto, texto: str, quando) -> Path:
    pasta = ctx.raiz / "medicoes"
    pasta.mkdir(exist_ok=True)
    base = "teste-%s" % quando.strftime("%Y%m%d-%H%M")
    rotulo = nome_de_arquivo(ctx.args.rotulo or ctx.modelo_base)
    if rotulo:
        base += "-" + rotulo
    arquivo, n = pasta / (base + ".md"), 2
    while arquivo.exists():
        arquivo, n = pasta / ("%s-%d.md" % (base, n)), n + 1
    arquivo.write_text(texto + "\n", encoding="utf-8")
    return arquivo


# ---------------------------------------------------------------- main

def agregar(caso: dict, execucoes: list) -> dict:
    passou = sum(1 for ex in execucoes if ex["status"] == "PASSOU")
    n = len(execucoes)
    if passou == n:
        status = "PASSOU"
    elif all(ex["status"] == "ERRO" for ex in execucoes):
        status = "ERRO"
    else:
        status = "FALHOU"
    ruim = next((ex for ex in execucoes if ex["status"] != "PASSOU"), None)
    motivo = "; ".join(ruim["falhas"]) if ruim else ""
    so_tempo = bool(ruim) and all(ex["status"] == "PASSOU" or (ex["status"] == "FALHOU" and
                                  len(ex["tempo"]) == len(ex["falhas"])) for ex in execucoes)
    return {"caso": caso, "status": status, "motivo": motivo, "execucoes": execucoes,
            "vezes": "%d/%d" % (passou, n), "so_tempo": so_tempo}


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Teste prático do Jarvis: perguntas reais pela API, com conferência "
                                            "das respostas e relatório em medicoes/.")
    p.add_argument("--caso", action="append", default=[], metavar="ID", help="roda só este caso (pode repetir)")
    p.add_argument("--categoria", action="append", default=[], metavar="X",
                   help="roda só os casos desta categoria (pode repetir)")
    p.add_argument("--incluir-escrita", action="store_true",
                   help="inclui os casos que criam dados de verdade (evento na agenda, memória)")
    p.add_argument("--repetir", type=int, default=1, metavar="N",
                   help="roda cada caso N vezes e mostra a consistência (os que alteram dados rodam uma vez só)")
    p.add_argument("--arquivo", help="outro arquivo de casos (padrão: testes/jarvis-casos.json)")
    p.add_argument("--pausa", type=float, default=1.0, help="segundos entre os casos (padrão 1)")
    p.add_argument("--timeout", type=float, default=180.0, help="limite por pergunta, em segundos (padrão 180)")
    p.add_argument("--listar", action="store_true", help="mostra os casos e sai")
    p.add_argument("--rotulo", help="nome no arquivo do relatório (padrão: o modelo base, do BASE_MODEL)")
    p.add_argument("--sem-aquecer", action="store_true",
                   help="não faz a pergunta de aquecimento antes (a 1ª pergunta pode pegar o modelo frio)")
    p.add_argument("--hermes", default=jc.URL_HERMES, help=argparse.SUPPRESS)
    p.add_argument("--raiz", default=str(jc.RAIZ), help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.repetir < 1:
        p.error("--repetir precisa ser 1 ou mais")

    raiz = Path(a.raiz).resolve()
    arquivo = Path(a.arquivo).resolve() if a.arquivo else CASOS_PADRAO
    try:
        a.arquivo_rel = str(arquivo.relative_to(jc.RAIZ))
    except ValueError:
        a.arquivo_rel = str(arquivo)
    casos = carregar_casos(arquivo)
    if a.listar:
        escolhidos, _ = selecionar(casos, a.caso, a.categoria)
        listar(escolhidos)
        return 0
    escolhidos, extras = selecionar(casos, a.caso, a.categoria)

    env = jc.ler_env(raiz / ".env")
    if not env:
        log("Aviso: não encontrei %s. Rode o script de dentro da pasta do projeto." % (raiz / ".env"))
    ctx = Contexto(a, env, raiz)
    quando = jc.agora_local(ctx.fuso)

    problema = jc.verificar_hermes(ctx.url, ctx.chave)
    if problema:
        log(problema)
        return 1
    log("Teste do Jarvis: %d caso(s), perguntas de verdade pela API do Hermes (%s)." % (len(escolhidos), ctx.url))
    for dep, quem in extras:
        log("Incluí o caso %s porque %s depende dele." % (dep, quem))

    ctx.recursos, ctx.recursos_erro = jc.recursos(raiz)
    log("Modelo: " + ctx.modelo)
    log("Integrações: " + ctx.descrever_recursos())
    medicoes, ctx.conferir_erro = jc.carregar_medicoes()
    if medicoes is not None:
        ctx.conferir_hora = medicoes.conferir_hora
    elif any("hora" in verificacoes(t) for c in escolhidos for t in c["turnos"]):
        log("Aviso: não consegui carregar o scripts/medicoes.py (%s); a hora não será conferida." % ctx.conferir_erro)

    if not a.sem_aquecer:
        r = jc.conversar(ctx.url, ctx.chave, [{"role": "user", "content": "Responda só com a palavra: pronto."}],
                         timeout=ctx.timeout)
        if r.get("cancelado"):
            log("Interrompido.")
            return 1
        if r.get("erro") and not r.get("texto"):
            ctx.aquecimento = "erro: " + r["erro"]
        else:
            ctx.aquecimento = "1ª palavra %s, total %s" % (jc.seg(r.get("t_primeira_palavra")), jc.seg(r.get("t_total")))
        log("Aquecimento (não conta): " + ctx.aquecimento)
    log("")

    resultados, status, interrompido, perguntou = [], {}, False, bool(ctx.aquecimento)
    largura = len(str(len(escolhidos)))
    inicio = time.monotonic()
    try:
        for idx, caso in enumerate(escolhidos, 1):
            lista_rot = rotulo_lista(caso)
            log("[%s/%d] %s%s · %s" % (str(idx).rjust(largura), len(escolhidos), ctx.estilo.negrito(caso["id"]),
                                       (" (%s)" % lista_rot) if lista_rot else "", perguntas_do_caso(caso)))
            motivo = motivo_para_pular(ctx, caso, status)
            if motivo:
                res = {"caso": caso, "status": "PULADO", "motivo": motivo, "execucoes": [], "vezes": ""}
                resultados.append(res)
                status[caso["id"]] = ("PULADO", motivo)
                log("        %s · %s" % (cor_status(ctx, "PULADO"), motivo))
                continue
            if caso.get("pausa_antes"):
                time.sleep(caso["pausa_antes"])
            n = 1 if caso.get("altera") else a.repetir
            execucoes = []
            for k in range(n):
                if perguntou and a.pausa > 0:
                    time.sleep(a.pausa)
                perguntou = True
                ex = rodar_caso(ctx, caso)
                execucoes.append(ex)
                mostrar_execucao(ctx, caso, ex, ("[%d/%d] " % (k + 1, n)) if n > 1 else "")
            if caso.get("altera") and a.repetir > 1:
                log(ctx.estilo.fraco("        (rodou uma vez só: altera dados)"))
            if caso.get("altera") and caso.get("nota"):
                log("        " + ctx.estilo.amarelo("atenção: ") + caso["nota"])
            res = agregar(caso, execucoes)
            resultados.append(res)
            status[caso["id"]] = (res["status"], res["motivo"])
    except KeyboardInterrupt:
        interrompido = True
        log("\nInterrompido. Gerando o relatório com o que rodou até aqui.")

    pl = placar(resultados)
    if resultados:
        imprimir_resumo(ctx, resultados, a.repetir)
    log("")
    log(ctx.estilo.negrito(frase_placar(pl)))
    if pl["so_tempo"]:
        log("Falharam só por tempo (resposta certa, mas lenta): %d" % pl["so_tempo"])
    log(ctx.estilo.negrito(frase_meta(pl)))
    if frase_raciocinio(resultados):
        frase = frase_raciocinio(resultados)
        log(ctx.estilo.amarelo("Aviso: ") + frase[0].lower() + frase[1:])
    texto = relatorio(ctx, resultados, quando, pl, interrompido, extras)
    arquivo_md = salvar_relatorio(ctx, texto, quando)
    try:
        mostrado = arquivo_md.relative_to(raiz)
    except ValueError:
        mostrado = arquivo_md
    log("")
    log("Concluído em %s. Relatório com as respostas completas: %s" % (jc.seg(time.monotonic() - inicio), mostrado))
    if interrompido:
        return 1
    return 0 if all(r["status"] in ("PASSOU", "PULADO") for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())

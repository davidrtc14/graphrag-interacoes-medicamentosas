"""Compara LLM puro, RAG vetorial e GraphRAG com perguntas de gabarito fixo."""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from .config import RAIZ
from .llm import ErroLLM
from .resposta import responder

ARQ_PERGUNTAS = RAIZ / "eval" / "perguntas.json"
DIR_RESULTADOS = RAIZ / "eval" / "resultados"
MODOS = ("llm_puro", "rag_vetorial", "graphrag")


def carregar_perguntas(arquivo: Path = ARQ_PERGUNTAS) -> list[dict]:
    return json.loads(arquivo.read_text(encoding="utf-8"))


def pontuar(pergunta: dict, texto: str, abstencao: bool) -> dict:
    """Pontuação automática: uma primeira leitura, conferida depois pelo grupo."""
    if pergunta["tipo"] == "sem_resposta":
        return {"acerto": None, "abstencao_correta": abstencao}
    acerto = not abstencao and all(re.search(p, texto, re.I) for p in pergunta["deve_conter"])
    return {"acerto": acerto, "abstencao_correta": None}


def executar(perguntas, llm, driver, embutir, arquivo: Path, modos=MODOS) -> list[dict]:
    """Salva cada resposta na hora. Se a cota acabar, a próxima execução continua de onde parou."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    feitas = {}
    if arquivo.exists():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            registro = json.loads(linha)
            feitas[(registro["id"], registro["modo"])] = registro
    with arquivo.open("a", encoding="utf-8") as saida:
        for pergunta in perguntas:
            for modo in modos:
                if (pergunta["id"], modo) in feitas:
                    continue
                try:
                    r = responder(pergunta["pergunta"], llm, driver, embutir, modo=modo)
                except ErroLLM as erro:
                    print(f"Parado em {pergunta['id']}/{modo}: {erro}")
                    return list(feitas.values())
                registro = {
                    "id": pergunta["id"], "tipo": pergunta["tipo"], "modo": modo, "pergunta": pergunta["pergunta"],
                    "resposta": r.texto, "abstencao": r.abstencao, "citacoes": r.citacoes, "confianca": r.confianca,
                    "evidencias_citadas": {c: r.evidencias[c] for c in r.citacoes}, "modelo": r.modelo,
                    "latencia_s": round(r.latencia_s, 2), **pontuar(pergunta, r.texto, r.abstencao),
                }
                saida.write(json.dumps(registro, ensure_ascii=False) + "\n")
                saida.flush()
                feitas[(pergunta["id"], modo)] = registro
    return list(feitas.values())


def _taxa(valores: list) -> float | None:
    validos = [v for v in valores if v is not None]
    return round(sum(map(bool, validos)) / len(validos), 3) if validos else None


def resumir(registros: list[dict]) -> dict:
    """Métricas por modo e tipo. Só resume o que foi executado, sem preencher lacunas."""
    grupos = defaultdict(list)
    for r in registros:
        grupos[r["modo"]].append(r)
    resumo = {}
    for modo, itens in grupos.items():
        latencias = sorted(i["latencia_s"] for i in itens)
        com_resposta = [i for i in itens if not i["abstencao"]]
        resumo[modo] = {
            "execucoes": len(itens),
            "acerto_direta": _taxa([i["acerto"] for i in itens if i["tipo"] == "direta"]),
            "acerto_multi_hop": _taxa([i["acerto"] for i in itens if i["tipo"] == "multi_hop"]),
            "abstencao_correta_sem_resposta": _taxa([i["abstencao_correta"] for i in itens if i["tipo"] == "sem_resposta"]),
            "abstencao_indevida": _taxa([i["abstencao"] for i in itens if i["tipo"] != "sem_resposta"]),
            "respostas_com_citacao": _taxa([bool(i["citacoes"]) for i in com_resposta]),
            "latencia_mediana_s": latencias[len(latencias) // 2] if latencias else None,
        }
    return resumo


def gerar_revisao(registros: list[dict], arquivo: Path) -> None:
    """Planilha para o grupo conferir as notas e marcar se a resposta é sustentada pela fonte."""
    with arquivo.open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(["id", "tipo", "modo", "pergunta", "resposta", "citacoes", "acerto_automatico",
                           "abstencao_correta", "acerto_revisado", "sustentada_pela_fonte", "revisor", "observacao"])
        for r in sorted(registros, key=lambda x: (x["id"], x["modo"])):
            escritor.writerow([r["id"], r["tipo"], r["modo"], r["pergunta"], r["resposta"], " ".join(r["citacoes"]),
                               r["acerto"], r["abstencao_correta"], "", "", "", ""])

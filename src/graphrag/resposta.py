"""Geração da resposta restrita à evidência, com citações validadas por código."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from neo4j import Driver

from .cypher_seguro import CypherRecusado, executar_leitura, validar_cypher
from .llm import LLM
from .recuperacao import Contexto, buscar_trechos, recuperar

Modo = Literal["graphrag", "rag_vetorial", "llm_puro"]

SIMILARIDADE_MINIMA = 0.45
AVISO = "Projeto acadêmico. Não substitui a orientação de um profissional de saúde."
MENSAGEM_ABSTENCAO = "Não encontrei evidência suficiente nas bulas carregadas para responder a essa pergunta."

_REGEX_CITACAO = re.compile(r"\[\s*([CTQ]\d+(?:\s*,\s*[CTQ]\d+)*)\s*\]")
_PEDE_LISTA = re.compile(r"\b(quantos|quantas|liste|listar|ranking|mais citad|quais classes)\b", re.I)

SISTEMA_RESPOSTA = """Você responde perguntas sobre interações medicamentosas em português do Brasil.
Use somente a evidência fornecida. Nunca complete com conhecimento próprio.

Regras:
- Cite a evidência logo após cada afirmação, com os identificadores exatos, como [C1], [T2] ou [Q1].
- Separe o que a bula declara do que foi inferido. Caminhos marcados como INFERIDO indicam só um possível
  mecanismo; diga isso de forma explícita e nunca os apresente como interação confirmada.
- Se a evidência não responder à pergunta, marque "abstencao" como true e não tente responder.
- Não recomende, ajuste ou suspenda tratamentos.

Responda somente em JSON: {"abstencao": false, "resposta": "texto com citações"}"""

SISTEMA_LLM_PURO = """Você responde perguntas sobre interações medicamentosas em português do Brasil.
Se não souber a resposta, diga isso marcando "abstencao" como true.
Responda somente em JSON: {"abstencao": false, "resposta": "texto"}"""

SISTEMA_CYPHER = """Você escreve uma única consulta Cypher de leitura para Neo4j.

Schema:
(:Medicamento {id, nome_canonico, nome_pt, em_recorte, pagerank, comunidade})
(:Enzima {id, nome_canonico}), (:Efeito {id, nome_canonico}), (:Condicao {id, nome_canonico})
(:ClasseTerapeutica {id, nome_canonico})
(:Medicamento)-[:INIBE|INDUZ|E_METABOLIZADO_POR]->(:Enzima)
(:Medicamento)-[:INTERAGE_COM]-(:Medicamento)
(:Medicamento)-[:PODE_CAUSAR]->(:Efeito)
(:Medicamento)-[:CONTRAINDICADO_EM]->(:Condicao)
(:Medicamento)-[:PERTENCE_A]->(:ClasseTerapeutica)

Nomes canônicos de medicamentos estão em inglês e minúsculas (clarithromycin). Enzimas: CYP3A4, P-gp.
Classes terapêuticas estão em português (Estatina). Use somente esse schema.
Retorne valores simples com aliases claros e inclua LIMIT.
Responda somente em JSON: {"cypher": "..."}"""


@dataclass
class Resposta:
    pergunta: str
    modo: str
    texto: str
    abstencao: bool
    citacoes: list[str] = field(default_factory=list)
    confianca: float = 0.0
    nivel: str = "nenhum"
    contexto: Contexto | None = None
    evidencias: dict[str, str] = field(default_factory=dict)
    modelo: str = ""
    latencia_s: float = 0.0
    motivo_abstencao: str = ""


def extrair_citacoes(texto: str) -> list[str]:
    """Aceita [C1] e [C1, T2], para que nenhuma citação escape da validação."""
    return list(dict.fromkeys(c.strip() for grupo in _REGEX_CITACAO.findall(texto) for c in grupo.split(",")))


def montar_evidencias(contexto: Contexto) -> dict[str, str]:
    """Numera a evidência. Os identificadores são os únicos que o LLM pode citar."""
    evidencias: dict[str, str] = {}
    for i, caminho in enumerate(contexto.caminhos, 1):
        tipo = "DECLARADO NA BULA" if caminho.declarado else "INFERIDO PELO GRAFO"
        evidencias[f"C{i}"] = f"{tipo}: {caminho.descrever()}"
    for i, trecho in enumerate(contexto.trechos, 1):
        evidencias[f"T{i}"] = f"Trecho {trecho['id']}: {trecho['texto']}"
    for i, linha in enumerate(contexto.linhas_geradas, 1):
        evidencias[f"Q{i}"] = f"Resultado da consulta gerada: {json.dumps(linha, ensure_ascii=False, default=str)}"
    return evidencias


def calcular_confianca(contexto: Contexto, citacoes: list[str]) -> tuple[float, str]:
    """Regra baseada na evidência citada, não na opinião do LLM. Não é probabilidade calibrada."""
    caminhos = [contexto.caminhos[int(c[1:]) - 1] for c in citacoes if c[0] == "C"]
    trechos = [contexto.trechos[int(c[1:]) - 1] for c in citacoes if c[0] == "T"]
    if not caminhos and not trechos and not any(c[0] == "Q" for c in citacoes):
        return 0.0, "nenhum"

    extracao = max((c.confianca for c in caminhos), default=0.5)
    quantidade = min(1.0, (len(caminhos) + len(trechos)) / 3)
    similaridades = [t["score"] for t in trechos if t.get("score") is not None]
    similaridade = max(similaridades, default=0.5)
    valor = 0.5 * extracao + 0.3 * quantidade + 0.2 * similaridade
    if caminhos and not any(c.declarado for c in caminhos) and not trechos:
        valor = min(valor, 0.6)  # Só inferência não pode ter confiança alta.
    valor = round(valor, 2)
    return valor, "alto" if valor >= 0.75 else "médio" if valor >= 0.5 else "baixo"


def _abster(contexto: Contexto | None, pergunta: str, modo: str, motivo: str, inicio: float) -> Resposta:
    return Resposta(
        pergunta=pergunta, modo=modo, texto=MENSAGEM_ABSTENCAO, abstencao=True, contexto=contexto,
        evidencias=montar_evidencias(contexto) if contexto else {}, latencia_s=time.perf_counter() - inicio,
        motivo_abstencao=motivo,
    )


def _sem_evidencia(contexto: Contexto) -> bool:
    if contexto.caminhos or contexto.linhas_geradas:
        return False
    return max((t.get("score") or 0.0 for t in contexto.trechos), default=0.0) < SIMILARIDADE_MINIMA


def gerar_cypher(driver: Driver, pergunta: str, llm: LLM, contexto: Contexto) -> None:
    """Consulta gerada pelo LLM. Se for recusada ou falhar, seguem as consultas prontas."""
    entidades = ", ".join(e["nome"] for e in contexto.entidades) or "nenhuma"
    try:
        bruto = llm.gerar(f"Pergunta: {pergunta}\nEntidades reconhecidas: {entidades}", SISTEMA_CYPHER, json_mode=True)
        cypher = validar_cypher(str(bruto.json().get("cypher", "")))
        contexto.cypher_gerado = cypher
        contexto.linhas_geradas = executar_leitura(driver, cypher)
        contexto.consultas.append(
            {"nome": "gerada_pelo_llm", "cypher": cypher, "parametros": {}, "linhas": len(contexto.linhas_geradas)}
        )
    except CypherRecusado as erro:
        contexto.consultas.append({"nome": "gerada_pelo_llm", "cypher": "", "parametros": {}, "linhas": 0, "erro": str(erro)})
    except Exception as erro:
        contexto.consultas.append(
            {"nome": "gerada_pelo_llm", "cypher": contexto.cypher_gerado, "parametros": {}, "linhas": 0,
             "erro": f"{type(erro).__name__}: {str(erro)[:200]}"}
        )


def responder(
    pergunta: str,
    llm: LLM,
    driver: Driver | None = None,
    embutir: Callable[[list[str]], list[list[float]]] | None = None,
    modo: Modo = "graphrag",
) -> Resposta:
    """Responde em um dos três modos comparados na avaliação."""
    inicio = time.perf_counter()

    if modo == "llm_puro":
        bruto = llm.gerar(f"Pergunta: {pergunta}", SISTEMA_LLM_PURO, json_mode=True)
        dados = bruto.json()
        return Resposta(pergunta, modo, str(dados.get("resposta", "")), bool(dados.get("abstencao")),
                        modelo=bruto.modelo, latencia_s=time.perf_counter() - inicio)

    if modo == "rag_vetorial":
        contexto = Contexto(pergunta=pergunta, trechos=buscar_trechos(driver, pergunta, embutir))
    else:
        contexto = recuperar(driver, pergunta, embutir)
        if not contexto.caminhos or _PEDE_LISTA.search(pergunta):
            gerar_cypher(driver, pergunta, llm, contexto)

    if _sem_evidencia(contexto):
        return _abster(contexto, pergunta, modo, "sem evidência recuperada", inicio)

    evidencias = montar_evidencias(contexto)
    bloco = "\n".join(f"[{k}] {v}" for k, v in evidencias.items())
    prompt = f"Evidência:\n{bloco}\n\nPergunta: {pergunta}"
    modelo = ""
    for tentativa in range(2):
        bruto = llm.gerar(prompt, SISTEMA_RESPOSTA, json_mode=True)
        modelo = bruto.modelo
        dados = bruto.json()
        if dados.get("abstencao"):
            return _abster(contexto, pergunta, modo, "o LLM considerou a evidência insuficiente", inicio)
        texto = str(dados.get("resposta", "")).strip()
        citacoes = extrair_citacoes(texto)
        invalidas = [c for c in citacoes if c not in evidencias]
        if texto and citacoes and not invalidas:
            valor, nivel = calcular_confianca(contexto, citacoes)
            return Resposta(pergunta, modo, texto, False, citacoes, valor, nivel, contexto, evidencias, modelo,
                            time.perf_counter() - inicio)
        prompt += (
            "\n\nSua resposta anterior foi recusada: toda afirmação precisa citar somente os "
            f"identificadores fornecidos. Inválidos: {invalidas or 'nenhuma citação'}."
        )
    return _abster(contexto, pergunta, modo, "citações inválidas após nova tentativa", inicio)

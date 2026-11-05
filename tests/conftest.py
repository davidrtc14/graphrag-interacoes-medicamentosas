"""Grafo de exemplo e LLM falso: os testes não gastam cota nem tocam nos dados reais."""
from __future__ import annotations

import json
import os

import pytest

from graphrag.config import carregar_config
from graphrag.llm import RespostaLLM
from graphrag.medicamentos import Medicamento, Resolvedor

URI_TESTE = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7688")

MEDICAMENTOS = [
    Medicamento("clarithromycin", "claritromicina", "Antibiótico macrolídeo"),
    Medicamento("simvastatin", "sinvastatina", "Estatina"),
    Medicamento("pravastatin", "pravastatina", "Estatina"),
    Medicamento("rifampin", "rifampicina", "Antibiótico rifamicina"),
]

BULAS = [
    {"generico": m.generico, "set_id": f"set-{m.generico}", "versao": "20260101", "marcas": ["Biaxin"] if m.generico == "clarithromycin" else [],
     "url": f"https://exemplo.test/{m.generico}"}
    for m in MEDICAMENTOS
]


def _relacao(trecho, origem, tipo_o, tipo, destino, tipo_d, evidencia, efeito="", confianca=0.9):
    return {"trecho_id": trecho, "origem": origem, "tipo_origem": tipo_o, "tipo": tipo, "destino": destino,
            "tipo_destino": tipo_d, "evidencia": evidencia, "efeito": efeito, "confianca": confianca, "modelo": "falso"}


EXTRACOES = [
    {"generico": "clarithromycin", "modelos": ["falso"],
     "trechos": [{"id": "clarithromycin:drug_interactions:0", "medicamento": "clarithromycin", "secao": "drug_interactions",
                  "texto": "Clarithromycin is a strong inhibitor of CYP3A4."}],
     "relacoes": [_relacao("clarithromycin:drug_interactions:0", "clarithromycin", "Medicamento", "INIBE", "CYP3A4", "Enzima",
                           "strong inhibitor of CYP3A4")],
     "rejeitadas": []},
    {"generico": "simvastatin", "modelos": ["falso"],
     "trechos": [{"id": "simvastatin:clinical_pharmacology:0", "medicamento": "simvastatin", "secao": "clinical_pharmacology",
                  "texto": "Simvastatin is metabolized by CYP 3A4. Myopathy has been reported."}],
     "relacoes": [
         _relacao("simvastatin:clinical_pharmacology:0", "Simvastatin", "Medicamento", "E_METABOLIZADO_POR", "CYP 3A4", "Enzima", "metabolized by CYP 3A4"),
         _relacao("simvastatin:clinical_pharmacology:0", "simvastatin", "Medicamento", "PODE_CAUSAR", "Myopathy", "Efeito", "Myopathy has been reported"),
     ],
     "rejeitadas": []},
    {"generico": "rifampin", "modelos": ["falso"],
     "trechos": [{"id": "rifampin:drug_interactions:0", "medicamento": "rifampin", "secao": "drug_interactions",
                  "texto": "Rifampicin induces CYP3A4 and interacts with simvastatin hydrochloride."}],
     "relacoes": [
         _relacao("rifampin:drug_interactions:0", "rifampicin", "Medicamento", "INDUZ", "cyp3a4", "Enzima", "induces CYP3A4"),
         _relacao("rifampin:drug_interactions:0", "rifampin", "Medicamento", "INTERAGE_COM", "simvastatin hydrochloride", "Medicamento",
                  "interacts with simvastatin", efeito="lower simvastatin exposure"),
     ],
     "rejeitadas": []},
]


def embutir_falso(textos):
    """Vetor fixo por palavras-chave, suficiente para testar o caminho da busca vetorial."""
    vetores = []
    for texto in textos:
        minusculo = texto.lower()
        v = [1.0 if "clarit" in minusculo else 0.0, 1.0 if ("simva" in minusculo or "sinva" in minusculo) else 0.0,
             1.0 if "rifamp" in minusculo else 0.0]
        vetores.append((v + [0.01] * 381)[:384])
    return vetores


class LLMFalso:
    """Devolve respostas prontas, em ordem, e guarda o que recebeu."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def gerar(self, prompt, sistema="", json_mode=False):
        self.chamadas.append({"prompt": prompt, "sistema": sistema})
        resposta = self.respostas.pop(0)
        return RespostaLLM(resposta if isinstance(resposta, str) else json.dumps(resposta), "falso")


@pytest.fixture
def llm_falso():
    return LLMFalso


@pytest.fixture(scope="session")
def driver_teste():
    """Só usa o Neo4j de teste, isolado da base real, e o apaga a cada sessão."""
    from neo4j import GraphDatabase

    from graphrag.grafo import calcular_algoritmos, carregar_grafo, criar_schema

    config = carregar_config()
    driver = GraphDatabase.driver(URI_TESTE, auth=(config.neo4j_user, config.neo4j_password))
    try:
        driver.verify_connectivity()
    except Exception:
        pytest.skip("Neo4j de teste desligado. Use: docker compose --profile test up -d --wait")
    if URI_TESTE == config.neo4j_uri:
        pytest.fail("O Neo4j de teste não pode ser o mesmo da base real.")
    driver.execute_query("MATCH (n) DETACH DELETE n")
    criar_schema(driver)
    resolvedor = Resolvedor(MEDICAMENTOS, {"Biaxin": "clarithromycin"})
    carregar_grafo(driver, MEDICAMENTOS, BULAS, EXTRACOES, resolvedor, embutir_falso)
    calcular_algoritmos(driver)
    yield driver
    driver.close()

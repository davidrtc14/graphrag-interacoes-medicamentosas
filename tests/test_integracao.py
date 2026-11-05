"""Testes contra um Neo4j real, com grafo de exemplo e LLM falso."""
import pytest
from neo4j.exceptions import ClientError

from graphrag.cypher_seguro import executar_leitura
from graphrag.grafo import estatisticas, exportar_grafo, importar_grafo
from graphrag.recuperacao import ligar_entidades, recuperar
from graphrag.resposta import MENSAGEM_ABSTENCAO, responder

from .conftest import embutir_falso

pytestmark = pytest.mark.integracao


def _nomes(contexto):
    return [c.descrever() for c in contexto.caminhos]


def test_resolucao_de_entidades_evita_duplicatas(driver_teste):
    nomes = [r["nome"] for r in executar_leitura(driver_teste, "MATCH (n:Medicamento) RETURN n.nome_canonico AS nome")]
    assert sorted(nomes) == ["clarithromycin", "pravastatin", "rifampin", "simvastatin"]
    enzimas = [r["nome"] for r in executar_leitura(driver_teste, "MATCH (n:Enzima) RETURN n.nome_canonico AS nome")]
    assert enzimas == ["CYP3A4"]


def test_schema_tem_constraints_e_indices(driver_teste):
    tipos = {r["type"] for r in executar_leitura(driver_teste, "SHOW INDEXES YIELD type RETURN type")}
    assert {"RANGE", "FULLTEXT", "VECTOR"} <= tipos
    constraints = executar_leitura(driver_teste, "SHOW CONSTRAINTS YIELD name RETURN name")
    assert {"medicamento_id", "medicamento_nome", "enzima_nome"} <= {c["name"] for c in constraints}


def test_ligacao_aceita_portugues_marca_e_enzima(driver_teste):
    entidades = ligar_entidades(driver_teste, "O Biaxin altera a sinvastatina pela CYP 3A4?")
    assert {e["nome"] for e in entidades} == {"clarithromycin", "simvastatin", "CYP3A4"}


def test_multi_hop_encontra_mecanismo_entre_bulas_diferentes(driver_teste):
    contexto = recuperar(driver_teste, "A claritromicina afeta a sinvastatina?", embutir_falso)
    inferidos = [c for c in contexto.caminhos if not c.declarado]
    assert any("clarithromycin -[INIBE]-> CYP3A4" in c.descrever() and "simvastatin -[E_METABOLIZADO_POR]-> CYP3A4" in c.descrever() for c in inferidos)
    assert {"clarithromycin:drug_interactions:0", "simvastatin:clinical_pharmacology:0"} <= {t["id"] for t in contexto.trechos}


def test_tres_saltos_chegam_ao_efeito(driver_teste):
    contexto = recuperar(driver_teste, "O que a claritromicina pode causar por interação?", embutir_falso)
    assert any(len(c.rels) == 3 and "myopathy" in c.descrever() for c in contexto.caminhos)


def test_algoritmos_gravam_propriedades(driver_teste):
    linhas = executar_leitura(driver_teste, "MATCH (n:Enzima) RETURN n.pagerank AS pr, n.comunidade AS com")
    assert linhas[0]["pr"] > 0 and linhas[0]["com"] is not None


def test_transacao_de_leitura_bloqueia_escrita(driver_teste):
    with pytest.raises(ClientError):
        executar_leitura(driver_teste, "CREATE (n:Invasor) RETURN n")


def test_resposta_valida_citacoes_e_marca_confianca(driver_teste, llm_falso):
    llm = llm_falso([{"abstencao": False, "resposta": "Possível mecanismo pela CYP3A4 [C1]."}])
    r = responder("A claritromicina afeta a sinvastatina?", llm, driver_teste, embutir_falso)
    assert not r.abstencao and r.citacoes == ["C1"] and r.nivel != "nenhum"
    assert "INFERIDO" in llm.chamadas[0]["prompt"]


def test_citacao_inventada_leva_a_nova_tentativa_e_abstencao(driver_teste, llm_falso):
    llm = llm_falso([{"abstencao": False, "resposta": "Certo [C99]."}, {"abstencao": False, "resposta": "Sem citação."}])
    r = responder("A claritromicina afeta a sinvastatina?", llm, driver_teste, embutir_falso)
    assert r.abstencao and r.texto == MENSAGEM_ABSTENCAO and len(llm.chamadas) == 2


def test_cypher_malicioso_do_llm_nao_executa(driver_teste, llm_falso):
    llm = llm_falso([{"cypher": "MATCH (n) DETACH DELETE n"}, {"abstencao": False, "resposta": "Evidência [C1]."}])
    r = responder("Quantos medicamentos interagem com a sinvastatina?", llm, driver_teste, embutir_falso)
    assert any("DETACH" in c.get("erro", "") for c in r.contexto.consultas)
    assert estatisticas(driver_teste)["nos"]["Medicamento"] == 4


def test_cypher_valido_do_llm_vira_evidencia_citavel(driver_teste, llm_falso):
    llm = llm_falso([
        {"cypher": "MATCH (m:Medicamento)-[:PERTENCE_A]->(c:ClasseTerapeutica {nome_canonico: 'Estatina'}) RETURN count(m) AS total"},
        {"abstencao": False, "resposta": "São 2 estatinas [Q1]."},
    ])
    r = responder("Quantos medicamentos são estatinas?", llm, driver_teste, embutir_falso)
    assert r.citacoes == ["Q1"] and r.contexto.linhas_geradas == [{"total": 2}]


def test_abstencao_sem_gastar_cota_na_resposta_quando_nada_e_recuperado(driver_teste, llm_falso, monkeypatch):
    monkeypatch.setattr("graphrag.resposta.SIMILARIDADE_MINIMA", 1.1)
    llm = llm_falso([{"cypher": "MATCH (m:Medicamento {nome_canonico: 'inexistente'}) RETURN m.id AS id"}])
    r = responder("Vitamina C cura gripe?", llm, driver_teste, embutir_falso)
    assert r.abstencao and len(llm.chamadas) == 1


def test_rag_vetorial_nao_usa_caminhos(driver_teste, llm_falso):
    llm = llm_falso([{"abstencao": False, "resposta": "Inibe a enzima [T1]."}])
    r = responder("claritromicina", llm, driver_teste, embutir_falso, modo="rag_vetorial")
    assert not r.contexto.caminhos and r.citacoes == ["T1"]


def test_llm_puro_nao_usa_grafo(llm_falso):
    llm = llm_falso([{"abstencao": False, "resposta": "Texto."}])
    r = responder("Pergunta?", llm, modo="llm_puro")
    assert r.texto == "Texto." and r.contexto is None


def test_exportar_e_importar_preservam_o_grafo(driver_teste, tmp_path):
    antes = estatisticas(driver_teste)
    arquivo = tmp_path / "grafo.json.gz"
    exportado = exportar_grafo(driver_teste, arquivo)
    driver_teste.execute_query("MATCH (n) DETACH DELETE n")
    importado = importar_grafo(driver_teste, arquivo)
    assert importado == exportado
    assert estatisticas(driver_teste) == antes
    assert recuperar(driver_teste, "A claritromicina afeta a sinvastatina?", embutir_falso).caminhos

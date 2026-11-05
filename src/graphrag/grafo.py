"""Schema LPG, carga, algoritmos de grafo e exportação no Neo4j."""
from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Callable

from neo4j import Driver, GraphDatabase

from .config import ARQ_GRAFO, DIMENSOES_EMBEDDING, Config
from .medicamentos import Medicamento, Resolvedor, entidade_invalida, nome_efeito, nome_enzima, normalizar

ROTULOS = ("Medicamento", "Enzima", "Efeito", "Condicao", "ClasseTerapeutica", "Bula", "Trecho")
RELACOES_CLINICAS = ("INIBE", "INDUZ", "E_METABOLIZADO_POR", "INTERAGE_COM", "PODE_CAUSAR", "CONTRAINDICADO_EM")

# No Neo4j 5, o índice RANGE substitui o antigo BTree citado no enunciado.
SCHEMA = [
    *[f"CREATE CONSTRAINT {r.lower()}_id IF NOT EXISTS FOR (n:{r}) REQUIRE n.id IS UNIQUE" for r in ROTULOS],
    "CREATE CONSTRAINT medicamento_nome IF NOT EXISTS FOR (n:Medicamento) REQUIRE n.nome_canonico IS UNIQUE",
    "CREATE CONSTRAINT enzima_nome IF NOT EXISTS FOR (n:Enzima) REQUIRE n.nome_canonico IS UNIQUE",
    "CREATE RANGE INDEX medicamento_pagerank IF NOT EXISTS FOR (n:Medicamento) ON (n.pagerank)",
    "CREATE RANGE INDEX trecho_secao IF NOT EXISTS FOR (n:Trecho) ON (n.secao)",
    "CREATE RANGE INDEX bula_versao IF NOT EXISTS FOR (n:Bula) ON (n.versao)",
    "CREATE FULLTEXT INDEX entidade_nome IF NOT EXISTS "
    "FOR (n:Medicamento|Enzima|Efeito|Condicao) ON EACH [n.nome_canonico, n.nome_pt, n.sinonimos]",
    "CREATE FULLTEXT INDEX trecho_texto IF NOT EXISTS FOR (n:Trecho) ON EACH [n.texto]",
    "CREATE VECTOR INDEX trecho_embedding IF NOT EXISTS FOR (n:Trecho) ON (n.embedding) "
    f"OPTIONS {{indexConfig: {{`vector.dimensions`: {DIMENSOES_EMBEDDING}, `vector.similarity_function`: 'cosine'}}}}",
]


def conectar(config: Config) -> Driver:
    driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))
    driver.verify_connectivity()
    return driver


def criar_schema(driver: Driver) -> None:
    for comando in SCHEMA:
        driver.execute_query(comando)
    driver.execute_query("CALL db.awaitIndexes(120)")


def _id_entidade(rotulo: str, nome: str) -> str:
    return f"{rotulo.lower()}:{normalizar(nome)}"


def _canonico(rotulo: str, nome: str, resolvedor: Resolvedor) -> str:
    if rotulo == "Medicamento":
        return resolvedor.medicamento(nome)
    if rotulo == "Enzima":
        return nome_enzima(nome) or nome.strip()
    return nome_efeito(nome)


def consolidar(extracoes: list[dict], resolvedor: Resolvedor) -> dict:
    """Aplica a resolução de entidades e junta relações iguais, somando as evidências."""
    entidades: dict[str, dict] = {}
    relacoes: dict[tuple[str, str, str], dict] = {}
    mencoes: set[tuple[str, str]] = set()
    descartadas = 0

    def entidade(rotulo: str, nome: str) -> dict:
        canonico = _canonico(rotulo, nome, resolvedor)
        ident = _id_entidade(rotulo, canonico)
        return entidades.setdefault(ident, {"id": ident, "rotulo": rotulo, "nome_canonico": canonico})

    for extracao in extracoes:
        for r in extracao["relacoes"]:
            if entidade_invalida(r["tipo_origem"], r["origem"], resolvedor) or entidade_invalida(r["tipo_destino"], r["destino"], resolvedor):
                descartadas += 1
                continue
            origem, destino = entidade(r["tipo_origem"], r["origem"]), entidade(r["tipo_destino"], r["destino"])
            if origem["id"] == destino["id"]:
                descartadas += 1
                continue
            chave = (origem["id"], r["tipo"], destino["id"])
            if r["tipo"] == "INTERAGE_COM":
                # Interação não tem direção clínica: A com B e B com A viram uma só relação.
                chave = (min(origem["id"], destino["id"]), r["tipo"], max(origem["id"], destino["id"]))
            atual = relacoes.setdefault(
                chave,
                {"origem": chave[0], "tipo": chave[1], "destino": chave[2], "trecho_ids": [], "evidencias": [],
                 "efeitos": [], "modelos": [], "confianca_extracao": 0.0},
            )
            if r["trecho_id"] not in atual["trecho_ids"]:
                atual["trecho_ids"].append(r["trecho_id"])
                atual["evidencias"].append(r["evidencia"])
            if r.get("efeito") and r["efeito"] not in atual["efeitos"]:
                atual["efeitos"].append(r["efeito"])
            if r["modelo"] not in atual["modelos"]:
                atual["modelos"].append(r["modelo"])
            atual["confianca_extracao"] = max(atual["confianca_extracao"], r["confianca"])
            mencoes.update({(r["trecho_id"], origem["id"]), (r["trecho_id"], destino["id"])})

    return {
        "entidades": list(entidades.values()),
        "relacoes": list(relacoes.values()),
        "mencoes": sorted(mencoes),
        "unificacoes": dict(resolvedor.unificacoes),
        "descartadas": descartadas,
    }


def carregar_grafo(
    driver: Driver,
    medicamentos: list[Medicamento],
    bulas: list[dict],
    extracoes: list[dict],
    resolvedor: Resolvedor,
    embutir: Callable[[list[str]], list[list[float]]],
) -> dict:
    """Carrega tudo com MERGE, então repetir a carga não duplica dados."""
    consolidado = consolidar(extracoes, resolvedor)
    em_lote = driver.execute_query

    marcas_por_generico: dict[str, list[str]] = {b["generico"]: b.get("marcas", []) for b in bulas}
    em_lote(
        """UNWIND $linhas AS l
        MERGE (m:Medicamento {id: l.id})
        SET m.nome_canonico = l.generico, m.nome_pt = l.nome_pt, m.sinonimos = l.sinonimos, m.em_recorte = true
        MERGE (c:ClasseTerapeutica {id: l.classe_id}) SET c.nome_canonico = l.classe
        MERGE (m)-[:PERTENCE_A]->(c)""",
        linhas=[
            {"id": _id_entidade("Medicamento", m.generico), "generico": m.generico, "nome_pt": m.nome_pt,
             "sinonimos": marcas_por_generico.get(m.generico, []),
             "classe": m.classe, "classe_id": f"classe:{normalizar(m.classe)}"}
            for m in medicamentos
        ],
    )

    em_lote(
        """UNWIND $linhas AS l
        MATCH (m:Medicamento {id: l.medicamento})
        MERGE (b:Bula {id: l.id}) SET b.set_id = l.set_id, b.versao = l.versao, b.url = l.url
        MERGE (m)-[:TEM_BULA]->(b)""",
        linhas=[
            {"id": f"bula:{b['generico']}", "medicamento": _id_entidade("Medicamento", b["generico"]),
             "set_id": b["set_id"], "versao": b["versao"], "url": b["url"]}
            for b in bulas
        ],
    )

    trechos = [t for e in extracoes for t in e["trechos"]]
    vetores = embutir([t["texto"] for t in trechos]) if trechos else []
    em_lote(
        """UNWIND $linhas AS l
        MATCH (b:Bula {id: l.bula})
        MERGE (t:Trecho {id: l.id}) SET t.texto = l.texto, t.secao = l.secao, t.embedding = l.embedding
        MERGE (b)-[:TEM_TRECHO]->(t)""",
        linhas=[
            {"id": t["id"], "bula": f"bula:{t['medicamento']}", "texto": t["texto"], "secao": t["secao"], "embedding": v}
            for t, v in zip(trechos, vetores)
        ],
    )

    por_rotulo: dict[str, list[dict]] = defaultdict(list)
    for e in consolidado["entidades"]:
        por_rotulo[e["rotulo"]].append(e)
    for rotulo, linhas in por_rotulo.items():
        em_lote(
            f"""UNWIND $linhas AS l
            MERGE (n:{rotulo} {{id: l.id}})
            ON CREATE SET n.em_recorte = false
            SET n.nome_canonico = l.nome_canonico""",
            linhas=linhas,
        )

    por_tipo: dict[str, list[dict]] = defaultdict(list)
    for r in consolidado["relacoes"]:
        por_tipo[r["tipo"]].append(r)
    for tipo, linhas in por_tipo.items():
        if tipo not in RELACOES_CLINICAS:
            raise ValueError(f"Tipo de relação inesperado: {tipo}")
        em_lote(
            f"""UNWIND $linhas AS l
            MATCH (a {{id: l.origem}}), (b {{id: l.destino}})
            MERGE (a)-[r:{tipo}]->(b)
            SET r.trecho_ids = l.trecho_ids, r.evidencias = l.evidencias, r.efeitos = l.efeitos,
                r.modelos = l.modelos, r.confianca_extracao = l.confianca_extracao""",
            linhas=linhas,
        )

    em_lote(
        """UNWIND $linhas AS l
        MATCH (t:Trecho {id: l[0]}), (n {id: l[1]})
        MERGE (t)-[:MENCIONA]->(n)""",
        linhas=[list(m) for m in consolidado["mencoes"]],
    )
    return consolidado


def calcular_algoritmos(driver: Driver) -> dict:
    """PageRank e Louvain sobre as relações clínicas, salvos como propriedades dos nós."""
    nome = "clinico"
    driver.execute_query("CALL gds.graph.drop($nome, false) YIELD graphName RETURN graphName", nome=nome)
    # O GDS recusa rótulos e tipos sem dados, então a projeção usa só os que existem.
    contagem = estatisticas(driver)
    rotulos = [r for r in ("Medicamento", "Enzima", "Efeito", "Condicao") if contagem["nos"].get(r)]
    projecao = {t: {"orientation": "UNDIRECTED"} for t in RELACOES_CLINICAS if contagem["relacoes"].get(t)}
    driver.execute_query(
        "CALL gds.graph.project($nome, $rotulos, $projecao) YIELD nodeCount RETURN nodeCount",
        nome=nome,
        rotulos=rotulos,
        projecao=projecao,
    )
    pagerank = driver.execute_query(
        "CALL gds.pageRank.write($nome, {writeProperty: 'pagerank'}) YIELD nodePropertiesWritten, ranIterations "
        "RETURN nodePropertiesWritten AS nos",
        nome=nome,
    ).records[0]["nos"]
    comunidades = driver.execute_query(
        "CALL gds.louvain.write($nome, {writeProperty: 'comunidade'}) YIELD communityCount, modularity "
        "RETURN communityCount AS total, modularity",
        nome=nome,
    ).records[0]
    driver.execute_query("CALL gds.graph.drop($nome) YIELD graphName RETURN graphName", nome=nome)
    return {"nos_pagerank": pagerank, "comunidades": comunidades["total"], "modularidade": comunidades["modularity"]}


def estatisticas(driver: Driver) -> dict:
    nos = driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS rotulo RETURN rotulo, count(*) AS total ORDER BY rotulo"
    ).records
    relacoes = driver.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS tipo, count(*) AS total ORDER BY tipo"
    ).records
    return {"nos": {r["rotulo"]: r["total"] for r in nos}, "relacoes": {r["tipo"]: r["total"] for r in relacoes}}


def exportar_grafo(driver: Driver, arquivo: Path = ARQ_GRAFO) -> dict:
    """Salva o grafo pronto para quem for reproduzir não precisar refazer a extração."""
    nos = driver.execute_query("MATCH (n) RETURN labels(n)[0] AS rotulo, properties(n) AS props ORDER BY props.id").records
    rels = driver.execute_query(
        "MATCH (a)-[r]->(b) RETURN a.id AS origem, type(r) AS tipo, b.id AS destino, properties(r) AS props "
        "ORDER BY origem, tipo, destino"
    ).records
    dados = {"nos": [dict(n) for n in nos], "relacoes": [dict(r) for r in rels]}
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(arquivo, "wt", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
    return {"nos": len(dados["nos"]), "relacoes": len(dados["relacoes"])}


def importar_grafo(driver: Driver, arquivo: Path = ARQ_GRAFO, tamanho_lote: int = 500) -> dict:
    """Carrega o grafo exportado em um Neo4j novo, como o Aura Free."""
    with gzip.open(arquivo, "rt", encoding="utf-8") as f:
        dados = json.load(f)
    criar_schema(driver)
    por_rotulo: dict[str, list[dict]] = defaultdict(list)
    for no in dados["nos"]:
        if no["rotulo"] not in ROTULOS:
            raise ValueError(f"Rótulo inesperado: {no['rotulo']}")
        por_rotulo[no["rotulo"]].append(no["props"])
    for rotulo, linhas in por_rotulo.items():
        for i in range(0, len(linhas), tamanho_lote):
            driver.execute_query(
                f"UNWIND $linhas AS l MERGE (n:{rotulo} {{id: l.id}}) SET n = l",
                linhas=linhas[i : i + tamanho_lote],
            )
    permitidos = (*RELACOES_CLINICAS, "PERTENCE_A", "TEM_BULA", "TEM_TRECHO", "MENCIONA")
    por_tipo: dict[str, list[dict]] = defaultdict(list)
    for rel in dados["relacoes"]:
        if rel["tipo"] not in permitidos:
            raise ValueError(f"Relação inesperada: {rel['tipo']}")
        por_tipo[rel["tipo"]].append(rel)
    for tipo, linhas in por_tipo.items():
        for i in range(0, len(linhas), tamanho_lote):
            driver.execute_query(
                f"""UNWIND $linhas AS l
                MATCH (a {{id: l.origem}}), (b {{id: l.destino}})
                MERGE (a)-[r:{tipo}]->(b) SET r = l.props""",
                linhas=linhas[i : i + tamanho_lote],
            )
    return {"nos": len(dados["nos"]), "relacoes": len(dados["relacoes"])}

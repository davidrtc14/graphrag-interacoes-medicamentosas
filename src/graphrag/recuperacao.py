"""Recuperação híbrida: busca vetorial, nomes de entidades e travessia Cypher multi-hop."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import permutations
from typing import Callable

from neo4j import Driver

from .cypher_seguro import executar_leitura
from .medicamentos import normalizar, nome_enzima

LIMITE_CAMINHOS = 12
LIMITE_TRECHOS = 6

_ELEMENTOS = """[n IN nodes(p) | {id: n.id, nome: n.nome_canonico, rotulo: labels(n)[0]}] AS nos,
       [r IN relationships(p) | {tipo: type(r), origem: startNode(r).id, destino: endNode(r).id,
        trecho_ids: r.trecho_ids, efeitos: r.efeitos, confianca: r.confianca_extracao}] AS rels,
       reduce(s = 0.0, n IN nodes(p) | s + coalesce(n.pagerank, 0.0)) AS relevancia"""

CLINICAS = "INTERAGE_COM|INIBE|INDUZ|E_METABOLIZADO_POR|PODE_CAUSAR|CONTRAINDICADO_EM"

# Consultas parametrizadas: nomes de usuário nunca entram no texto do Cypher.
CONSULTAS = {
    "interacao_direta": f"""MATCH p = (a:Medicamento {{id: $a}})-[:INTERAGE_COM]-(b:Medicamento {{id: $b}})
RETURN {_ELEMENTOS} LIMIT 5""",
    "mecanismo_entre_dois": f"""MATCH p = (a:Medicamento {{id: $a}})-[:INIBE|INDUZ]->(:Enzima)<-[:E_METABOLIZADO_POR]-(b:Medicamento {{id: $b}})
RETURN {_ELEMENTOS} ORDER BY relevancia DESC LIMIT 5""",
    "caminho_mais_curto": f"""MATCH p = shortestPath((a:Medicamento {{id: $a}})-[:{CLINICAS}*..4]-(b:Medicamento {{id: $b}}))
RETURN {_ELEMENTOS} LIMIT 1""",
    "vizinhanca": f"""MATCH p = (a {{id: $a}})-[:{CLINICAS}]-(x)
RETURN {_ELEMENTOS} ORDER BY coalesce(x.pagerank, 0.0) DESC LIMIT 12""",
    "afeta_outros": f"""MATCH p = (a:Medicamento {{id: $a}})-[:INIBE|INDUZ]->(:Enzima)<-[:E_METABOLIZADO_POR]-(b:Medicamento)
WHERE a <> b AND b.em_recorte
RETURN {_ELEMENTOS} ORDER BY relevancia DESC LIMIT 10""",
    "afetado_por_outros": f"""MATCH p = (a:Medicamento {{id: $a}})-[:E_METABOLIZADO_POR]->(:Enzima)<-[:INIBE|INDUZ]-(b:Medicamento)
WHERE a <> b AND b.em_recorte
RETURN {_ELEMENTOS} ORDER BY relevancia DESC LIMIT 10""",
    "risco_em_tres_saltos": f"""MATCH p = (a:Medicamento {{id: $a}})-[:INIBE]->(:Enzima)<-[:E_METABOLIZADO_POR]-(b:Medicamento)-[:PODE_CAUSAR]->(:Efeito)
WHERE a <> b AND b.em_recorte
RETURN {_ELEMENTOS} ORDER BY relevancia DESC LIMIT 6""",
    "enzima": f"""MATCH p = (e:Enzima {{id: $a}})<-[:INIBE|INDUZ|E_METABOLIZADO_POR]-(m:Medicamento)
RETURN {_ELEMENTOS} ORDER BY coalesce(m.pagerank, 0.0) DESC LIMIT 15""",
}

CONSULTA_VETORIAL = """CALL db.index.vector.queryNodes('trecho_embedding', $k, $vetor) YIELD node, score
MATCH (b:Bula)-[:TEM_TRECHO]->(node)
RETURN node.id AS id, node.texto AS texto, node.secao AS secao, b.url AS url, score
ORDER BY score DESC"""


@dataclass
class Caminho:
    nos: list[dict]
    rels: list[dict]
    origem_consulta: str

    @property
    def declarado(self) -> bool:
        """Um salto é o que a bula afirma; dois ou mais são uma inferência do grafo."""
        return len(self.rels) == 1

    @property
    def trecho_ids(self) -> list[str]:
        return list(dict.fromkeys(t for r in self.rels for t in (r.get("trecho_ids") or [])))

    @property
    def confianca(self) -> float:
        """A corrente é tão forte quanto o elo mais fraco."""
        return min((r.get("confianca") or 0.0) for r in self.rels) if self.rels else 0.0

    def chave(self) -> tuple:
        return tuple((r["origem"], r["tipo"], r["destino"]) for r in self.rels)

    def descrever(self) -> str:
        nomes = {n["id"]: n["nome"] for n in self.nos}
        partes = [f"{nomes[r['origem']]} -[{r['tipo']}]-> {nomes[r['destino']]}" for r in self.rels]
        efeitos = [e for r in self.rels for e in (r.get("efeitos") or [])]
        sufixo = f" | efeito descrito: {'; '.join(efeitos[:2])}" if efeitos else ""
        return "; ".join(partes) + sufixo


@dataclass
class Contexto:
    pergunta: str
    entidades: list[dict] = field(default_factory=list)
    caminhos: list[Caminho] = field(default_factory=list)
    trechos: list[dict] = field(default_factory=list)
    consultas: list[dict] = field(default_factory=list)
    linhas_geradas: list[dict] = field(default_factory=list)
    cypher_gerado: str = ""


def ligar_entidades(driver: Driver, pergunta: str) -> list[dict]:
    """Encontra medicamentos e enzimas da pergunta pelo nome, em português, inglês ou marca."""
    texto = normalizar(pergunta)
    registros = executar_leitura(
        driver,
        """MATCH (n) WHERE n:Medicamento OR n:Enzima
        RETURN n.id AS id, n.nome_canonico AS nome, labels(n)[0] AS rotulo, n.nome_pt AS nome_pt,
               coalesce(n.sinonimos, []) AS sinonimos""",
    )
    achadas: dict[str, tuple[int, dict]] = {}
    enzimas_citadas = {nome_enzima(m) for m in re.findall(r"cyp[\s-]*\d+\s*[a-z]\s*\d*|p-?gp|p-glicoprote\w+|bcrp", texto)}
    enzimas_citadas |= {"P-gp"} if re.search(r"p-?glicoprote", texto) else set()
    for r in registros:
        if r["rotulo"] == "Enzima":
            if r["nome"] in enzimas_citadas:
                achadas[r["id"]] = (texto.find(normalizar(r["nome"])), r)
            continue
        for apelido in [r["nome"], r["nome_pt"], *r["sinonimos"]]:
            if not apelido:
                continue
            achado = re.search(rf"\b{re.escape(normalizar(apelido))}\b", texto)
            if achado:
                achadas[r["id"]] = (achado.start(), r)
                break
    ordenadas = sorted(achadas.values(), key=lambda par: par[0])
    return [{"id": r["id"], "nome": r["nome"], "rotulo": r["rotulo"]} for _, r in ordenadas]


def buscar_trechos(driver: Driver, pergunta: str, embutir: Callable[[list[str]], list[list[float]]], k: int = LIMITE_TRECHOS) -> list[dict]:
    return executar_leitura(driver, CONSULTA_VETORIAL, {"k": k, "vetor": embutir([pergunta])[0]})


def _executar(driver: Driver, contexto: Contexto, nome: str, **parametros) -> None:
    linhas = executar_leitura(driver, CONSULTAS[nome], parametros)
    contexto.consultas.append({"nome": nome, "cypher": CONSULTAS[nome], "parametros": parametros, "linhas": len(linhas)})
    for linha in linhas:
        contexto.caminhos.append(Caminho(linha["nos"], linha["rels"], nome))


def percorrer_grafo(driver: Driver, contexto: Contexto) -> None:
    """Escolhe as travessias conforme as entidades encontradas na pergunta."""
    medicamentos = [e for e in contexto.entidades if e["rotulo"] == "Medicamento"]
    enzimas = [e for e in contexto.entidades if e["rotulo"] == "Enzima"]

    if len(medicamentos) >= 2:
        for a, b in permutations(medicamentos[:3], 2):
            if a["id"] < b["id"]:
                _executar(driver, contexto, "interacao_direta", a=a["id"], b=b["id"])
            _executar(driver, contexto, "mecanismo_entre_dois", a=a["id"], b=b["id"])
        if not contexto.caminhos:
            a, b = medicamentos[0], medicamentos[1]
            _executar(driver, contexto, "caminho_mais_curto", a=a["id"], b=b["id"])
    elif medicamentos:
        ident = medicamentos[0]["id"]
        for nome in ("vizinhanca", "afeta_outros", "afetado_por_outros", "risco_em_tres_saltos"):
            _executar(driver, contexto, nome, a=ident)
    for enzima in enzimas[:2]:
        _executar(driver, contexto, "enzima", a=enzima["id"])

    unicos: dict[tuple, Caminho] = {}
    for caminho in contexto.caminhos:
        unicos.setdefault(caminho.chave(), caminho)
    # Declarados primeiro: inferências só completam o contexto até o limite.
    ordenados = sorted(unicos.values(), key=lambda c: (not c.declarado, -c.confianca))
    contexto.caminhos = ordenados[:LIMITE_CAMINHOS]


def completar_trechos_citados(driver: Driver, contexto: Contexto) -> None:
    """Traz o texto original dos trechos que sustentam os caminhos encontrados."""
    ids = list(dict.fromkeys(t for c in contexto.caminhos for t in c.trecho_ids))
    ja_presentes = {t["id"] for t in contexto.trechos}
    faltantes = [t for t in ids if t not in ja_presentes]
    if not faltantes:
        return
    linhas = executar_leitura(
        driver,
        """MATCH (b:Bula)-[:TEM_TRECHO]->(t:Trecho) WHERE t.id IN $ids
        RETURN t.id AS id, t.texto AS texto, t.secao AS secao, b.url AS url, null AS score""",
        {"ids": faltantes},
    )
    contexto.trechos.extend(linhas)


def recuperar(driver: Driver, pergunta: str, embutir: Callable[[list[str]], list[list[float]]]) -> Contexto:
    contexto = Contexto(pergunta=pergunta)
    contexto.entidades = ligar_entidades(driver, pergunta)
    contexto.trechos = buscar_trechos(driver, pergunta, embutir)
    percorrer_grafo(driver, contexto)
    completar_trechos_citados(driver, contexto)
    return contexto

"""Validação do Cypher gerado pelo LLM antes de executá-lo."""
from __future__ import annotations

import re

from neo4j import Driver, unit_of_work

LIMITE_LINHAS = 25
TEMPO_MAXIMO_S = 10.0

_PROIBIDAS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH|CALL|GRANT|REVOKE|DENY|ALTER|RENAME|START|STOP|USE|TERMINATE)\b",
    re.I,
)
_LITERAIS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.I)


class CypherRecusado(ValueError):
    """O Cypher gerado não passou na validação."""


def validar_cypher(cypher: str) -> str:
    """Aceita só uma consulta de leitura, com LIMIT. Devolve o texto final."""
    texto = re.sub(r"^```(?:cypher)?\s*|\s*```$", "", cypher.strip()).strip().rstrip(";").strip()
    sem_literais = _LITERAIS.sub("''", texto)
    if not texto:
        raise CypherRecusado("consulta vazia")
    if ";" in sem_literais or "//" in sem_literais or "/*" in sem_literais:
        raise CypherRecusado("somente uma consulta, sem comentários")
    proibida = _PROIBIDAS.search(sem_literais)
    if proibida:
        raise CypherRecusado(f"palavra não permitida: {proibida.group(1).upper()}")
    if not re.match(r"^(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND)\b", sem_literais, re.I):
        raise CypherRecusado("a consulta precisa começar com MATCH, OPTIONAL MATCH, WITH ou UNWIND")
    if not re.search(r"\bRETURN\b", sem_literais, re.I):
        raise CypherRecusado("a consulta precisa de RETURN")
    limite = _LIMIT.search(sem_literais)
    if limite is None:
        return f"{texto}\nLIMIT {LIMITE_LINHAS}"
    if int(limite.group(1)) > LIMITE_LINHAS:
        return _LIMIT.sub(f"LIMIT {LIMITE_LINHAS}", texto)
    return texto


def executar_leitura(driver: Driver, cypher: str, parametros: dict | None = None) -> list[dict]:
    """A transação de leitura faz o próprio Neo4j recusar qualquer escrita."""

    @unit_of_work(timeout=TEMPO_MAXIMO_S)
    def trabalho(tx):
        return [dict(r) for r in tx.run(cypher, parametros or {})]

    with driver.session() as sessao:
        return sessao.execute_read(trabalho)

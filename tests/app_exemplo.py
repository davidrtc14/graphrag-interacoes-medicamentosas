"""Interface com o grafo de exemplo dos testes e LLM falso, para conferir a tela sem chaves.

Rode antes os testes de integração, que preparam a base do Neo4j de teste.
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RAIZ / "app"), str(RAIZ / "src")]
os.environ["NEO4J_URI"] = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7688")

import streamlit_app

from graphrag.llm import RespostaLLM


class LLMExemplo:
    def gerar(self, prompt, sistema="", json_mode=False):
        if "consulta Cypher" in sistema:
            texto = '{"cypher": "MATCH (m:Medicamento) RETURN count(m) AS total"}'
        else:
            texto = (
                '{"abstencao": false, "resposta": "Resposta de exemplo, sem LLM real. A claritromicina inibe a '
                'CYP3A4 [C1], enzima que metaboliza a sinvastatina. Isso indica só um possível mecanismo."}'
            )
        return RespostaLLM(texto, "exemplo")


streamlit_app.criar_llm = lambda config: LLMExemplo()
streamlit_app.main()

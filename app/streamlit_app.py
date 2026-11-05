"""Interface: pergunta, resposta citada, subgrafo interativo e rastreabilidade."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from graphrag.config import carregar_config
from graphrag.embeddings import embutir
from graphrag.grafo import conectar
from graphrag.llm import ErroLLM, criar_llm
from graphrag.recuperacao import Contexto
from graphrag.resposta import AVISO, Resposta, responder

CORES = {"Medicamento": "#4C78A8", "Enzima": "#F58518", "Efeito": "#E45756", "Condicao": "#72B7B2"}
EXEMPLOS = [
    "A claritromicina pode alterar o efeito da sinvastatina?",
    "Quais medicamentos podem ser afetados pelo cetoconazol?",
    "A rifampicina pode interferir na apixabana?",
    "Quais medicamentos inibem a CYP3A4?",
]


@st.cache_resource
def recursos():
    config = carregar_config()
    return conectar(config), criar_llm(config)


def desenhar_subgrafo(contexto: Contexto) -> str:
    rede = Network(height="460px", width="100%", directed=True, bgcolor="#ffffff", font_color="#222222", cdn_resources="in_line")
    rede.barnes_hut(spring_length=170)
    for caminho in contexto.caminhos:
        for no in caminho.nos:
            rede.add_node(no["id"], label=no["nome"], title=no["rotulo"], color=CORES.get(no["rotulo"], "#999999"), size=22, font={"size": 18})
    vistas = set()
    for caminho in contexto.caminhos:
        for rel in caminho.rels:
            chave = (rel["origem"], rel["tipo"], rel["destino"])
            if chave in vistas:
                continue
            vistas.add(chave)
            rede.add_edge(rel["origem"], rel["destino"], label=rel["tipo"], title=", ".join(rel.get("trecho_ids") or []),
                          dashes=not caminho.declarado, font={"size": 14, "align": "top"})
    return rede.generate_html(notebook=False)


def mostrar_rastreabilidade(r: Resposta) -> None:
    contexto = r.contexto
    if contexto is None:
        return
    declarados = [c for c in contexto.caminhos if c.declarado]
    inferidos = [c for c in contexto.caminhos if not c.declarado]

    if contexto.caminhos:
        st.subheader("Subgrafo usado")
        st.caption("Linha contínua: declarado na bula. Linha tracejada: inferido pelo grafo, indica só um possível mecanismo.")
        components.html(desenhar_subgrafo(contexto), height=480)

    with st.expander(f"Nós consultados ({len({n['id'] for c in contexto.caminhos for n in c.nos})})"):
        nos = {n["id"]: n for c in contexto.caminhos for n in c.nos}
        st.dataframe([{"nome": n["nome"], "tipo": n["rotulo"]} for n in nos.values()], width="stretch")
    with st.expander(f"Caminhos: {len(declarados)} declarados, {len(inferidos)} inferidos"):
        for caminho in contexto.caminhos:
            st.markdown(f"- {'Declarado' if caminho.declarado else 'Inferido'}: `{caminho.descrever()}`")
    with st.expander(f"Consultas Cypher ({len(contexto.consultas)})"):
        for consulta in contexto.consultas:
            st.markdown(f"**{consulta['nome']}**: {consulta['linhas']} linhas, parâmetros `{consulta['parametros']}`")
            if consulta.get("erro"):
                st.warning(f"Recusada ou com falha: {consulta['erro']}")
            if consulta["cypher"]:
                st.code(consulta["cypher"], language="cypher")
    with st.expander("Evidências citadas"):
        for citacao in r.citacoes:
            st.markdown(f"**[{citacao}]** {r.evidencias[citacao]}")
        urls = sorted({t["url"] for t in contexto.trechos if t.get("url")})
        if urls:
            st.markdown("**Bulas de origem:** " + " · ".join(f"[{u.split('setid=')[-1][:8]}]({u})" for u in urls))


def main() -> None:
    st.set_page_config(page_title="Interações Medicamentosas", layout="wide")
    st.title("Assistente de interações medicamentosas")
    st.warning(AVISO + " Não digite dados pessoais ou de pacientes.")

    escolhida = st.selectbox("Exemplos", [""] + EXEMPLOS, format_func=lambda x: x or "Escolha um exemplo ou escreva abaixo")
    pergunta = st.text_input("Pergunta", value=escolhida, max_chars=300)
    if not st.button("Perguntar", type="primary") or not pergunta.strip():
        return

    try:
        driver, llm = recursos()
        with st.spinner("Consultando o grafo e as bulas..."):
            resposta = responder(pergunta.strip(), llm, driver, embutir)
    except ErroLLM as erro:
        st.error(f"Os provedores de LLM não responderam. {erro}")
        return
    except Exception as erro:
        st.error(f"Não foi possível consultar o sistema: {type(erro).__name__}. Confira se o Neo4j está ativo.")
        return

    if resposta.abstencao:
        st.info(resposta.texto)
        st.caption(f"Motivo: {resposta.motivo_abstencao}.")
    else:
        st.markdown(resposta.texto)
        col1, col2, col3 = st.columns(3)
        col1.metric("Nível de confiança", f"{resposta.nivel} ({resposta.confianca:.2f})")
        col2.metric("Modelo", resposta.modelo or "-")
        col3.metric("Tempo", f"{resposta.latencia_s:.1f} s")
        st.caption("A confiança é calculada pela evidência citada. É uma regra do sistema, não uma probabilidade calibrada.")
    mostrar_rastreabilidade(resposta)


if __name__ == "__main__":
    main()

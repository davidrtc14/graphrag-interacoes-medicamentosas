import re

import pytest

from graphrag.avaliacao import carregar_perguntas, executar, gerar_revisao, pontuar, resumir
from graphrag.openfda import carregar_bulas

PERGUNTAS = carregar_perguntas()
BULAS = {b["generico"]: " ".join(b["secoes"].values()) for b in carregar_bulas()}
CORPO = " ".join(BULAS.values()).lower()


def _compacto(texto):
    return re.sub(r"\s+", " ", texto).lower()


def test_conjunto_tem_a_composicao_planejada():
    tipos = [p["tipo"] for p in PERGUNTAS]
    assert (tipos.count("direta"), tipos.count("multi_hop"), tipos.count("sem_resposta")) == (15, 15, 10)
    assert len({p["id"] for p in PERGUNTAS}) == 40


@pytest.mark.parametrize("pergunta", [p for p in PERGUNTAS if p["tipo"] != "sem_resposta"], ids=lambda p: p["id"])
def test_gabarito_existe_literalmente_nas_bulas(pergunta):
    for evidencia in pergunta["evidencias"]:
        assert _compacto(evidencia["texto"]) in _compacto(BULAS[evidencia["bula"]]), evidencia


@pytest.mark.parametrize("pergunta", [p for p in PERGUNTAS if p["tipo"] == "sem_resposta"], ids=lambda p: p["id"])
def test_perguntas_sem_resposta_nao_tem_o_assunto_nas_bulas(pergunta):
    for termo in pergunta["termos_ausentes"]:
        assert termo not in CORPO, termo


def test_multi_hop_usa_mais_de_uma_bula():
    for p in PERGUNTAS:
        if p["tipo"] == "multi_hop":
            assert len({e["bula"] for e in p["evidencias"]}) >= 2, p["id"]


def test_pontuacao():
    direta = {"tipo": "direta", "deve_conter": ["20\\s?mg"]}
    sem = {"tipo": "sem_resposta"}
    assert pontuar(direta, "até 20 mg [C1]", False)["acerto"] is True
    assert pontuar(direta, "até 40 mg", False)["acerto"] is False
    assert pontuar(direta, "20 mg", True)["acerto"] is False
    assert pontuar(sem, "", True)["abstencao_correta"] is True
    assert pontuar(sem, "sim", False)["abstencao_correta"] is False


def test_execucao_retoma_sem_refazer_e_gera_saidas(tmp_path, llm_falso):
    perguntas = [{"id": "A", "tipo": "direta", "pergunta": "p", "deve_conter": ["x"]}, {"id": "B", "tipo": "sem_resposta", "pergunta": "q"}]
    arquivo = tmp_path / "r.jsonl"
    llm = llm_falso([{"abstencao": False, "resposta": "x"}, {"abstencao": True, "resposta": ""}])
    executar(perguntas, llm, None, None, arquivo, modos=("llm_puro",))
    registros = executar(perguntas, llm, None, None, arquivo, modos=("llm_puro",))
    assert len(llm.chamadas) == 2 and len(registros) == 2
    resumo = resumir(registros)["llm_puro"]
    assert resumo["acerto_direta"] == 1.0 and resumo["abstencao_correta_sem_resposta"] == 1.0
    revisao = tmp_path / "revisao.csv"
    gerar_revisao(registros, revisao)
    assert "sustentada_pela_fonte" in revisao.read_text(encoding="utf-8-sig")

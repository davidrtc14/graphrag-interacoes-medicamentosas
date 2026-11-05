import pytest

from graphrag.cypher_seguro import CypherRecusado, validar_cypher
from graphrag.extracao import evidencia_no_texto, extrair_bula, validar_relacoes
from graphrag.grafo import consolidar
from graphrag.llm import CotaDiariaEsgotada, Encadeado, ErroLLM, RespostaLLM, interpretar_json
from graphrag.medicamentos import Medicamento, Resolvedor, nome_enzima, normalizar
from graphrag.openfda import Trecho, dividir_em_trechos, escolher_bula
from graphrag.recuperacao import Caminho, Contexto
from graphrag.resposta import calcular_confianca, extrair_citacoes

MEDS = [Medicamento("simvastatin", "sinvastatina", "Estatina"), Medicamento("rifampin", "rifampicina", "Rifamicina")]


@pytest.mark.parametrize("entrada,esperado", [("CYP 3A4", "CYP3A4"), ("cyp3a4", "CYP3A4"), ("CYP-2C19", "CYP2C19"),
                                              ("CYP3A", "CYP3A4"), ("CYP 3A isoenzymes", "CYP3A4"),("P-glycoprotein", "P-gp"), ("Pgp", "P-gp"), ("aspirin", None)])
def test_nome_enzima(entrada, esperado):
    assert nome_enzima(entrada) == esperado


@pytest.mark.parametrize("entrada", ["Simvastatin", "sinvastatina", "SIMVASTATIN HYDROCHLORIDE", "Zocor", "simvastatine", "Simvastatin acid"])
def test_resolvedor_unifica_variacoes(entrada):
    assert Resolvedor(MEDS, {"Zocor": "simvastatin"}).medicamento(entrada) == "simvastatin"


def test_resolvedor_nao_funde_medicamentos_diferentes():
    r = Resolvedor([Medicamento("pravastatin", "pravastatina", "E"), Medicamento("simvastatin", "sinvastatina", "E")])
    assert r.medicamento("lovastatin") == "lovastatin"
    assert r.medicamento("rifampicin") == "rifampin"
    assert normalizar("Fenitoína") == "fenitoina"


def test_escolher_bula_recusa_combinacao_e_prefere_a_mais_recente():
    combinada = {"effective_time": "20260101", "openfda": {"generic_name": ["LISINOPRIL AND HYDROCHLOROTHIAZIDE"], "substance_name": ["A", "B"]}}
    antiga = {"effective_time": "20200101", "openfda": {"generic_name": ["LISINOPRIL"], "substance_name": ["LISINOPRIL"]}}
    nova = {"effective_time": "20250101", "openfda": {"generic_name": ["LISINOPRIL"], "substance_name": ["LISINOPRIL"]}}
    assert escolher_bula("lisinopril", [combinada, antiga, nova]) is nova
    assert escolher_bula("lisinopril", [combinada]) is None


def test_trechos_filtram_farmacologia_mas_mantem_interacoes():
    bula = {"generico": "x", "secoes": {"drug_interactions": "Avoid alcohol. Take care.",
                                        "clinical_pharmacology": "It lowers pressure. It is metabolized by CYP3A4."}}
    trechos = dividir_em_trechos(bula)
    por_secao = {t.secao: t.texto for t in trechos}
    assert "Avoid alcohol" in por_secao["drug_interactions"]
    assert "lowers pressure" not in por_secao["clinical_pharmacology"]
    assert "CYP3A4" in por_secao["clinical_pharmacology"]


BASE = {"trecho_id": "t1", "origem": "xilo", "tipo_origem": "Medicamento", "tipo": "INIBE", "destino": "CYP3A4",
        "tipo_destino": "Enzima", "evidencia": "strong inhibitor of CYP3A4", "confianca": 0.9}
TRECHOS = {"t1": Trecho("t1", "x", "drug_interactions", "X is a strong inhibitor of CYP3A4.")}


@pytest.mark.parametrize("alteracao,motivo", [
    ({"evidencia": "an inducer of CYP2D6"}, "evidência não encontrada nos trechos"),
    ({"tipo_destino": "Efeito"}, "combinação de tipos"),
    ({"confianca": 7}, "formato"),
])
def test_validacao_rejeita_extracao_ruim(alteracao, motivo):
    aceitas, rejeitadas = validar_relacoes([{**BASE, **alteracao}], TRECHOS)
    assert not aceitas and rejeitadas[0]["motivo"] == motivo


def test_validacao_aceita_evidencia_literal_com_outra_pontuacao():
    aceitas, _ = validar_relacoes([{**BASE, "evidencia": "strong  inhibitor of CYP3A4."}], TRECHOS)
    assert len(aceitas) == 1


def test_validacao_completa_tipos_omitidos_pelo_modelo():
    sem_tipos = {k: v for k, v in BASE.items() if k not in ("tipo_origem", "tipo_destino")}
    aceitas, _ = validar_relacoes([sem_tipos], TRECHOS)
    assert aceitas[0].tipo_origem == "Medicamento" and aceitas[0].tipo_destino == "Enzima"


def test_consolidar_descarta_entidades_invalidas():
    base = {"trecho_id": "a", "tipo_origem": "Medicamento", "tipo": "INIBE", "tipo_destino": "Enzima", "evidencia": "e", "efeito": "", "confianca": 0.9, "modelo": "m"}
    relacoes = [{**base, "origem": "simvastatin", "destino": "CYP450"}, {**base, "origem": "simvastatin", "destino": "rifampin"},
                {**base, "origem": "tyrosine kinase inhibitor", "destino": "CYP3A4"}, {**base, "origem": "simvastatin", "destino": "CITP3A4"}]
    c = consolidar([{"relacoes": relacoes}], Resolvedor(MEDS))
    assert c["descartadas"] == 3
    assert [r["destino"] for r in c["relacoes"]] == ["enzima:cyp3a4"]


def test_evidencia_com_reticencias_exige_as_partes_em_ordem():
    texto = "Simvastatin is a substrate of CYP3A4 and of the transport protein OATP1B1."
    assert evidencia_no_texto("Simvastatin is a substrate of ... the transport protein OATP1B1", texto)
    assert not evidencia_no_texto("the transport protein OATP1B1 ... Simvastatin is a substrate", texto)
    assert not evidencia_no_texto("Simvastatin ... inducer of CYP2D6", texto)
    assert not evidencia_no_texto("is ... of", texto)


def test_validacao_corrige_id_errado_quando_a_evidencia_existe_em_outro_trecho():
    trechos = {**TRECHOS, "t2": Trecho("t2", "x", "contraindications", "Avoid in pregnancy.")}
    aceitas, _ = validar_relacoes([{**BASE, "trecho_id": "t2"}], trechos)
    assert aceitas[0].trecho_id == "t1"


def test_lote_salvo_nao_e_pago_de_novo_apos_falha(tmp_path, llm_falso):
    trechos = [Trecho("a", "x", "s", "y" * 8000 + " strong inhibitor of CYP3A4"), Trecho("b", "x", "s", "z" * 100)]
    relacao = {**BASE, "trecho_id": "a"}
    primeiro = llm_falso([{"relacoes": [relacao]}, "inválido", "inválido"])
    with pytest.raises(ErroLLM):
        extrair_bula("x", trechos, primeiro, tmp_path)
    segundo = llm_falso([{"relacoes": []}])
    resultado = extrair_bula("x", trechos, segundo, tmp_path)
    assert len(segundo.chamadas) == 1 and len(resultado["relacoes"]) == 1
    assert not list((tmp_path / ".parciais").iterdir())


def test_extracao_nao_gasta_cota_duas_vezes(tmp_path, llm_falso):
    llm = llm_falso([{"relacoes": [BASE]}])
    trechos = [TRECHOS["t1"]]
    assert len(extrair_bula("x", trechos, llm, tmp_path)["relacoes"]) == 1
    assert len(extrair_bula("x", trechos, llm, tmp_path)["relacoes"]) == 1
    assert len(llm.chamadas) == 1


def test_consolidar_junta_sinonimos_e_interacao_sem_direcao():
    r1 = {"trecho_id": "a", "origem": "Simvastatin", "tipo_origem": "Medicamento", "tipo": "INTERAGE_COM", "destino": "rifampicin",
          "tipo_destino": "Medicamento", "evidencia": "e1", "efeito": "x", "confianca": 0.5, "modelo": "m"}
    r2 = {**r1, "trecho_id": "b", "origem": "rifampin", "destino": "sinvastatina", "evidencia": "e2", "confianca": 0.8}
    autorref = {**r1, "origem": "simvastatin", "destino": "sinvastatina"}
    c = consolidar([{"relacoes": [r1, r2, autorref]}], Resolvedor(MEDS))
    assert len(c["relacoes"]) == 1
    assert c["relacoes"][0]["trecho_ids"] == ["a", "b"]
    assert c["relacoes"][0]["confianca_extracao"] == 0.8
    assert c["descartadas"] == 1


@pytest.mark.parametrize("cypher", [
    "MATCH (n) DETACH DELETE n", "CREATE (n:X) RETURN n", "MATCH (n) SET n.a = 1 RETURN n", "CALL db.labels() YIELD label RETURN label",
    "MATCH (n) RETURN n; MATCH (m) DELETE m", "MATCH (n) // x\nRETURN n", "LOAD CSV FROM 'http://x' AS l RETURN l", "MATCH (n)", "", "RETURN 1",
])
def test_cypher_perigoso_e_recusado(cypher):
    with pytest.raises(CypherRecusado):
        validar_cypher(cypher)


def test_cypher_de_leitura_recebe_limite():
    assert validar_cypher("MATCH (n) RETURN n").endswith("LIMIT 25")
    assert validar_cypher("MATCH (n) RETURN n LIMIT 5;").endswith("LIMIT 5")
    assert validar_cypher("MATCH (n) RETURN n LIMIT 9999").endswith("LIMIT 25")


def test_palavra_proibida_dentro_de_texto_e_permitida():
    assert "DELETE" in validar_cypher("MATCH (n {nome: 'DELETE'}) RETURN n")


def test_interpretar_json_aceita_bloco_cercado():
    assert interpretar_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert interpretar_json('Claro: {"a": 2}') == {"a": 2}


def test_encadeado_usa_reserva_e_informa_falha_total():
    class Falha:
        def gerar(self, *a, **k):
            raise RuntimeError("cota")

    class Ok:
        def gerar(self, *a, **k):
            return RespostaLLM("ok", "reserva")

    assert Encadeado([Falha(), Ok()]).gerar("p").modelo == "reserva"
    with pytest.raises(ErroLLM):
        Encadeado([Falha()]).gerar("p")
    with pytest.raises(ErroLLM):
        Encadeado([])


def test_encadeado_troca_de_provedor_com_json_cortado_e_cota_diaria():
    class Cortado:
        def gerar(self, *a, **k):
            return RespostaLLM('{"relacoes": [{', "cortado")

    class SemCota:
        chamadas = 0

        def gerar(self, *a, **k):
            SemCota.chamadas += 1
            raise CotaDiariaEsgotada("per day")

    class Ok:
        def gerar(self, *a, **k):
            return RespostaLLM('{"a": 1}', "ok")

    cadeia = Encadeado([Cortado(), SemCota(), Ok()])
    assert cadeia.gerar("p", json_mode=True).modelo == "ok"
    assert cadeia.gerar("p", json_mode=True).modelo == "ok"
    assert SemCota.chamadas == 1
    assert not cadeia.todos_esgotados


def _caminho(saltos, confianca=0.9):
    nos = [{"id": f"n{i}", "nome": f"n{i}", "rotulo": "Medicamento"} for i in range(saltos + 1)]
    rels = [{"tipo": "INIBE", "origem": f"n{i}", "destino": f"n{i + 1}", "trecho_ids": ["t"], "confianca": confianca} for i in range(saltos)]
    return Caminho(nos, rels, "x")


def test_citacoes_agrupadas_nao_escapam_da_validacao():
    assert extrair_citacoes("Fato [C2, T7] e outro [C2] e [Q99].") == ["C2", "T7", "Q99"]


def test_confianca_limita_resposta_so_com_inferencia():
    inferido = Contexto("p", caminhos=[_caminho(2), _caminho(2), _caminho(2)])
    declarado = Contexto("p", caminhos=[_caminho(1), _caminho(1), _caminho(1)])
    assert calcular_confianca(inferido, ["C1", "C2", "C3"])[0] <= 0.6
    assert calcular_confianca(declarado, ["C1", "C2", "C3"])[1] == "alto"
    assert calcular_confianca(declarado, []) == (0.0, "nenhum")

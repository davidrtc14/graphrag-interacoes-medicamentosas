"""Extração de entidades e relações (NER/RE) com LLM, com resultado salvo em arquivo."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .config import DIR_EXTRACOES
from .llm import LLM, ErroLLM
from .medicamentos import normalizar
from .openfda import Trecho

MAX_CHARS_LOTE = 8000

TipoEntidade = Literal["Medicamento", "Enzima", "Efeito", "Condicao"]
TipoRelacao = Literal["INIBE", "INDUZ", "E_METABOLIZADO_POR", "INTERAGE_COM", "PODE_CAUSAR", "CONTRAINDICADO_EM"]

COMBINACOES_VALIDAS = {
    ("Medicamento", "INIBE", "Enzima"),
    ("Medicamento", "INDUZ", "Enzima"),
    ("Medicamento", "E_METABOLIZADO_POR", "Enzima"),
    ("Medicamento", "INTERAGE_COM", "Medicamento"),
    ("Medicamento", "PODE_CAUSAR", "Efeito"),
    ("Medicamento", "CONTRAINDICADO_EM", "Condicao"),
}

TIPOS_POR_RELACAO = {tipo: (origem, destino) for origem, tipo, destino in COMBINACOES_VALIDAS}

SISTEMA_EXTRACAO = """Você extrai relações farmacológicas de trechos de bulas em inglês.
Use apenas o que está escrito no trecho. Nunca use conhecimento externo.

Tipos de relação (origem -> destino):
- INIBE: Medicamento -> Enzima. O medicamento inibe a enzima ou o transportador.
- INDUZ: Medicamento -> Enzima. O medicamento induz a enzima ou o transportador.
- E_METABOLIZADO_POR: Medicamento -> Enzima. O medicamento é metabolizado pela enzima ou é substrato dela.
- INTERAGE_COM: Medicamento -> Medicamento. O texto afirma uma interação entre dois medicamentos nomeados.
- PODE_CAUSAR: Medicamento -> Efeito. O texto associa o medicamento a um efeito adverso.
- CONTRAINDICADO_EM: Medicamento -> Condicao. O texto contraindica o medicamento em uma condição.

Regras:
- Escreva nomes em inglês, no singular. Medicamento: nome genérico, sem sal. Enzima: como CYP3A4 ou P-gp.
- Classes genéricas, como "strong CYP3A4 inhibitors", não são Medicamento. Extraia apenas os exemplos nomeados.
- "evidencia" é uma cópia literal e contínua, sem reticências, de até 200 caracteres do trecho que sustenta a relação.
- "efeito" é um resumo em inglês do resultado, só em INTERAGE_COM. Nas demais relações, deixe vazio.
- "confianca" vai de 0 a 1 e indica o quanto o texto afirma a relação de forma direta.
- Se um trecho não tiver relações, não invente nenhuma.

Responda somente em JSON:
{"relacoes": [{"trecho_id": "...", "origem": "...", "tipo_origem": "Medicamento", "tipo": "INIBE",
"destino": "...", "tipo_destino": "Enzima", "evidencia": "...", "efeito": "", "confianca": 0.9}]}"""


class Relacao(BaseModel):
    trecho_id: str
    origem: str = Field(min_length=2)
    tipo_origem: TipoEntidade
    tipo: TipoRelacao
    destino: str = Field(min_length=2)
    tipo_destino: TipoEntidade
    evidencia: str = Field(min_length=3)
    efeito: str = ""
    confianca: float = Field(ge=0, le=1)


def _compacto(texto: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalizar(texto))


def evidencia_no_texto(evidencia: str, texto: str) -> bool:
    """Confere a cópia literal. Com reticências, cada parte precisa aparecer na ordem."""
    partes = [_compacto(p) for p in re.split(r"\.{3}|…", evidencia)]
    partes = [p for p in partes if p]
    if sum(map(len, partes)) < 10:
        return False
    alvo, posicao = _compacto(texto), 0
    for parte in partes:
        posicao = alvo.find(parte, posicao)
        if posicao == -1:
            return False
        posicao += len(parte)
    return True


def validar_relacoes(brutas: list[dict], trechos: dict[str, Trecho]) -> tuple[list[Relacao], list[dict]]:
    """Descarta o que não tem formato, tipo válido ou evidência literal no trecho citado."""
    aceitas, rejeitadas = [], []
    for bruta in brutas:
        try:
            # Alguns modelos omitem os tipos das pontas, que o tipo da relação já determina.
            pontas = TIPOS_POR_RELACAO.get(bruta.get("tipo"), (None, None))
            completa = {**bruta, "tipo_origem": bruta.get("tipo_origem") or pontas[0],
                        "tipo_destino": bruta.get("tipo_destino") or pontas[1]}
            relacao = Relacao.model_validate(completa)
        except ValidationError:
            rejeitadas.append({**bruta, "motivo": "formato"})
            continue
        if (relacao.tipo_origem, relacao.tipo, relacao.tipo_destino) not in COMBINACOES_VALIDAS:
            rejeitadas.append({**bruta, "motivo": "combinação de tipos"})
            continue
        citado = trechos.get(relacao.trecho_id)
        if citado is not None and evidencia_no_texto(relacao.evidencia, citado.texto):
            aceitas.append(relacao)
            continue
        # O LLM às vezes erra o ID: vale o trecho onde a evidência realmente está.
        correto = next((t for t in trechos.values() if evidencia_no_texto(relacao.evidencia, t.texto)), None)
        if correto is None:
            rejeitadas.append({**bruta, "motivo": "evidência não encontrada nos trechos"})
        else:
            aceitas.append(relacao.model_copy(update={"trecho_id": correto.id}))
    return aceitas, rejeitadas


def _lotes(trechos: list[Trecho]) -> list[list[Trecho]]:
    lotes, atual, tamanho = [], [], 0
    for trecho in trechos:
        if atual and tamanho + len(trecho.texto) > MAX_CHARS_LOTE:
            lotes.append(atual)
            atual, tamanho = [], 0
        atual.append(trecho)
        tamanho += len(trecho.texto)
    if atual:
        lotes.append(atual)
    return lotes


def _chamar_com_json(llm: LLM, prompt: str, generico: str) -> tuple[list[dict], str]:
    """Tenta de novo uma vez quando o modelo devolve JSON inválido ou fora do formato."""
    for _ in range(2):
        resposta = llm.gerar(prompt, SISTEMA_EXTRACAO, json_mode=True)
        try:
            brutas = resposta.json()["relacoes"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if isinstance(brutas, list):
            return brutas, resposta.modelo
    raise ErroLLM(f"{resposta.modelo} devolveu JSON inválido para {generico}")


def extrair_bula(generico: str, trechos: list[Trecho], llm: LLM, destino: Path = DIR_EXTRACOES) -> dict:
    """Extrai uma bula e salva o resultado. Se o arquivo já existir, não gasta cota."""
    destino.mkdir(parents=True, exist_ok=True)
    arquivo = destino / f"{generico}.json"
    if arquivo.exists():
        return json.loads(arquivo.read_text(encoding="utf-8"))

    por_id = {t.id: t for t in trechos}
    aceitas: list[dict] = []
    rejeitadas: list[dict] = []
    modelos: set[str] = set()
    parciais = destino / ".parciais"
    parciais.mkdir(exist_ok=True)
    arquivos_lote = []
    for i, lote in enumerate(_lotes(trechos)):
        arquivo_lote = parciais / f"{generico}.{i}.json"
        arquivos_lote.append(arquivo_lote)
        if arquivo_lote.exists():
            parcial = json.loads(arquivo_lote.read_text(encoding="utf-8"))
        else:
            texto = "\n\n".join(f"[{t.id}] (bula de {t.medicamento}, seção {t.secao})\n{t.texto}" for t in lote)
            brutas, modelo = _chamar_com_json(llm, f"Trechos:\n\n{texto}", generico)
            boas, ruins = validar_relacoes(brutas, por_id)
            parcial = {"aceitas": [{**r.model_dump(), "modelo": modelo} for r in boas], "rejeitadas": ruins, "modelo": modelo}
            # Cada lote fica salvo: se a cota acabar no meio da bula, nada é pago duas vezes.
            arquivo_lote.write_text(json.dumps(parcial, ensure_ascii=False), encoding="utf-8")
        aceitas += parcial["aceitas"]
        rejeitadas += parcial["rejeitadas"]
        modelos.add(parcial["modelo"])

    resultado = {
        "generico": generico,
        "modelos": sorted(modelos),
        "trechos": [asdict(t) for t in trechos],
        "relacoes": aceitas,
        "rejeitadas": rejeitadas,
    }
    arquivo.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    for arquivo_lote in arquivos_lote:
        arquivo_lote.unlink()
    return resultado

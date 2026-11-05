"""Download das bulas da openFDA e divisão em trechos."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import DIR_BULAS
from .medicamentos import Medicamento

URL_API = "https://api.fda.gov/drug/label.json"
SECOES_COMPLETAS = ("drug_interactions", "contraindications")
SECOES_FILTRADAS = ("clinical_pharmacology", "pharmacokinetics")
_REGEX_RELEVANTE = re.compile(r"CYP|P-gp|P-glycoprotein|metaboli[sz]|inhibit|induc|substrate", re.I)
_REGEX_FRASE = re.compile(r"(?<=[.;])\s+(?=[A-Z(\[])")

MAX_CHARS_TRECHO = 1200


@dataclass(frozen=True)
class Trecho:
    id: str
    medicamento: str
    secao: str
    texto: str


def escolher_bula(generico: str, candidatas: list[dict]) -> dict | None:
    """Prefere a bula mais recente de princípio ativo único, sem combinações."""

    def principio_unico(bula: dict) -> bool:
        info = bula.get("openfda", {})
        nomes = info.get("generic_name", [])
        substancias = info.get("substance_name", [])
        return (
            len(nomes) == 1
            and len(substancias) <= 1
            and generico.lower() in nomes[0].lower()
            and not re.search(r"\band\b|,", nomes[0], re.I)
        )

    unicas = [b for b in candidatas if principio_unico(b)]
    if not unicas:
        return None
    return max(unicas, key=lambda b: b.get("effective_time", ""))


def resumir_bula(generico: str, bula: dict) -> dict:
    info = bula.get("openfda", {})
    return {
        "generico": generico,
        "set_id": bula.get("set_id", ""),
        "versao": bula.get("effective_time", ""),
        "marcas": sorted(set(info.get("brand_name", []))),
        "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={bula.get('set_id', '')}",
        "secoes": {s: " ".join(bula[s]) for s in SECOES_COMPLETAS + SECOES_FILTRADAS if s in bula},
    }


def baixar_bulas(medicamentos: list[Medicamento], destino: Path = DIR_BULAS) -> list[str]:
    """Baixa o que falta e devolve os medicamentos sem bula adequada."""
    destino.mkdir(parents=True, exist_ok=True)
    faltando = []
    with httpx.Client(timeout=30) as cliente:
        for m in medicamentos:
            arquivo = destino / f"{m.generico}.json"
            if arquivo.exists():
                continue
            consulta = quote(f'openfda.generic_name:"{m.generico}" AND _exists_:drug_interactions')
            resposta = cliente.get(f"{URL_API}?search={consulta}&limit=50")
            candidatas = resposta.json().get("results", []) if resposta.status_code == 200 else []
            bula = escolher_bula(m.generico, candidatas)
            if bula is None:
                faltando.append(m.generico)
                continue
            arquivo.write_text(json.dumps(resumir_bula(m.generico, bula), ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(0.3)
    return faltando


def carregar_bulas(origem: Path = DIR_BULAS) -> list[dict]:
    return [json.loads(a.read_text(encoding="utf-8")) for a in sorted(origem.glob("*.json"))]


def _agrupar(frases: list[str]) -> list[str]:
    blocos, atual = [], ""
    for frase in frases:
        if atual and len(atual) + len(frase) + 1 > MAX_CHARS_TRECHO:
            blocos.append(atual)
            atual = frase
        else:
            atual = f"{atual} {frase}".strip()
    if atual:
        blocos.append(atual)
    return blocos


def dividir_em_trechos(bula: dict) -> list[Trecho]:
    """Mantém as seções de interação inteiras; das demais, só as frases sobre mecanismo."""
    trechos = []
    for secao, texto in bula["secoes"].items():
        frases = [f.strip() for f in _REGEX_FRASE.split(texto) if f.strip()]
        if secao in SECOES_FILTRADAS:
            frases = [f for f in frases if _REGEX_RELEVANTE.search(f)]
        for i, bloco in enumerate(_agrupar(frases)):
            trechos.append(Trecho(f"{bula['generico']}:{secao}:{i}", bula["generico"], secao, bloco))
    return trechos

"""Lista fixa de medicamentos do recorte e resolução de nomes para a forma canônica."""
from __future__ import annotations

import csv
import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .config import ARQ_MEDICAMENTOS

SAIS = (
    "hydrochloride hcl besylate besilate succinate tartrate potassium sodium calcium magnesium "
    "maleate mesylate fumarate acetate sulfate phosphate medoxomil cilexetil etexilate tosylate "
    "dihydrate monohydrate hemihydrate trihydrate anhydrous acid"
).split()
_REGEX_SAIS = re.compile(r"\b(" + "|".join(SAIS) + r")\b")

SINONIMOS_FIXOS = {
    "rifampicin": "rifampin",
    "cyclosporin": "cyclosporine",
    "ciclosporin": "cyclosporine",
    "cyclosporin a": "cyclosporine",
    "coumadin": "warfarin",
}

TRANSPORTADORES = {
    "p-gp": "P-gp",
    "pgp": "P-gp",
    "p gp": "P-gp",
    "p-glycoprotein": "P-gp",
    "p glycoprotein": "P-gp",
    "abcb1": "P-gp",
    "bcrp": "BCRP",
    "abcg2": "BCRP",
    "oatp1b1": "OATP1B1",
    "oatp1b3": "OATP1B3",
    "breast cancer resistance protein": "BCRP",
    "angiotensin-converting enzyme": "ACE",
    "citp3a4": "CYP3A4",
    "beta 2 -adrenoreceptor": "beta-2 adrenoceptor",
    "beta 2-adrenoceptor": "beta-2 adrenoceptor",
}

# Termos genéricos demais ligariam medicamentos sem nenhum mecanismo em comum.
ENZIMAS_GENERICAS = {"cyp450", "cytochrome p-450", "cytochrome p450", "p450"}

EFEITOS_EQUIVALENTES = {
    "qt interval prolongation": "qt prolongation",
    "bleeding events": "bleeding",
    "angioneurotic edema": "angioedema",
    "hypersensitivity reactions": "hypersensitivity",
}

_REGEX_CLASSE = re.compile(r"(inhibitors?|inducers?|blockers?|agonists?|antagonists?|substrates?|agents?|drugs?)$")
_REGEX_CYP = re.compile(r"\bcyp(?:450)?[\s-]*(\d{1,2})\s*([a-z])\s*(\d{0,2})\b", re.I)


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos e com espaços simples."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


@dataclass(frozen=True)
class Medicamento:
    generico: str
    nome_pt: str
    classe: str


def carregar_medicamentos(arquivo: Path = ARQ_MEDICAMENTOS) -> list[Medicamento]:
    with arquivo.open(encoding="utf-8", newline="") as f:
        return [Medicamento(l["generico"], l["nome_pt"], l["classe"]) for l in csv.DictReader(f)]


def nome_enzima(texto: str) -> str | None:
    """Devolve a forma canônica de uma enzima ou transportador, ou None se não reconhecer."""
    base = normalizar(texto)
    if base in TRANSPORTADORES:
        return TRANSPORTADORES[base]
    achado = _REGEX_CYP.search(base)
    if achado:
        canonico = f"CYP{achado.group(1)}{achado.group(2).upper()}{achado.group(3)}"
        # As bulas alternam CYP3A e CYP3A4. Unificar mantém conectados os mecanismos; o texto original fica na evidência.
        return "CYP3A4" if canonico == "CYP3A" else canonico
    return None


class Resolvedor:
    """Unifica marca, sal, português e pequenas variações de grafia."""

    def __init__(self, medicamentos: list[Medicamento], marcas: dict[str, str] | None = None) -> None:
        self.genericos = {m.generico for m in medicamentos}
        self.apelidos: dict[str, str] = dict(SINONIMOS_FIXOS)
        for m in medicamentos:
            self.apelidos[normalizar(m.generico)] = m.generico
            self.apelidos[normalizar(m.nome_pt)] = m.generico
        for marca, generico in (marcas or {}).items():
            self.apelidos.setdefault(normalizar(marca), generico)
        self.unificacoes: dict[str, str] = {}

    def medicamento(self, nome: str) -> str:
        base = normalizar(nome)
        if base in self.apelidos:
            canonico = self.apelidos[base]
        else:
            sem_sal = re.sub(r"\s+", " ", _REGEX_SAIS.sub(" ", base)).strip()
            if sem_sal in self.apelidos:
                canonico = self.apelidos[sem_sal]
            else:
                parecido = difflib.get_close_matches(sem_sal, list(self.apelidos), n=1, cutoff=0.93)
                canonico = self.apelidos[parecido[0]] if parecido else sem_sal
        if canonico != base:
            self.unificacoes[nome] = canonico
        return canonico

    def no_recorte(self, canonico: str) -> bool:
        return canonico in self.genericos


def nome_efeito(texto: str) -> str:
    base = normalizar(texto).strip(" .,;")
    return EFEITOS_EQUIVALENTES.get(base, base)


def entidade_invalida(rotulo: str, nome: str, resolvedor: Resolvedor) -> bool:
    """Recusa o que a resolução mostra estar errado: classe como medicamento ou medicamento como enzima."""
    base = normalizar(nome)
    if rotulo == "Medicamento":
        return bool(_REGEX_CLASSE.search(base)) and base not in resolvedor.apelidos
    if rotulo == "Enzima":
        return base in ENZIMAS_GENERICAS or resolvedor.medicamento(nome) in resolvedor.genericos
    return False

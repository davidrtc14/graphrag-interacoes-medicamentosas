"""Configuração lida do ambiente e do arquivo .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
DIR_DADOS = RAIZ / "data"
ARQ_MEDICAMENTOS = DIR_DADOS / "medicamentos.csv"
DIR_BULAS = DIR_DADOS / "bulas"
DIR_EXTRACOES = DIR_DADOS / "extracoes"
ARQ_GRAFO = DIR_DADOS / "grafo" / "grafo.json.gz"

MODELO_EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSOES_EMBEDDING = 384


@dataclass(frozen=True)
class Config:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    provedores: tuple[str, ...]
    gemini_key: str
    gemini_model: str
    groq_key: str
    groq_model: str
    ollama_url: str
    ollama_model: str


def carregar_config() -> Config:
    load_dotenv(RAIZ / ".env")
    env = os.environ.get
    return Config(
        neo4j_uri=env("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=env("NEO4J_USER", "neo4j"),
        neo4j_password=env("NEO4J_PASSWORD", ""),
        provedores=tuple(p.strip() for p in env("LLM_PROVIDERS", "gemini,groq").split(",") if p.strip()),
        gemini_key=env("GEMINI_API_KEY", ""),
        gemini_model=env("GEMINI_MODEL", "gemini-3.5-flash,gemini-3-flash-preview,gemini-3.1-flash-lite"),
        groq_key=env("GROQ_API_KEY", ""),
        groq_model=env("GROQ_MODEL", "openai/gpt-oss-120b"),
        ollama_url=env("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=env("OLLAMA_MODEL", "qwen3:8b"),
    )

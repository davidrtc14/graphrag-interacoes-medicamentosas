"""Embeddings multilíngues leves, executados localmente em ONNX, sem PyTorch."""
from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding

from .config import MODELO_EMBEDDING


@lru_cache(maxsize=1)
def _modelo() -> TextEmbedding:
    return TextEmbedding(model_name=MODELO_EMBEDDING)


def embutir(textos: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _modelo().embed(textos)]

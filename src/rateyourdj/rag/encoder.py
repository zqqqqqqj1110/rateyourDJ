"""Text encoders. ``SentenceTransformerEncoder`` wraps BAAI/bge-m3 (dense vectors);
``HashingEncoder`` is a dependency-free stand-in for offline tests."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

DEFAULT_MODEL = "BAAI/bge-m3"


class Encoder(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray: ...


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class SentenceTransformerEncoder:
    def __init__(self, model_name: str = DEFAULT_MODEL, *, device: str | None = None,
                 max_seq_length: int = 256) -> None:
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as error:  # pragma: no cover - optional dependency
            raise RuntimeError('install the RAG extras first:  pip install -e ".[rag]"') from error
        if device is None:
            device = "mps" if torch.backends.mps.is_available() else (
                "cuda" if torch.cuda.is_available() else "cpu")
        self.model = SentenceTransformer(model_name, device=device)
        self.model.max_seq_length = max_seq_length
        self.name = model_name
        self.device = device
        getter = getattr(self.model, "get_embedding_dimension", None) or \
            self.model.get_sentence_embedding_dimension
        self.dim = int(getter())

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        vectors = self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=len(texts) > 256)
        return vectors.astype(np.float32)


class HashingEncoder:
    """Bag-of-words hashed into ``dim`` buckets; similar wording -> similar vectors."""

    def __init__(self, dim: int = 512) -> None:
        self.name = f"hashing-{dim}"
        self.dim = dim

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in re.findall(r"\w+", text.lower()):
                bucket = int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % self.dim
                out[row, bucket] += 1.0
        return _normalize(out)

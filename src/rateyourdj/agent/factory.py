"""Build the production RecommenderV2: bge-m3 + index loaded once, lazily."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rateyourdj.rag.retrieval import Retriever

from .llm import OpenAICompatibleChat
from .service import RecommenderV2


def vector_similarity(retriever: Retriever):
    if retriever.matrix is None:
        return None

    def sim(a: str, b: str) -> float:
        ra, rb = retriever.row.get(a), retriever.row.get(b)
        return 0.0 if ra is None or rb is None else float(retriever.matrix[ra] @ retriever.matrix[rb])
    return sim


def build_recommender(*, catalog_root: str | Path = "data/catalog", users_root: str | Path = "data/users",
                      index_root: str | Path = "data/index", model: str | None = None,
                      use_llm: bool = True, encoder: Any = None) -> RecommenderV2:
    import json

    from rateyourdj.rag.index import load_index

    manifest = Path(catalog_root) / "manifest.json"
    catalog_version = json.loads(manifest.read_text("utf-8"))["catalog_version"] if manifest.is_file() else "unknown"
    state: dict[str, Any] = {"index": None, "encoder": encoder, "loaded": False}

    def retriever_factory(songs, context):
        if not state["loaded"]:
            state["index"] = load_index(index_root)
            if state["index"] is not None and state["encoder"] is None:
                from rateyourdj.rag.encoder import SentenceTransformerEncoder
                state["encoder"] = SentenceTransformerEncoder(model or state["index"].manifest["model"])
            state["loaded"] = True
        return Retriever(songs, context, index=state["index"],
                         encoder=state["encoder"] if state["index"] is not None else None,
                         catalog_version=catalog_version)

    return RecommenderV2(catalog_root=catalog_root, users_root=users_root,
                         retriever_factory=retriever_factory,
                         llm=OpenAICompatibleChat.from_env() if use_llm else None,
                         similarity_factory=vector_similarity)

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


def conversation_llm_from_env() -> OpenAICompatibleChat | None:
    """LLM for the conversation layer: CONVERSE_LLM_* if set, else DeepSeek; None without a key.

    Kept separate from AGENT_LLM_* so the selection step can point at the fine-tuned
    model while the conversation layer stays on a general model.
    """
    import os

    key = os.getenv("CONVERSE_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None
    return OpenAICompatibleChat(key, model=os.getenv("CONVERSE_LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat",
                                base_url=os.getenv("CONVERSE_LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL")
                                or "https://api.deepseek.com", temperature=0.3)


def playback_resolver_from_env(catalog_root: str | Path) -> Any | None:
    """On-demand playback lookup for songs about to be shown (cached in playback_cache.json).

    Spotify title+artist search for head/mid songs, YouTube (quota-limited) for tail
    songs. The slow MusicBrainz ISRC lookup is skipped here to keep responses fast.
    Returns None when neither Spotify nor YouTube credentials are configured.
    """
    import os

    from rateyourdj.data_pipeline.playback import PlaybackResolver, YouTubeResolver, spotify_search_from_env

    spotify = spotify_search_from_env()
    youtube = YouTubeResolver(Path(catalog_root) / "raw") if os.getenv("YOUTUBE_API_KEY") else None
    if spotify is None and youtube is None:
        return None
    return PlaybackResolver(Path(catalog_root) / "playback_cache.json", youtube=youtube, spotify_search=spotify)


def build_recommender(*, catalog_root: str | Path = "data/catalog", users_root: str | Path = "data/users",
                      index_root: str | Path = "data/index", model: str | None = None,
                      use_llm: bool = True, encoder: Any = None, phase: str = "dev",
                      playback_lookup: bool = True) -> RecommenderV2:
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
                         similarity_factory=vector_similarity, feedback_phase=phase,
                         playback=playback_resolver_from_env(catalog_root) if playback_lookup else None)

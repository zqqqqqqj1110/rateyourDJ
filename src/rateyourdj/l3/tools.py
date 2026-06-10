from __future__ import annotations

from pathlib import Path

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore

from .models import RetrievalResult
from .service import CandidateRetrievalService


def retrieve_candidates(
    user_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    top_k: int = 20,
    max_per_artist: int = 2,
    min_score: float = 0.0,
) -> RetrievalResult:
    service = CandidateRetrievalService(
        JsonProfileStore(profile_dir),
        JsonSongStore(song_dir),
    )
    return service.retrieve(
        user_id,
        top_k=top_k,
        max_per_artist=max_per_artist,
        min_score=min_score,
    )

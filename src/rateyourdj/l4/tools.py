from __future__ import annotations

from pathlib import Path

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore

from .models import RankingResult
from .service import RecommendationRankingService


def rank_candidates(
    user_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    top_k: int = 20,
    candidate_pool_size: int | None = None,
    max_per_artist: int = 2,
    min_retrieval_score: float = 0.0,
) -> RankingResult:
    service = RecommendationRankingService(
        JsonProfileStore(profile_dir),
        JsonSongStore(song_dir),
    )
    return service.rank(
        user_id,
        top_k=top_k,
        candidate_pool_size=candidate_pool_size,
        max_per_artist=max_per_artist,
        min_retrieval_score=min_retrieval_score,
    )

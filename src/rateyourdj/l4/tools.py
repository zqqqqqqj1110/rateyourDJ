from __future__ import annotations

from pathlib import Path

from rateyourdj.agent_tools import ToolObservation
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


def rank_candidates_tool(
    user_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    top_k: int = 20,
    candidate_pool_size: int | None = None,
    max_per_artist: int = 2,
    min_retrieval_score: float = 0.0,
) -> ToolObservation:
    result = rank_candidates(
        user_id,
        profile_dir=profile_dir,
        song_dir=song_dir,
        top_k=top_k,
        candidate_pool_size=candidate_pool_size,
        max_per_artist=max_per_artist,
        min_retrieval_score=min_retrieval_score,
    )
    diagnostics: list[str] = []
    actions: list[dict[str, object]] = []
    if result.missing_seed_song_ids:
        diagnostics.append(
            f"{len(result.missing_seed_song_ids)} collection seeds lack L2 profiles"
        )
    if result.missing_candidate_song_ids:
        diagnostics.append(
            f"{len(result.missing_candidate_song_ids)} L3 candidates lack L2 profiles"
        )
    if len(result.ranked_songs) < top_k:
        diagnostics.append(
            f"requested {top_k} songs but ranked {len(result.ranked_songs)}"
        )
        pool_size = candidate_pool_size or max(top_k * 5, top_k)
        actions.append(
            {
                "tool": "L4.rank_candidates",
                "arguments": {
                    "candidate_pool_size": min(max(pool_size * 2, top_k), 1000)
                },
                "reason": "expand the L3 candidate pool",
            }
        )
        if min_retrieval_score > 0:
            actions.append(
                {
                    "tool": "L4.rank_candidates",
                    "arguments": {
                        "min_retrieval_score": max(
                            0.0,
                            min_retrieval_score / 2,
                        )
                    },
                    "reason": "relax the retrieval threshold",
                }
            )
    status = (
        "empty"
        if not result.ranked_songs
        else "partial"
        if diagnostics
        else "ok"
    )
    return ToolObservation(
        tool="L4.rank_candidates",
        status=status,
        data=result.to_dict(),
        diagnostics=diagnostics,
        retryable=status != "ok",
        suggested_actions=actions,
    )

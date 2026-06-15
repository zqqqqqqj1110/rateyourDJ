from __future__ import annotations

from pathlib import Path

from rateyourdj.agent_tools import ToolObservation
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


def retrieve_candidates_tool(
    user_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    top_k: int = 20,
    max_per_artist: int = 2,
    min_score: float = 0.0,
) -> ToolObservation:
    result = retrieve_candidates(
        user_id,
        profile_dir=profile_dir,
        song_dir=song_dir,
        top_k=top_k,
        max_per_artist=max_per_artist,
        min_score=min_score,
    )
    diagnostics: list[str] = []
    actions: list[dict[str, object]] = []
    if result.missing_seed_song_ids:
        diagnostics.append(
            f"{len(result.missing_seed_song_ids)} collection seeds lack L2 profiles"
        )
        actions.append(
            {
                "tool": "L2.collect_or_import",
                "song_ids": list(result.missing_seed_song_ids),
                "reason": "restore missing collection seed profiles",
            }
        )
    if len(result.candidates) < top_k:
        diagnostics.append(
            f"requested {top_k} candidates but retrieved {len(result.candidates)}"
        )
        if min_score > 0:
            actions.append(
                {
                    "tool": "L3.retrieve_candidates",
                    "arguments": {"min_score": max(0.0, min_score / 2)},
                    "reason": "relax the retrieval threshold",
                }
            )
        if max_per_artist < 10:
            actions.append(
                {
                    "tool": "L3.retrieve_candidates",
                    "arguments": {"max_per_artist": max_per_artist + 1},
                    "reason": "allow more candidates from the same artist",
                }
            )
    status = (
        "empty"
        if not result.candidates
        else "partial"
        if diagnostics
        else "ok"
    )
    return ToolObservation(
        tool="L3.retrieve_candidates",
        status=status,
        data=result.to_dict(),
        diagnostics=diagnostics,
        retryable=status != "ok",
        suggested_actions=actions,
    )

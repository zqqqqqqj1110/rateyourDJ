from __future__ import annotations

from collections.abc import Mapping

from rateyourdj.l1 import UserProfile
from rateyourdj.l2 import SongProfile
from rateyourdj.l3 import RetrievalCandidate, weighted_jaccard

from .models import (
    BASE_SCORE_WEIGHTS,
    DIVERSITY_SIMILARITY_WEIGHTS,
    FEEDBACK_ADJUSTMENT_WEIGHT,
)


def _normalized_text(value: str | None) -> str:
    return " ".join((value or "").strip().casefold().split())


def _normalized_scores(values: Mapping[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for label, score in values.items():
        key = _normalized_text(label)
        if key:
            normalized[key] = max(normalized.get(key, 0.0), float(score))
    return normalized


def candidate_tags(song: SongProfile) -> dict[str, float]:
    tags: dict[str, float] = {}
    for source_tags in song.source_tags.values():
        for label, score in _normalized_scores(source_tags).items():
            tags[label] = max(tags.get(label, 0.0), score)
    return tags


def artist_preference_score(
    preferences: Mapping[str, float], artist: str | None
) -> float:
    artist_key = _normalized_text(artist)
    if not artist_key:
        return 0.0
    return _normalized_scores(preferences).get(artist_key, 0.0)


def quality_score(song: SongProfile) -> float:
    return float(song.confidence_score or 0.0)


def score_candidate(
    profile: UserProfile,
    song: SongProfile,
    candidate: RetrievalCandidate,
    *,
    feedback_score: float = 0.0,
) -> tuple[float, dict[str, float], dict[str, float]]:
    raw_scores = {
        "retrieval": candidate.similarity_score,
        "artist_preference": artist_preference_score(
            profile.artist_preferences,
            song.metadata["artist"],
        ),
        "genre_preference": weighted_jaccard(
            profile.genre_preferences,
            song.genres,
        ),
        "tag_preference": weighted_jaccard(
            profile.tag_preferences,
            candidate_tags(song),
        ),
        "quality": quality_score(song),
        "feedback": max(-1.0, min(float(feedback_score), 1.0)),
    }
    breakdown = {
        name: round(raw_scores[name] * weight, 6)
        for name, weight in BASE_SCORE_WEIGHTS.items()
    }
    breakdown["feedback_adjustment"] = round(
        raw_scores["feedback"] * FEEDBACK_ADJUSTMENT_WEIGHT,
        6,
    )
    return round(sum(breakdown.values()), 6), breakdown, raw_scores


def diversity_similarity(left: SongProfile, right: SongProfile) -> float:
    artist_match = float(
        bool(_normalized_text(left.metadata["artist"]))
        and _normalized_text(left.metadata["artist"])
        == _normalized_text(right.metadata["artist"])
    )
    raw_scores = {
        "artist": artist_match,
        "genres": weighted_jaccard(left.genres, right.genres),
        "tags": weighted_jaccard(
            candidate_tags(left),
            candidate_tags(right),
        ),
    }
    return sum(
        raw_scores[name] * weight
        for name, weight in DIVERSITY_SIMILARITY_WEIGHTS.items()
    )


def ranking_reasons(
    raw_scores: Mapping[str, float],
    diversity_penalty: float,
) -> list[str]:
    reasons: list[str] = []
    if raw_scores["retrieval"] >= 0.5:
        reasons.append("strong similarity to the collection seeds")
    elif raw_scores["retrieval"] > 0:
        reasons.append("retrieved from collection-level song similarity")

    if raw_scores.get("feedback", 0.0) >= 0.25:
        reasons.append("promoted by positive feedback")
    elif raw_scores.get("feedback", 0.0) <= -0.25:
        reasons.append("penalized by negative feedback")

    preference_scores = (
        ("artist_preference", "matches a preferred artist"),
        ("genre_preference", "matches the collection genre profile"),
        ("tag_preference", "matches the collection tag profile"),
    )
    for name, reason in sorted(
        preference_scores,
        key=lambda item: (-raw_scores[item[0]], item[0]),
    ):
        if raw_scores[name] >= 0.1:
            reasons.append(reason)

    if raw_scores["quality"] >= 0.8:
        reasons.append("high-confidence song profile")
    if diversity_penalty >= 0.05:
        reasons.append("penalized for similarity to higher-ranked songs")
    if not reasons:
        reasons.append("selected from the L3 candidate pool")
    return reasons[:4]

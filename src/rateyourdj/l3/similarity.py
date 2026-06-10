from __future__ import annotations

from collections.abc import Mapping

from rateyourdj.l2 import SongProfile

from .models import SCORE_WEIGHTS


def _normalized_scores(values: Mapping[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for label, score in values.items():
        key = label.strip().casefold()
        if key:
            normalized[key] = max(normalized.get(key, 0.0), float(score))
    return normalized


def weighted_jaccard(
    left: Mapping[str, float], right: Mapping[str, float]
) -> float:
    left_scores = _normalized_scores(left)
    right_scores = _normalized_scores(right)
    labels = set(left_scores) | set(right_scores)
    if not labels:
        return 0.0

    denominator = sum(
        max(left_scores.get(label, 0.0), right_scores.get(label, 0.0))
        for label in labels
    )
    if denominator == 0:
        return 0.0
    numerator = sum(
        min(left_scores.get(label, 0.0), right_scores.get(label, 0.0))
        for label in labels
    )
    return numerator / denominator


def release_year_similarity(
    left_year: int | None, right_year: int | None
) -> float:
    if left_year is None or right_year is None:
        return 0.0
    return max(0.0, 1.0 - min(abs(left_year - right_year), 30) / 30)


def score_song_pair(
    seed: SongProfile, candidate: SongProfile
) -> tuple[float, dict[str, float]]:
    raw_scores = {
        "track_tags": weighted_jaccard(
            seed.source_tags["lastfm_track_tags"],
            candidate.source_tags["lastfm_track_tags"],
        ),
        "genres": weighted_jaccard(seed.genres, candidate.genres),
        "artist_tags": weighted_jaccard(
            seed.source_tags["lastfm_artist_tags"],
            candidate.source_tags["lastfm_artist_tags"],
        ),
        "release_year": release_year_similarity(
            seed.metadata["release_year"],
            candidate.metadata["release_year"],
        ),
    }
    breakdown = {
        field_name: round(raw_scores[field_name] * weight, 6)
        for field_name, weight in SCORE_WEIGHTS.items()
    }
    return round(sum(breakdown.values()), 6), breakdown

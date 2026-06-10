from __future__ import annotations

import re
from collections import Counter

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore, SongProfile

from .models import (
    COLLECTION_SCORE_WEIGHTS,
    SCORE_WEIGHTS,
    TOP_SEED_COUNT,
    RetrievalCandidate,
    RetrievalResult,
)
from .similarity import score_song_pair


_VERSION_MARKERS = re.compile(
    r"\b(remaster(?:ed)?|live|mono|stereo|acoustic|demo|edit|mix|version)\b",
    re.IGNORECASE,
)


def _normalized_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _canonical_title(value: str | None) -> str:
    if not value:
        return ""

    title = re.sub(
        r"[\[(]([^\])]+)[\])]",
        lambda match: "" if _VERSION_MARKERS.search(match.group(1)) else match.group(0),
        value,
    )
    parts = re.split(r"\s+-\s+", title, maxsplit=1)
    if len(parts) == 2 and _VERSION_MARKERS.search(parts[1]):
        title = parts[0]
    return _normalized_text(title)


def _same_external_record(left: SongProfile, right: SongProfile) -> bool:
    return any(
        left.external_ids[field_name]
        and left.external_ids[field_name] == right.external_ids[field_name]
        for field_name in left.external_ids
    )


def _duplicate_version(left: SongProfile, right: SongProfile) -> bool:
    if _normalized_text(left.metadata["artist"]) != _normalized_text(
        right.metadata["artist"]
    ):
        return False
    if _canonical_title(left.metadata["title"]) != _canonical_title(
        right.metadata["title"]
    ):
        return False
    if not _canonical_title(left.metadata["title"]):
        return False

    left_duration = left.metadata["duration_ms"]
    right_duration = right.metadata["duration_ms"]
    return (
        left_duration is None
        or right_duration is None
        or abs(left_duration - right_duration) <= 10_000
    )


class CandidateRetrievalService:
    def __init__(
        self,
        profile_store: JsonProfileStore,
        song_store: JsonSongStore,
    ) -> None:
        self.profile_store = profile_store
        self.song_store = song_store

    def _load_seed_profiles(
        self, song_ids: list[str]
    ) -> tuple[list[SongProfile], list[str]]:
        seeds: list[SongProfile] = []
        missing: list[str] = []
        for song_id in song_ids:
            if self.song_store.exists(song_id):
                seeds.append(self.song_store.load(song_id))
            else:
                missing.append(song_id)
        return seeds, missing

    def _load_candidate_profiles(self) -> list[SongProfile]:
        return [
            self.song_store.load(path.stem)
            for path in sorted(self.song_store.root.glob("*.json"))
        ]

    def retrieve(
        self,
        user_id: str,
        *,
        top_k: int = 20,
        max_per_artist: int = 2,
        min_score: float = 0.0,
    ) -> RetrievalResult:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if max_per_artist < 1:
            raise ValueError("max_per_artist must be at least 1")
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")

        profile = self.profile_store.load(user_id)
        collection_ids = set(profile.collection_song_ids)
        seeds, missing = self._load_seed_profiles(profile.collection_song_ids)
        scored: list[tuple[RetrievalCandidate, SongProfile]] = []

        for candidate in self._load_candidate_profiles():
            if candidate.song_id in collection_ids:
                continue
            if any(
                _same_external_record(seed, candidate)
                or _duplicate_version(seed, candidate)
                for seed in seeds
            ):
                continue

            matches: list[str] = []
            seed_scores: list[tuple[str, float, dict[str, float]]] = []
            for seed in seeds:
                score, breakdown = score_song_pair(seed, candidate)
                if score > 0:
                    matches.append(seed.song_id)
                seed_scores.append((seed.song_id, score, breakdown))

            seed_scores.sort(key=lambda item: (-item[1], item[0]))
            if not seed_scores:
                continue
            best_seed_song_id, best_seed_score, _ = seed_scores[0]
            top_seed_scores = seed_scores[:TOP_SEED_COUNT]
            top_seed_average_score = sum(
                item[1] for item in top_seed_scores
            ) / len(top_seed_scores)
            collection_score = (
                COLLECTION_SCORE_WEIGHTS["best_seed"] * best_seed_score
                + COLLECTION_SCORE_WEIGHTS["top_seed_average"]
                * top_seed_average_score
            )
            collection_breakdown = {
                field_name: round(
                    COLLECTION_SCORE_WEIGHTS["best_seed"]
                    * top_seed_scores[0][2][field_name]
                    + COLLECTION_SCORE_WEIGHTS["top_seed_average"]
                    * (
                        sum(item[2][field_name] for item in top_seed_scores)
                        / len(top_seed_scores)
                    ),
                    6,
                )
                for field_name in SCORE_WEIGHTS
            }
            collection_score = round(sum(collection_breakdown.values()), 6)

            if collection_score < min_score or collection_score == 0:
                continue
            scored.append(
                (
                    RetrievalCandidate(
                        candidate_song_id=candidate.song_id,
                        best_seed_song_id=best_seed_song_id,
                        matched_seed_song_ids=matches,
                        best_seed_score=round(best_seed_score, 6),
                        top_seed_average_score=round(
                            top_seed_average_score, 6
                        ),
                        similarity_score=collection_score,
                        score_breakdown=collection_breakdown,
                    ),
                    candidate,
                )
            )

        scored.sort(
            key=lambda item: (
                -item[0].similarity_score,
                item[0].candidate_song_id,
            )
        )
        selected: list[RetrievalCandidate] = []
        selected_profiles: list[SongProfile] = []
        artist_counts: Counter[str] = Counter()
        for result, candidate in scored:
            if any(
                _same_external_record(existing, candidate)
                or _duplicate_version(existing, candidate)
                for existing in selected_profiles
            ):
                continue

            artist_key = _normalized_text(candidate.metadata["artist"])
            if artist_key and artist_counts[artist_key] >= max_per_artist:
                continue
            selected.append(result)
            selected_profiles.append(candidate)
            if artist_key:
                artist_counts[artist_key] += 1
            if len(selected) == top_k:
                break

        return RetrievalResult(
            user_id=user_id,
            seed_song_ids=[seed.song_id for seed in seeds],
            missing_seed_song_ids=missing,
            candidates=selected,
        )

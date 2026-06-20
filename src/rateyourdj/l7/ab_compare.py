"""Offline A/B comparison of two agent configurations.

We cannot run a live A/B test (no production traffic), so this runs an *offline*
controlled comparison: the same batch of queries is executed under two agent
configurations (variants A and B) in isolated temp stores, and their agent
metrics are aggregated side by side. This supports questions the project roadmap
raises:

* rules-only vs model(ReAct)-driven recommendation
* feedback-updated profile vs cold profile

Each variant is described by an :class:`ABVariant` (a label plus knobs for
agent_mode and whether to attach a model/provider). Metrics reuse the existing
trajectory-quality aggregation (grounding rate, hallucination rate, thought
coverage) plus latency and tool-call success.

Honesty note: an SFT/GRPO "before vs after fine-tuning" arm is intentionally NOT
implemented here — that requires a trained checkpoint, which does not exist yet.
The framework is structured so such an arm can be added once a model is trained.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l4 import RecommendationRankingService
from rateyourdj.l6 import (
    AgentResponse,
    AgentToolRegistryV1,
    JsonSessionStore,
    JsonTrajectoryStore,
    RecommendationAgentService,
)

from .models import ABComparisonReport, ABVariantMetrics
from .trajectory_quality import compute_trajectory_quality


@dataclass(slots=True)
class ABVariant:
    """One arm of the comparison.

    ``service_factory`` builds a configured :class:`RecommendationAgentService`
    given the per-run stores. ``seed_feedback`` optionally pre-populates the
    profile so a "with feedback history" arm can be compared against a cold one.
    """

    label: str
    agent_mode: str = "rules"
    seed_profile: bool = True
    llm_provider_factory: Callable[[], Any] | None = None
    music_provider_factory: Callable[[], Any] | None = None
    track_generator_factory: Callable[[], Any] | None = None


def run_ab_comparison(
    queries: list[str],
    variant_a: ABVariant,
    variant_b: ABVariant,
    *,
    user_id: str = "ab-user",
    comparison: str | None = None,
) -> ABComparisonReport:
    if not queries:
        raise ValueError("A/B comparison requires at least one query")
    metrics_a = _run_variant(variant_a, queries, user_id)
    metrics_b = _run_variant(variant_b, queries, user_id)
    deltas = _compute_deltas(metrics_a, metrics_b)
    notes = [
        "Offline controlled comparison on a shared query batch (no live "
        "traffic).",
        "An SFT/GRPO fine-tuned arm is not included; it requires a trained "
        "model checkpoint that does not exist yet.",
    ]
    return ABComparisonReport(
        comparison=comparison or f"{variant_a.label} vs {variant_b.label}",
        query_count=len(queries),
        variant_a=metrics_a,
        variant_b=metrics_b,
        deltas=deltas,
        notes=notes,
    )


def _run_variant(
    variant: ABVariant,
    queries: list[str],
    user_id: str,
) -> ABVariantMetrics:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        profile_store = JsonProfileStore(root / "profiles")
        song_store = JsonSongStore(root / "songs")
        trajectory_store = JsonTrajectoryStore(root / "trajectories")
        session_store = JsonSessionStore(root / "sessions")
        _seed_song_store(song_store)
        if variant.seed_profile:
            _seed_profile_store(profile_store, user_id)

        music_provider = (
            variant.music_provider_factory()
            if variant.music_provider_factory is not None
            else None
        )
        track_generator = (
            variant.track_generator_factory()
            if variant.track_generator_factory is not None
            else None
        )
        llm_provider = (
            variant.llm_provider_factory()
            if variant.llm_provider_factory is not None
            else None
        )
        registry = AgentToolRegistryV1.default(
            profile_store,
            song_store,
            music_provider=music_provider,
            track_generator=track_generator,
            session_store=session_store,
        )
        service = RecommendationAgentService(
            RecommendationRankingService(profile_store, song_store),
            song_store,
            trajectory_store,
            session_store=session_store,
            model_tool_registry=registry,
            llm_provider=llm_provider,
            agent_mode=variant.agent_mode,
        )
        responses: list[AgentResponse] = []
        for index, query in enumerate(queries):
            responses.append(
                service.recommend(
                    user_id,
                    query,
                    session_id=f"ab-{variant.label}-{index}",
                )
            )
        return _aggregate_variant(variant.label, responses)


def _aggregate_variant(
    label: str,
    responses: list[AgentResponse],
) -> ABVariantMetrics:
    count = len(responses)
    records = [response.to_dict() for response in responses]
    quality = compute_trajectory_quality(records)

    rec_counts = [len(response.ranked_songs) for response in responses]
    non_empty = sum(1 for response in responses if response.ranked_songs)
    latencies = [
        float(response.latency_ms)
        for response in responses
        if isinstance(response.latency_ms, (int, float))
        and not isinstance(response.latency_ms, bool)
    ]
    fallbacks = sum(1 for response in responses if response.fallback_reason)

    tool_statuses = [
        str(call.get("observation", {}).get("status", "unknown"))
        for response in responses
        for call in response.tool_calls
    ]
    tool_success = sum(
        status in {"ok", "partial"} for status in tool_statuses
    )

    return ABVariantMetrics(
        label=label,
        query_count=count,
        average_recommendations=_average(rec_counts),
        non_empty_rate=_ratio(non_empty, count),
        tool_call_success_rate=_ratio(tool_success, len(tool_statuses)),
        grounding_rate=quality.grounding_rate,
        average_hallucination_rate=quality.average_hallucination_rate,
        thought_coverage_rate=quality.thought_coverage_rate,
        average_latency_ms=_average(latencies),
        p95_latency_ms=_percentile(latencies, 95),
        fallback_rate=_ratio(fallbacks, count),
    )


def _compute_deltas(
    a: ABVariantMetrics,
    b: ABVariantMetrics,
) -> dict[str, float]:
    """B minus A for each numeric metric (positive => B scores higher)."""
    fields = (
        "average_recommendations",
        "non_empty_rate",
        "tool_call_success_rate",
        "grounding_rate",
        "average_hallucination_rate",
        "thought_coverage_rate",
        "average_latency_ms",
        "p95_latency_ms",
        "fallback_rate",
    )
    return {
        name: round(getattr(b, name) - getattr(a, name), 6) for name in fields
    }


# --------------------------------------------------------------------------- #
# Seed helpers (mirror the eval suite's isolated environment)
# --------------------------------------------------------------------------- #
def _seed_song_store(song_store: JsonSongStore) -> None:
    specs = [
        ("seed-rock", "Seed Rock", "Seed Artist", {"rock": 1.0}),
        ("rock-a", "Rock A", "Artist A", {"rock": 1.0}),
        ("rock-b", "Rock B", "Artist B", {"rock": 1.0}),
        ("british-rock", "British Rock", "British Artist", {"rock": 1.0}),
    ]
    for song_id, title, artist, genres in specs:
        song = SongProfile.empty(song_id)
        song.metadata.update(
            {
                "title": title,
                "artist": artist,
                "album": "Album",
                "release_year": 2000,
                "duration_ms": 200_000,
                "version_type": "original",
            }
        )
        song.genres = dict(genres)
        song.source_tags["lastfm_track_tags"] = dict(genres)
        song.confidence_score = 1.0
        song_store.save(song)


def _seed_profile_store(
    profile_store: JsonProfileStore,
    user_id: str,
) -> None:
    profile_store.save(
        UserProfile(
            user_id=user_id,
            collection_song_ids=["seed-rock"],
            genre_preferences={"rock": 1.0},
            tag_preferences={"rock": 1.0, "british": 0.8},
        )
    )


# --------------------------------------------------------------------------- #
# small math utils
# --------------------------------------------------------------------------- #
def _average(values: list[float | int]) -> float:
    items = [float(value) for value in values]
    if not items:
        return 0.0
    return round(sum(items) / len(items), 6)


def _percentile(values: list[float | int], percentile: float) -> float:
    items = sorted(float(value) for value in values)
    if not items:
        return 0.0
    if len(items) == 1:
        return round(items[0], 6)
    import math

    rank = max(1, math.ceil(percentile / 100 * len(items)))
    return round(items[min(rank, len(items)) - 1], 6)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)

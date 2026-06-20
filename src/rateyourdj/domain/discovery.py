"""Generative track discovery with external grounding.

This is the heart of the LLM-as-DJ design. Instead of scanning a fixed local
candidate library, an LLM proposes candidate songs from the user's taste
profile and the current request. Those proposals are *grounded* against an
external music provider (Spotify search): proposals that cannot be confirmed to
exist are dropped as hallucinations. Confirmed tracks are optionally cached to
the local song store so future runs can skip the lookup.

Flow:

    user taste + intent
        -> TrackGenerator proposes candidates (artist + title + reason)
        -> ExternalMusicProvider.get_track_metadata confirms each one
             - confirmed  -> kept, enriched with real ids / preview / album
             - not found  -> dropped (hallucination)
        -> DiscoveryResult(grounded tracks, generated/grounded/dropped counts)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from rateyourdj.providers import (
    ExternalMusicProvider,
    ProviderError,
    ProviderTrack,
    TrackQuery,
)


@dataclass(frozen=True, slots=True)
class GeneratedCandidate:
    """A single song proposed by the generator before grounding."""

    title: str
    artist: str
    reason: str = ""
    album: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DiscoveredTrack:
    """A generated candidate that was confirmed to exist by a provider."""

    title: str
    artist: str
    discovery_reason: str
    track: ProviderTrack

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "discovery_reason": self.discovery_reason,
            "track": self.track.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Outcome of one discovery run, including grounding statistics."""

    intent: str
    tracks: list[DiscoveredTrack]
    generated: int
    grounded: int
    dropped: int
    dropped_candidates: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    @property
    def hallucination_rate(self) -> float:
        if self.generated <= 0:
            return 0.0
        return round(self.dropped / self.generated, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "tracks": [track.to_dict() for track in self.tracks],
            "generated": self.generated,
            "grounded": self.grounded,
            "dropped": self.dropped,
            "hallucination_rate": self.hallucination_rate,
            "dropped_candidates": [dict(item) for item in self.dropped_candidates],
            "diagnostics": list(self.diagnostics),
        }


class TrackGenerator(Protocol):
    """Proposes candidate songs from user taste and the current request."""

    @property
    def name(self) -> str:
        ...

    def generate(
        self,
        *,
        intent: str,
        user_taste: dict[str, Any],
        count: int,
        exclude_artists: list[str],
    ) -> list[GeneratedCandidate]:
        ...


class DiscoveryService:
    """Generate candidate songs, then ground them against a music provider."""

    def __init__(
        self,
        generator: TrackGenerator,
        music_provider: ExternalMusicProvider,
        *,
        overgenerate_factor: int = 2,
        max_generate: int = 40,
    ) -> None:
        if overgenerate_factor < 1:
            raise ValueError("overgenerate_factor must be >= 1")
        if max_generate < 1:
            raise ValueError("max_generate must be >= 1")
        self.generator = generator
        self.music_provider = music_provider
        self.overgenerate_factor = overgenerate_factor
        self.max_generate = max_generate

    def discover(
        self,
        *,
        intent: str,
        user_taste: dict[str, Any] | None = None,
        count: int = 10,
        exclude_artists: list[str] | None = None,
    ) -> DiscoveryResult:
        if count < 1:
            raise ValueError("count must be >= 1")
        taste = user_taste or {}
        excluded = _normalized_set(exclude_artists or [])
        # Overgenerate so that after dropping hallucinations and excluded
        # artists we still have a good chance of reaching `count`.
        request_count = min(count * self.overgenerate_factor, self.max_generate)

        diagnostics: list[str] = []
        try:
            candidates = self.generator.generate(
                intent=intent,
                user_taste=taste,
                count=request_count,
                exclude_artists=sorted(excluded),
            )
        except Exception as error:  # noqa: BLE001 - generator failures are reported, not raised
            return DiscoveryResult(
                intent=intent,
                tracks=[],
                generated=0,
                grounded=0,
                dropped=0,
                diagnostics=[f"generator {self.generator.name} failed: {error}"],
            )

        candidates = _dedupe_candidates(candidates)
        generated = len(candidates)
        if generated == 0:
            diagnostics.append(
                f"generator {self.generator.name} returned no candidates"
            )

        grounded_tracks: list[DiscoveredTrack] = []
        dropped_candidates: list[dict[str, Any]] = []
        seen_track_ids: set[str] = set()

        for candidate in candidates:
            artist_key = _normalize(candidate.artist)
            if artist_key and artist_key in excluded:
                dropped_candidates.append(
                    {**candidate.to_dict(), "drop_reason": "excluded_artist"}
                )
                continue
            try:
                track = self.music_provider.get_track_metadata(
                    TrackQuery(
                        title=candidate.title,
                        artist=candidate.artist,
                        album=candidate.album,
                    )
                )
            except (ProviderError, ValueError, LookupError) as error:
                # Could not confirm the song exists -> treat as a hallucination.
                dropped_candidates.append(
                    {
                        **candidate.to_dict(),
                        "drop_reason": "not_found",
                        "detail": str(error),
                    }
                )
                continue
            except Exception as error:  # noqa: BLE001 - unexpected provider failure
                dropped_candidates.append(
                    {
                        **candidate.to_dict(),
                        "drop_reason": "provider_error",
                        "detail": str(error),
                    }
                )
                continue

            track_id = (track.track_id or "").strip()
            dedupe_key = track_id or f"{_normalize(track.artist)}::{_normalize(track.title)}"
            if dedupe_key in seen_track_ids:
                dropped_candidates.append(
                    {**candidate.to_dict(), "drop_reason": "duplicate"}
                )
                continue
            seen_track_ids.add(dedupe_key)

            grounded_tracks.append(
                DiscoveredTrack(
                    title=track.title or candidate.title,
                    artist=track.artist or candidate.artist,
                    discovery_reason=candidate.reason,
                    track=track,
                )
            )
            if len(grounded_tracks) >= count:
                break

        return DiscoveryResult(
            intent=intent,
            tracks=grounded_tracks,
            generated=generated,
            grounded=len(grounded_tracks),
            dropped=len(dropped_candidates),
            dropped_candidates=dropped_candidates,
            diagnostics=diagnostics,
        )


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _normalized_set(values: list[str]) -> set[str]:
    return {_normalize(value) for value in values if str(value).strip()}


def _dedupe_candidates(
    candidates: list[GeneratedCandidate],
) -> list[GeneratedCandidate]:
    result: list[GeneratedCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        title = str(candidate.title or "").strip()
        artist = str(candidate.artist or "").strip()
        if not title or not artist:
            continue
        key = f"{_normalize(artist)}::{_normalize(title)}"
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result

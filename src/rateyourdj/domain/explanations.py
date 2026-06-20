"""Evidence-based recommendation explanations.

A recommendation is more trustworthy when it can say *why* it was picked. This
module turns the structured signals that already exist on a ranked song —
ranking score breakdown, the user's long-term preferences, song metadata, and
the LLM's original discovery reason — into concrete, human-readable reasons.

The design principle is: collect structured `Evidence` first, then render it to
text. Reasons are never invented from nothing; each one is backed by a piece of
evidence with a weight, so the output stays grounded and auditable. An optional
LLM writer can later polish the rendered reasons into a DJ voice, but the
default template rendering already produces usable, faithful text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Evidence:
    """One piece of structured support for recommending a track."""

    type: str  # preference_match | similar_collection | discovery | ranking | metadata | feedback
    label: str  # short human label, e.g. "符合你的口味"
    detail: dict[str, Any] = field(default_factory=dict)
    weight: float = 0.5  # 0..1, higher = more important

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Reason:
    """A rendered, user-facing reason backed by one Evidence."""

    type: str
    label: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrackExplanation:
    song_id: str
    title: str | None
    artist: str | None
    evidence: list[Evidence]
    reasons: list[Reason]

    def to_dict(self) -> dict[str, Any]:
        return {
            "song_id": self.song_id,
            "title": self.title,
            "artist": self.artist,
            "evidence": [item.to_dict() for item in self.evidence],
            "reasons": [reason.to_dict() for reason in self.reasons],
        }


# Score-breakdown keys that indicate a preference / profile match contributed.
_PREFERENCE_BREAKDOWN_KEYS = (
    "genre_preference",
    "tag_preference",
    "artist_preference",
    "profile_match",
)


class ExplanationGenerator:
    """Generate evidence-backed reasons for ranked recommendations."""

    def __init__(self, *, max_reasons: int = 3) -> None:
        if max_reasons < 1:
            raise ValueError("max_reasons must be >= 1")
        self.max_reasons = max_reasons

    def explain_track(
        self,
        track: dict[str, Any],
        *,
        user_memory: dict[str, Any] | None = None,
        style: str = "balanced",
    ) -> TrackExplanation:
        memory = user_memory or {}
        evidence = self._collect_evidence(track, memory)
        evidence.sort(key=lambda item: item.weight, reverse=True)

        limit = 1 if style == "short" else self.max_reasons
        reasons: list[Reason] = []
        for item in evidence:
            text = self._render(item, track)
            if not text:
                continue
            reasons.append(Reason(type=item.type, label=item.label, text=text))
            if len(reasons) >= limit:
                break

        if not reasons:
            title = track.get("title") or "这首歌"
            reasons.append(
                Reason(
                    type="session_intent",
                    label="符合本次请求",
                    text=f"{title} 符合这次的推荐条件。",
                )
            )

        return TrackExplanation(
            song_id=str(track.get("song_id") or track.get("track_id") or ""),
            title=track.get("title"),
            artist=track.get("artist"),
            evidence=evidence,
            reasons=reasons,
        )

    def explain_all(
        self,
        tracks: list[dict[str, Any]],
        *,
        user_memory: dict[str, Any] | None = None,
        style: str = "balanced",
    ) -> list[TrackExplanation]:
        return [
            self.explain_track(track, user_memory=user_memory, style=style)
            for track in tracks
        ]

    # -- evidence collection -------------------------------------------------

    def _collect_evidence(
        self,
        track: dict[str, Any],
        memory: dict[str, Any],
    ) -> list[Evidence]:
        evidence: list[Evidence] = []

        matched_genres = self._matched(
            track.get("genres"), memory.get("genre_preferences")
        )
        matched_tags = self._matched(
            track.get("tags"), memory.get("tag_preferences")
        )
        matched_terms = matched_genres + matched_tags
        if matched_terms:
            top_weight = max(weight for _, weight in matched_terms)
            evidence.append(
                Evidence(
                    type="preference_match",
                    label="符合你的口味",
                    detail={"terms": [name for name, _ in matched_terms[:3]]},
                    weight=min(0.6 + top_weight * 0.4, 1.0),
                )
            )

        artist = str(track.get("artist") or "").strip()
        artist_prefs = memory.get("artist_preferences")
        if artist and isinstance(artist_prefs, dict):
            for name, weight in artist_prefs.items():
                if str(name).casefold() == artist.casefold():
                    evidence.append(
                        Evidence(
                            type="preference_match",
                            label="你偏好的艺人",
                            detail={"artist": artist},
                            weight=min(0.7 + float(weight) * 0.3, 1.0),
                        )
                    )
                    break

        discovery_reason = str(track.get("discovery_reason") or "").strip()
        if discovery_reason:
            evidence.append(
                Evidence(
                    type="discovery",
                    label="DJ 选曲理由",
                    detail={"reason": discovery_reason},
                    weight=0.9,
                )
            )

        ranking_reasons = track.get("ranking_reasons")
        if isinstance(ranking_reasons, list):
            for reason in ranking_reasons:
                if isinstance(reason, str) and reason.strip():
                    evidence.append(
                        Evidence(
                            type="ranking",
                            label="推荐算法依据",
                            detail={"reason": reason.strip()},
                            weight=0.5,
                        )
                    )
                    break

        breakdown = track.get("score_breakdown")
        if isinstance(breakdown, dict):
            feedback = float(breakdown.get("feedback_adjustment") or 0.0)
            if feedback > 0:
                evidence.append(
                    Evidence(
                        type="feedback",
                        label="基于你的正向反馈",
                        detail={"adjustment": round(feedback, 3)},
                        weight=0.55,
                    )
                )

        release_year = track.get("release_year")
        if isinstance(release_year, int) and not isinstance(release_year, bool):
            evidence.append(
                Evidence(
                    type="metadata",
                    label="年代背景",
                    detail={"year": release_year},
                    weight=0.3,
                )
            )

        return evidence

    # -- rendering -----------------------------------------------------------

    def _render(self, evidence: Evidence, track: dict[str, Any]) -> str:
        if evidence.type == "preference_match" and "terms" in evidence.detail:
            terms = evidence.detail.get("terms") or []
            if terms:
                return "你常听 " + "、".join(map(str, terms)) + "，这首正好是这类风格"
        if evidence.type == "preference_match" and "artist" in evidence.detail:
            return f"{evidence.detail['artist']} 是你偏好的艺人"
        if evidence.type == "discovery":
            return str(evidence.detail.get("reason") or "")
        if evidence.type == "ranking":
            return str(evidence.detail.get("reason") or "")
        if evidence.type == "feedback":
            return "和你之前点赞过的歌曲风格相近"
        if evidence.type == "metadata" and "year" in evidence.detail:
            return f"{evidence.detail['year']} 年代作品，契合这次的听歌场景"
        return ""

    @staticmethod
    def _matched(
        track_field: Any,
        preferences: Any,
    ) -> list[tuple[str, float]]:
        if not isinstance(preferences, dict):
            return []
        labels = _label_set(track_field)
        if not labels:
            return []
        matches: list[tuple[str, float]] = []
        for name, weight in preferences.items():
            if not isinstance(name, str):
                continue
            if name.casefold() in labels:
                try:
                    matches.append((name, float(weight)))
                except (TypeError, ValueError):
                    matches.append((name, 0.0))
        matches.sort(key=lambda item: item[1], reverse=True)
        return matches


def _label_set(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key).casefold() for key in value if key}
    if isinstance(value, list):
        return {str(item).casefold() for item in value if item}
    return set()

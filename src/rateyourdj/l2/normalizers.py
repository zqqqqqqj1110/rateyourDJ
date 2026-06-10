from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


GENRE_ALIASES = {
    "alt rock": "alternative_rock",
    "alternative": "alternative_rock",
    "alternative rock": "alternative_rock",
    "brit pop": "britpop",
    "britpop": "britpop",
    "classic rock": "classic_rock",
    "dance pop": "dance_pop",
    "dream pop": "dream_pop",
    "electronic": "electronic",
    "electronica": "electronic",
    "folk": "folk",
    "folk rock": "folk_rock",
    "funk": "funk",
    "hard rock": "hard_rock",
    "heavy metal": "metal",
    "hip hop": "hip_hop",
    "hip-hop": "hip_hop",
    "indie": "indie",
    "indie pop": "indie_pop",
    "indie rock": "indie_rock",
    "jazz": "jazz",
    "metal": "metal",
    "new wave": "new_wave",
    "pop": "pop",
    "pop rock": "pop_rock",
    "post punk": "post_punk",
    "post-punk": "post_punk",
    "progressive rock": "progressive_rock",
    "punk": "punk",
    "punk rock": "punk",
    "r&b": "r_and_b",
    "rnb": "r_and_b",
    "rock": "rock",
    "shoegaze": "shoegaze",
    "soul": "soul",
    "synthpop": "synthpop",
}

_NON_GENRE_TAGS = {
    "albums i own",
    "awesome",
    "beautiful",
    "best",
    "british",
    "favorites",
    "favourite",
    "great",
    "love",
    "male vocalists",
    "manchester",
    "seen live",
    "under 2000 listeners",
}
_DECADE_RE = re.compile(r"^(?:19|20)?\d0s$")


def normalize_tag_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = re.sub(r"[_/]+", " ", value)
    return re.sub(r"\s+", " ", value)


def normalize_tag_scores(
    tags: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> dict[str, float]:
    if not tags:
        return {}
    if isinstance(tags, Mapping):
        raw = {str(name): float(score) for name, score in tags.items()}
    else:
        raw = {
            str(item.get("name")): float(item.get("count") or 0)
            for item in tags
            if item.get("name")
        }
    maximum = max(raw.values(), default=0)
    if maximum <= 0:
        return {}
    return {
        normalize_tag_name(name): round(max(0.0, min(score / maximum, 1.0)), 4)
        for name, score in raw.items()
    }


class GenreNormalizer:
    def normalize(
        self,
        track_tags: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        artist_tags: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
        *,
        artist: str | None = None,
    ) -> dict[str, float]:
        normalized_track = normalize_tag_scores(track_tags)
        normalized_artist = normalize_tag_scores(artist_tags)
        artist_tag = normalize_tag_name(artist) if artist else None
        genres: dict[str, float] = {}

        self._merge_genres(
            genres,
            normalized_track,
            weight=1.0,
            minimum_score=0.05,
            artist_tag=artist_tag,
        )
        self._merge_genres(
            genres,
            normalized_artist,
            weight=0.4,
            minimum_score=0.2,
            artist_tag=artist_tag,
        )
        return dict(sorted(genres.items(), key=lambda item: (-item[1], item[0])))

    @staticmethod
    def _merge_genres(
        target: dict[str, float],
        tags: Mapping[str, float],
        *,
        weight: float,
        minimum_score: float,
        artist_tag: str | None,
    ) -> None:
        for tag, score in tags.items():
            if (
                score < minimum_score
                or tag == artist_tag
                or tag in _NON_GENRE_TAGS
                or _DECADE_RE.match(tag)
            ):
                continue
            genre = GENRE_ALIASES.get(tag)
            if genre is None:
                continue
            weighted_score = round(score * weight, 4)
            target[genre] = max(target.get(genre, 0), weighted_score)

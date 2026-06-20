from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .http import request_json


LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"


class LastfmCollector:
    def __init__(self, api_key: str, tag_limit: int = 20) -> None:
        if not api_key:
            raise ValueError("Last.fm API key is required")
        self.api_key = api_key
        self.tag_limit = tag_limit
        self._artist_tags: dict[str, list[dict[str, Any]]] = {}
        self._similar_artists: dict[tuple[str, int], list[dict[str, Any]]] = {}

    def collect_tags(self, title: str, artist: str) -> dict[str, Any]:
        artist_key = artist.casefold()
        if artist_key not in self._artist_tags:
            self._artist_tags[artist_key] = self._top_tags(
                "artist.getTopTags",
                artist=artist,
            )
        return {
            "source": "Last.fm",
            "track": title,
            "artist": artist,
            "track_tags": self._top_tags(
                "track.getTopTags",
                artist=artist,
                track=title,
            ),
            "artist_tags": self._artist_tags[artist_key],
        }

    def collect_similar_artists(
        self,
        artist: str,
        *,
        limit: int = 10,
    ) -> dict[str, Any]:
        resolved_limit = max(1, min(int(limit), 50))
        cache_key = (artist.casefold(), resolved_limit)
        if cache_key not in self._similar_artists:
            self._similar_artists[cache_key] = self._request_similar_artists(
                artist,
                limit=resolved_limit,
            )
        return {
            "source": "Last.fm",
            "artist": artist,
            "similar_artists": list(self._similar_artists[cache_key]),
        }

    def _top_tags(
        self,
        method: str,
        *,
        artist: str,
        track: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {
            "method": method,
            "api_key": self.api_key,
            "artist": artist,
            "autocorrect": 1,
            "format": "json",
        }
        if track is not None:
            params["track"] = track
        payload = request_json(f"{LASTFM_API_URL}?{urlencode(params)}")
        if "error" in payload:
            raise RuntimeError(
                f"Last.fm error {payload['error']}: "
                f"{payload.get('message', '')}"
            )
        tags = payload.get("toptags", {}).get("tag", [])
        if isinstance(tags, dict):
            tags = [tags]
        parsed = []
        for item in tags[: self.tag_limit]:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            try:
                count = int(item.get("count", 0))
            except (TypeError, ValueError):
                count = 0
            parsed.append({"name": name, "count": count})
        return parsed

    def _request_similar_artists(
        self,
        artist: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        params = {
            "method": "artist.getSimilar",
            "api_key": self.api_key,
            "artist": artist,
            "autocorrect": 1,
            "limit": limit,
            "format": "json",
        }
        payload = request_json(f"{LASTFM_API_URL}?{urlencode(params)}")
        if "error" in payload:
            raise RuntimeError(
                f"Last.fm error {payload['error']}: "
                f"{payload.get('message', '')}"
            )
        artists = payload.get("similarartists", {}).get("artist", [])
        if isinstance(artists, dict):
            artists = [artists]
        parsed: list[dict[str, Any]] = []
        for item in artists[:limit]:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            raw_match = item.get("match")
            try:
                match_score = float(raw_match)
            except (TypeError, ValueError):
                match_score = 0.0
            parsed.append(
                {
                    "name": name,
                    "match": max(0.0, min(match_score, 1.0)),
                    "url": str(item.get("url", "")).strip() or None,
                }
            )
        return parsed

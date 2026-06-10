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

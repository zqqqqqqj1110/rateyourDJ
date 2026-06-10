from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from rateyourdj.l2.matching import records_match, select_preferred_version

from .http import request_json


MUSICBRAINZ_BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = "rateyourDJ/0.1 (local music dataset builder)"


class MusicBrainzCollector:
    def __init__(self, request_interval_seconds: float = 1.05) -> None:
        self.request_interval_seconds = request_interval_seconds
        self._last_request_at = 0.0

    def collect_recording(self, title: str, artist: str) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_interval_seconds:
            time.sleep(self.request_interval_seconds - elapsed)
        query = quote(f'recording:"{title}" AND artist:"{artist}"')
        url = (
            f"{MUSICBRAINZ_BASE_URL}/recording/"
            f"?query={query}&fmt=json&limit=5"
        )
        payload = request_json(url, headers={"User-Agent": USER_AGENT})
        self._last_request_at = time.monotonic()
        recordings = payload.get("recordings", [])
        if not recordings:
            raise LookupError(
                f"No MusicBrainz recording found for {title} by {artist}"
            )
        candidates = [self._parse_recording(item) for item in recordings]
        reference = {"title": title, "artist": artist}
        matching = [
            candidate
            for candidate in candidates
            if records_match(reference, candidate)
        ]
        selected = select_preferred_version(matching)
        if selected is None:
            raise LookupError(
                f"No matching MusicBrainz recording found for {title}"
            )
        return dict(selected)

    @staticmethod
    def _parse_recording(recording: dict[str, Any]) -> dict[str, Any]:
        releases = recording.get("releases", [])
        release = releases[0] if releases else {}
        release_group = release.get("release-group", {})
        first_release_date = recording.get("first-release-date", "")
        artist_credit = recording.get("artist-credit", [])
        return {
            "source": "MusicBrainz",
            "musicbrainz_recording_id": recording.get("id"),
            "title": recording.get("title"),
            "artists": [
                credit.get("name")
                for credit in artist_credit
                if isinstance(credit, dict) and credit.get("name")
            ],
            "album": release_group.get("title") or release.get("title"),
            "release_year": (
                int(first_release_date[:4])
                if len(first_release_date) >= 4
                and first_release_date[:4].isdigit()
                else None
            ),
            "duration_ms": recording.get("length"),
            "score": recording.get("score"),
        }

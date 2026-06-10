from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlencode

from rateyourdj.l2.matching import records_match, select_preferred_version

from .http import request_json


SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"


class SpotifyCollector:
    def __init__(self, client_id: str, client_secret: str) -> None:
        if not client_id or not client_secret:
            raise ValueError("Spotify client credentials are required")
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None

    def _access_token(self) -> str:
        if self._token:
            return self._token
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        payload = request_json(
            SPOTIFY_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=urlencode({"grant_type": "client_credentials"}).encode("ascii"),
        )
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Spotify token response did not contain access_token")
        self._token = str(token)
        return self._token

    def collect_track(
        self,
        title: str,
        artist: str,
        album: str | None = None,
    ) -> dict[str, Any]:
        terms = [f'track:"{title}"', f'artist:"{artist}"']
        if album:
            terms.append(f'album:"{album}"')
        query = urlencode(
            {
                "q": " ".join(terms),
                "type": "track",
                "limit": 10,
            }
        )
        payload = request_json(
            f"{SPOTIFY_API_URL}/search?{query}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        items = payload.get("tracks", {}).get("items", [])
        if not items:
            raise LookupError(f"No Spotify track found for {title} by {artist}")
        candidates = [self._parse_track(item) for item in items]
        reference = {"title": title, "artist": artist}
        matching = [
            candidate
            for candidate in candidates
            if records_match(reference, candidate)
        ]
        selected = select_preferred_version(matching)
        if selected is None:
            raise LookupError(f"No matching Spotify track found for {title}")
        return dict(selected)

    @staticmethod
    def _parse_track(track: dict[str, Any]) -> dict[str, Any]:
        album = track.get("album", {})
        release_date = album.get("release_date", "")
        return {
            "source": "Spotify",
            "spotify_track_id": track.get("id"),
            "title": track.get("name"),
            "artists": [
                item.get("name")
                for item in track.get("artists", [])
                if item.get("name")
            ],
            "album": album.get("name"),
            "release_year": (
                int(release_date[:4])
                if len(release_date) >= 4 and release_date[:4].isdigit()
                else None
            ),
            "duration_ms": track.get("duration_ms"),
        }

"""Live smoke tests for external L2 metadata sources.

These tests are intentionally excluded from normal offline test runs. Enable them with:

    RUN_LIVE_API_TESTS=1 python -m unittest tests.test_data_collection_smoke -v

Spotify additionally requires:

    SPOTIFY_CLIENT_ID=...
    SPOTIFY_CLIENT_SECRET=...
"""

from __future__ import annotations

import base64
import json
import os
import unittest
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


RUN_LIVE_API_TESTS = os.getenv("RUN_LIVE_API_TESTS") == "1"
MUSICBRAINZ_BASE_URL = "https://musicbrainz.org/ws/2"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"
USER_AGENT = "rateyourDJ/0.1 (L2 metadata collection smoke test)"
TIMEOUT_SECONDS = 15


def _request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> dict[str, Any]:
    request = Request(
        url,
        headers={"Accept": "application/json", **(headers or {})},
        data=data,
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {error.code} from {url}: {body[:500]}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"Could not reach {url}: {error.reason}") from error


def collect_musicbrainz_recording(
    title: str, artist: str
) -> dict[str, Any]:
    query = quote(f'recording:"{title}" AND artist:"{artist}"')
    url = (
        f"{MUSICBRAINZ_BASE_URL}/recording/"
        f"?query={query}&fmt=json&limit=1"
    )
    payload = _request_json(url, headers={"User-Agent": USER_AGENT})
    recordings = payload.get("recordings", [])
    if not recordings:
        raise LookupError(f"No MusicBrainz recording found for {title} by {artist}")

    recording = recordings[0]
    releases = recording.get("releases", [])
    release = releases[0] if releases else {}
    release_group = release.get("release-group", {})
    first_release_date = recording.get("first-release-date", "")
    artist_credit = recording.get("artist-credit", [])
    artist_names = [
        credit.get("name", "")
        for credit in artist_credit
        if isinstance(credit, dict) and credit.get("name")
    ]

    return {
        "source": "MusicBrainz",
        "musicbrainz_recording_id": recording.get("id"),
        "title": recording.get("title"),
        "artists": artist_names,
        "album": release_group.get("title") or release.get("title"),
        "release_year": (
            int(first_release_date[:4])
            if len(first_release_date) >= 4 and first_release_date[:4].isdigit()
            else None
        ),
        "duration_ms": recording.get("length"),
        "isrcs": recording.get("isrcs", []),
        "score": recording.get("score"),
    }


def _spotify_access_token(client_id: str, client_secret: str) -> str:
    credentials = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("ascii")
    payload = _request_json(
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
    return str(token)


def collect_spotify_track(
    title: str,
    artist: str,
    *,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    token = _spotify_access_token(client_id, client_secret)
    query = urlencode(
        {
            "q": f'track:"{title}" artist:"{artist}"',
            "type": "track",
            "limit": 1,
        }
    )
    payload = _request_json(
        f"{SPOTIFY_API_URL}/search?{query}",
        headers={"Authorization": f"Bearer {token}"},
    )
    items = payload.get("tracks", {}).get("items", [])
    if not items:
        raise LookupError(f"No Spotify track found for {title} by {artist}")

    track = items[0]
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
        "popularity": (
            track["popularity"] / 100
            if isinstance(track.get("popularity"), int)
            else None
        ),
        "isrc": track.get("external_ids", {}).get("isrc"),
        "explicit": track.get("explicit"),
    }


@unittest.skipUnless(
    RUN_LIVE_API_TESTS,
    "set RUN_LIVE_API_TESTS=1 to call external APIs",
)
class MusicBrainzCollectionSmokeTest(unittest.TestCase):
    def test_collect_wonderwall(self) -> None:
        result = collect_musicbrainz_recording("Wonderwall", "Oasis")
        print("\nMusicBrainz result:")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))

        self.assertEqual(result["source"], "MusicBrainz")
        self.assertTrue(result["musicbrainz_recording_id"])
        self.assertIn("Wonderwall", result["title"])
        self.assertIn("Oasis", result["artists"])


@unittest.skipUnless(
    RUN_LIVE_API_TESTS
    and bool(os.getenv("SPOTIFY_CLIENT_ID"))
    and bool(os.getenv("SPOTIFY_CLIENT_SECRET")),
    "set RUN_LIVE_API_TESTS=1 and Spotify client credentials",
)
class SpotifyCollectionSmokeTest(unittest.TestCase):
    def test_collect_wonderwall(self) -> None:
        result = collect_spotify_track(
            "Wonderwall",
            "Oasis",
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        )
        print("\nSpotify result:")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))

        self.assertEqual(result["source"], "Spotify")
        self.assertTrue(result["spotify_track_id"])
        self.assertIn("Wonderwall", result["title"])
        self.assertIn("Oasis", result["artists"])


if __name__ == "__main__":
    unittest.main()

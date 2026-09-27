"""On-demand playback source resolution with a persistent cache.

head / mid / unknown -> Spotify (ISRC exact match first, then title+artist),
                        falling back to YouTube
tail                 -> YouTube Data API search, verified with videos.list
                        (public + embeddable, title contains the song title)

Only songs about to be shown (or explicitly requested, e.g. seeds and eval
sets) are resolved. YouTube search costs quota (default 100 searches/day), so
searches are counted per UTC day and stop at ``youtube_daily_search_budget``.
Failures are cached too (and retried after ``retry_misses_after_days``).
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from rateyourdj.contracts import normalize_name

from .http_cache import CachedJsonClient
from .sources import MusicBrainzClient, split_title, title_parts

YT_API = "https://www.googleapis.com/youtube/v3"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(text: str) -> str:
    text = re.sub(r"[\(\[].*?[\)\]]", " ", text or "")
    return normalize_name(re.sub(r"[^\w\s]", " ", text))


class YouTubeResolver:
    def __init__(self, cache_root: str | Path, *, api_key: str | None = None,
                 **client_kwargs: Any) -> None:
        self.api_key = api_key or os.getenv("YOUTUBE_API_KEY")
        # key travels in a header so it never lands in cached URLs
        self.http = CachedJsonClient(Path(cache_root) / "youtube",
                                     headers={"X-Goog-Api-Key": self.api_key or ""},
                                     min_interval=0.2, **client_kwargs)

    def search(self, title: str, artist: str) -> list[dict[str, Any]]:
        url = f"{YT_API}/search?" + urlencode({
            "part": "snippet", "type": "video", "maxResults": 5, "videoCategoryId": "10",
            "q": f"{artist} {title}"})
        payload = self.http.get(url) or {}
        return [{"video_id": (i.get("id") or {}).get("videoId"),
                 "title": html.unescape((i.get("snippet") or {}).get("title", "")),
                 "channel": html.unescape((i.get("snippet") or {}).get("channelTitle", ""))}
                for i in payload.get("items") or [] if (i.get("id") or {}).get("videoId")]

    def verify(self, video_id: str) -> dict[str, Any] | None:
        url = f"{YT_API}/videos?" + urlencode({"part": "snippet,status", "id": video_id})
        items = (self.http.get(url, use_cache=False) or {}).get("items") or []
        if not items:
            return None
        item = items[0]
        status = item.get("status") or {}
        if status.get("privacyStatus") != "public" or status.get("embeddable") is False:
            return None
        snippet = item.get("snippet") or {}
        return {"title": html.unescape(snippet.get("title", "")),
                "channel": html.unescape(snippet.get("channelTitle", ""))}


def same_song_title(candidate: str, title: str) -> bool:
    """Base titles equal and, if the song names parts, the same parts (I–V == 1-5)."""
    if _clean(split_title(candidate)[0]) != _clean(split_title(title)[0]):
        return False
    wanted = title_parts(title)
    return not wanted or title_parts(candidate) == wanted


def source_rank(candidate: dict[str, Any], artist: str) -> int:
    """0 = YouTube auto-generated '<Artist> - Topic', 1 = artist's own channel, 2 = anyone else."""
    channel = _clean(candidate.get("channel", ""))
    artist_c = _clean(artist)
    if channel == artist_c + " topic":
        return 0
    if channel in (artist_c, artist_c + " official", artist_c + "vevo", "official " + artist_c):
        return 1
    return 2


def youtube_match(candidate: dict[str, Any], title: str, artist: str) -> bool:
    text = _clean(candidate["title"])
    channel = _clean(candidate["channel"])
    # video titles look like "Artist - Song (Official Audio)"; strip a leading artist
    video_title = candidate["title"]
    for sep in (" - ", " – ", " — "):
        head, found, rest = video_title.partition(sep)
        if found and _clean(artist) in _clean(head):
            video_title = rest
            break
    if not same_song_title(video_title, title) and _clean(split_title(title)[0]) not in text:
        return False
    if title_parts(title) and title_parts(video_title) != title_parts(title):
        return False
    artist_c = _clean(artist)
    return artist_c in text or artist_c in channel or channel == artist_c + " topic"


class PlaybackResolver:
    def __init__(self, cache_path: str | Path, *, mb: MusicBrainzClient | None = None,
                 youtube: YouTubeResolver | None = None,
                 spotify_search: Callable[[str], list[dict[str, Any]]] | None = None,
                 youtube_daily_search_budget: int = 95, retry_misses_after_days: int = 30,
                 now: Callable[[], datetime] = _now) -> None:
        self.cache_path = Path(cache_path)
        self.mb, self.youtube, self.spotify_search = mb, youtube, spotify_search
        self.budget = youtube_daily_search_budget
        self.retry_after = timedelta(days=retry_misses_after_days)
        self.now = now
        self.state = self._load()

    # ---------------------------------------------------------- persistence
    def _load(self) -> dict[str, Any]:
        if self.cache_path.is_file():
            return json.loads(self.cache_path.read_text("utf-8"))
        return {"songs": {}, "youtube_searches": {}}

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1) + "\n", "utf-8")
        tmp.replace(self.cache_path)

    def _searches_today(self) -> int:
        return self.state["youtube_searches"].get(self.now().date().isoformat(), 0)

    def _count_search(self) -> None:
        day = self.now().date().isoformat()
        self.state["youtube_searches"][day] = self.state["youtube_searches"].get(day, 0) + 1

    # ---------------------------------------------------------- resolution
    def resolve(self, song: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        cached = self.state["songs"].get(song["song_id"])
        if cached and not force:
            if cached["source"] != "none":
                return cached
            checked = datetime.fromisoformat(cached["checked_at"])
            if cached.get("reason") != "youtube_quota" and self.now() - checked < self.retry_after:
                return cached
        bucket = song.get("popularity", {}).get("bucket", "unknown")
        title = song.get("title") or ""
        artist = song.get("artist_credit") or ", ".join(a.get("name") or "" for a in song.get("artists", []))
        result = None
        if bucket in ("head", "mid", "unknown"):
            result = self._spotify(song, title, artist)
        if result is None:
            result = self._youtube(title, artist)
        result["checked_at"] = self.now().isoformat(timespec="seconds")
        self.state["songs"][song["song_id"]] = result
        return result

    def _spotify(self, song: dict[str, Any], title: str, artist: str) -> dict[str, Any] | None:
        if self.spotify_search is None:
            return None
        isrcs = list(song.get("external_ids", {}).get("isrc") or [])
        mbid = song.get("external_ids", {}).get("musicbrainz_recording")
        if not isrcs and mbid and self.mb is not None:
            isrcs = self.mb.lookup_recording(mbid).get("isrcs", [])
            song.setdefault("external_ids", {})["isrc"] = isrcs
        for isrc in isrcs[:8]:
            items = self.spotify_search(f"isrc:{isrc}")
            if items:
                return self._spotify_result(items[0], "spotify_isrc")
        for item in self.spotify_search(f'track:"{title}" artist:"{artist}"'):
            names = " ".join(item.get("artists", []))
            if same_song_title(item.get("title", ""), title) and _clean(artist) in _clean(names):
                return self._spotify_result(item, "spotify_title_artist")
        return None

    @staticmethod
    def _spotify_result(item: dict[str, Any], method: str) -> dict[str, Any]:
        track_id = item["spotify_track_id"]
        return {"source": "spotify", "url": f"https://open.spotify.com/track/{track_id}",
                "spotify_track": track_id, "verified": True, "verification_method": method}

    def _youtube(self, title: str, artist: str) -> dict[str, Any]:
        miss = {"source": "none", "url": None, "verified": False, "verification_method": None}
        if self.youtube is None or not self.youtube.api_key:
            return {**miss, "reason": "youtube_not_configured"}
        if self._searches_today() >= self.budget:
            return {**miss, "reason": "youtube_quota"}
        self._count_search()
        matches = [c for c in self.youtube.search(title, artist) if youtube_match(c, title, artist)]
        for candidate in sorted(matches, key=lambda c: source_rank(c, artist)):
            verified = self.youtube.verify(candidate["video_id"])
            if verified and youtube_match({**candidate, **verified}, title, artist):
                vid = candidate["video_id"]
                return {"source": "youtube", "url": f"https://www.youtube.com/watch?v={vid}",
                        "youtube_video": vid, "verified": True,
                        "verification_method": "youtube_videos_list",
                        "channel": candidate["channel"],
                        "official": source_rank(candidate, artist) < 2}
        return {**miss, "reason": "no_verified_match"}


def spotify_search_from_env() -> Callable[[str], list[dict[str, Any]]] | None:
    client_id, secret = os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")
    if not (client_id and secret):
        return None
    from rateyourdj.collectors.http import request_json
    from rateyourdj.collectors.spotify import SPOTIFY_API_URL, SpotifyCollector

    collector = SpotifyCollector(client_id, secret)

    def search(query: str) -> list[dict[str, Any]]:
        url = f"{SPOTIFY_API_URL}/search?" + urlencode({"q": query, "type": "track", "limit": 5})
        payload = request_json(url, headers={"Authorization": f"Bearer {collector._access_token()}"})
        return [SpotifyCollector._parse_track(i) for i in (payload.get("tracks") or {}).get("items") or []]

    return search


def apply_playback(song: dict[str, Any], result: dict[str, Any]) -> None:
    """Copy a resolution result into a SongProfileV2 record."""
    song["playback"] = {k: result.get(k) for k in ("source", "url", "verified", "verification_method")}
    song["playback"]["verified_at"] = result.get("checked_at") if result.get("verified") else None
    if result.get("reason"):
        song["playback"]["reason"] = result["reason"]
    ext = song.setdefault("external_ids", {})
    if result.get("spotify_track"):
        ext["spotify_track"] = result["spotify_track"]
    if result.get("youtube_video"):
        ext["youtube_video"] = result["youtube_video"]

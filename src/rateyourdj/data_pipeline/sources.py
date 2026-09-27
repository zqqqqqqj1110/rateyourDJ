"""MusicBrainz and ListenBrainz API clients built on CachedJsonClient.

Response parsing is deliberately defensive: every parser tolerates missing
fields and returns plain dicts, so a schema change upstream degrades to
missing values (visible in the quality report) instead of crashing a run.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlencode

from .http_cache import CachedJsonClient

MB_BASE = "https://musicbrainz.org/ws/2"
LB_BASE = "https://api.listenbrainz.org/1"
LB_LABS = "https://labs.api.listenbrainz.org"
# Widest listening window with the full 100-result limit; overridable.
DEFAULT_SIMILAR_ARTISTS_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
)
NON_STUDIO_SECONDARY_TYPES = {"live", "compilation", "remix", "dj-mix", "demo", "interview",
                              "soundtrack", "spokenword", "audiobook", "mixtape/street"}
# Checked only against a title's version suffix ("(...)", "[...]", " - ...") and
# the MB disambiguation, never the base title: "Live Forever" is not a live take.
VARIANT_WORDS = ("live", "demo", "remix", " mix", "acoustic", "instrumental", "karaoke",
                 "session", "rehearsal", "unplugged", "radio edit", "take ", "version",
                 " tv ", "8-track", "bootleg", "outtake")
_DATED_SUFFIX = re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b|\b(19|20)\d{2}[-/]\d{2}[-/]\d{2}\b")
NON_STUDIO_WORDS = VARIANT_WORDS  # backwards-compatible alias


def split_title(title: str) -> tuple[str, str]:
    """('Time - 2011 Remaster') -> ('Time', '2011 Remaster'); suffix joins all version parts."""
    text = title or ""
    suffix_parts = re.findall(r"[\(\[]([^\)\]]*)[\)\]]", text)
    base = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*", " ", text)
    if " - " in base:
        base, _, tail = base.partition(" - ")
        suffix_parts.append(tail)
    # "Shine On You Crazy Diamond, Parts I–V" / "…, Pt. 1–5": part numbers are a suffix
    match = re.search(r",?\s+\b(parts?|pts?\.?)\s+(.*)$", base, flags=re.I)
    if match:
        suffix_parts.append(match.group(0).lstrip(", "))
        base = base[: match.start()]
    return base.strip(), " ".join(suffix_parts).strip()


_ROMAN = {"I": 1, "V": 5, "X": 10}


def _to_int(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    token = token.upper()
    if not token or any(c not in _ROMAN for c in token):
        return None
    total = 0
    for i, c in enumerate(token):
        v = _ROMAN[c]
        total += -v if i + 1 < len(token) and _ROMAN[token[i + 1]] > v else v
    return total


def title_parts(title: str) -> set[int]:
    """Part numbers named in a title's suffix: 'Parts I–V' -> {1..5}; 'Parts 1–5, 7' -> {1..5, 7}."""
    suffix = split_title(title)[1]
    m = re.search(r"\b(?:parts?|pts?\.?)\s+(.*)$", suffix, flags=re.I)
    if not m:
        return set()
    parts: set[int] = set()
    for piece in re.split(r"[,&/]|\band\b", m.group(1)):
        bounds = [_to_int(t) for t in re.findall(r"[0-9]+|[IVXivx]+", piece)[:2]]
        bounds = [b for b in bounds if b]
        if len(bounds) == 2 and re.search(r"[-–—]", piece):
            parts.update(range(min(bounds), max(bounds) + 1))
        elif bounds:
            parts.add(bounds[0])
    return parts


_BOOTLEG_RELEASE = re.compile(
    r"^\d{4}[-‐.]\d{2}[-‐.]\d{2}|\b(live|tour|concert|bootleg|sessions?|radio shows?|"
    r"broadcast|soundboard|audience|cassette master|outtakes?|rehearsals?|remix(es)?|remixed|karaoke|tribute)\b|\b(19|20)\d{2}\b.*\b(night|show)\b",
    re.I)


def is_bootleg_release(release_title: str | None) -> bool:
    """Heuristic for unofficial live / archival releases seen in ListenBrainz data."""
    return bool(release_title) and bool(_BOOTLEG_RELEASE.search(release_title))


def is_variant_title(title: str, disambiguation: str = "") -> bool:
    suffix = (" " + split_title(title)[1] + " " + (disambiguation or "") + " ").lower()
    return any(word in suffix for word in VARIANT_WORDS) or bool(_DATED_SUFFIX.search(suffix))


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _lucene(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ----------------------------------------------------------------- MusicBrainz


class MusicBrainzClient:
    def __init__(self, cache_root: str | Path, *, contact: str | None = None,
                 **client_kwargs: Any) -> None:
        contact = contact or os.getenv("MUSICBRAINZ_CONTACT") or "unknown"
        self.http = CachedJsonClient(
            Path(cache_root) / "musicbrainz",
            headers={"User-Agent": f"rateyourDJ/0.2 ( {contact} )"},
            min_interval=client_kwargs.pop("min_interval", 1.1),  # MB: 1 req/s
            **client_kwargs,
        )

    def search_recordings(self, title: str, artist: str, limit: int = 25) -> list[dict[str, Any]]:
        query = f"recording:{_lucene(title)} AND artist:{_lucene(artist)}"
        url = f"{MB_BASE}/recording?" + urlencode({"query": query, "limit": limit, "fmt": "json"})
        payload = self.http.get(url) or {}
        return [parse_mb_recording(r) for r in payload.get("recordings") or []]

    def lookup_recording(self, mbid: str) -> dict[str, Any]:
        url = f"{MB_BASE}/recording/{quote(mbid)}?" + urlencode(
            {"inc": "artist-credits isrcs tags releases release-groups", "fmt": "json"})
        return parse_mb_recording(self.http.get(url) or {})

    def search_artists_by_tag(self, tag: str, *, limit: int = 100, offset: int = 0
                              ) -> list[dict[str, Any]]:
        url = f"{MB_BASE}/artist?" + urlencode(
            {"query": f"tag:{_lucene(tag)}", "limit": limit, "offset": offset, "fmt": "json"})
        payload = self.http.get(url) or {}
        return [
            {"mbid": a.get("id"), "name": a.get("name"), "score": a.get("score"),
             "country": a.get("country"),
             "tags": [t.get("name") for t in a.get("tags") or [] if t.get("name")]}
            for a in payload.get("artists") or [] if a.get("id")
        ]

    def lookup_artist(self, mbid: str) -> dict[str, Any]:
        url = f"{MB_BASE}/artist/{quote(mbid)}?" + urlencode({"inc": "tags", "fmt": "json"})
        a = self.http.get(url) or {}
        return {"mbid": a.get("id"), "name": a.get("name"), "country": a.get("country"),
                "tags": sorted(((t.get("name"), t.get("count", 0)) for t in a.get("tags") or []),
                               key=lambda x: -x[1])}


def parse_mb_recording(r: dict[str, Any]) -> dict[str, Any]:
    credits = r.get("artist-credit") or []
    releases = []
    for rel in r.get("releases") or []:
        rg = rel.get("release-group") or {}
        releases.append({
            "mbid": rel.get("id"), "title": rel.get("title"), "status": rel.get("status"),
            "date": rel.get("date"), "country": rel.get("country"),
            "primary_type": rg.get("primary-type"),
            "secondary_types": [s.lower() for s in rg.get("secondary-types") or []],
        })
    return {
        "mbid": r.get("id"),
        "title": r.get("title"),
        "score": r.get("score"),
        "length_ms": r.get("length"),
        "disambiguation": r.get("disambiguation") or "",
        "video": bool(r.get("video")),
        "artist_credit": "".join((c.get("name") or "") + (c.get("joinphrase") or "") for c in credits),
        "artists": [{"name": (c.get("artist") or {}).get("name") or c.get("name"),
                     "mbid": (c.get("artist") or {}).get("id")} for c in credits],
        "isrcs": list(r.get("isrcs") or []),
        "tags": [(t.get("name"), t.get("count", 0)) for t in r.get("tags") or []],
        "first_release_date": r.get("first-release-date"),
        "releases": releases,
    }


def is_studio_recording(rec: dict[str, Any]) -> bool:
    """Heuristic: not live/demo/remix and on at least one official studio album/single/EP."""
    if rec.get("video") or is_variant_title(rec.get("title", ""), rec.get("disambiguation", "")):
        return False
    releases = rec.get("releases") or []
    if not releases:
        return True  # unknown; let later dedupe decide
    return any(
        (rel.get("status") in (None, "Official"))
        and not (set(rel.get("secondary_types") or []) & NON_STUDIO_SECONDARY_TYPES)
        for rel in releases
    )


# ---------------------------------------------------------------- ListenBrainz


class ListenBrainzClient:
    def __init__(self, cache_root: str | Path, *, token: str | None = None,
                 batch_size: int = 500, **client_kwargs: Any) -> None:
        token = token or os.getenv("LISTENBRAINZ_TOKEN")
        headers = {"User-Agent": "rateyourDJ/0.2"}
        if token:
            headers["Authorization"] = f"Token {token}"
        self.batch_size = batch_size
        self.http = CachedJsonClient(Path(cache_root) / "listenbrainz", headers=headers,
                                     min_interval=client_kwargs.pop("min_interval", 0.2),
                                     **client_kwargs)

    def similar_artists(self, artist_mbid: str, *,
                        algorithm: str = DEFAULT_SIMILAR_ARTISTS_ALGORITHM) -> list[dict[str, Any]]:
        url = f"{LB_LABS}/similar-artists/json?" + urlencode(
            {"artist_mbids": artist_mbid, "algorithm": algorithm})
        rows = _flatten_dicts(self.http.get(url, allow_404=True))
        out = []
        for row in rows:
            mbid = row.get("artist_mbid")
            if mbid and mbid != artist_mbid:
                out.append({"mbid": mbid, "name": row.get("name"),
                            "score": float(row.get("score") or 0)})
        return sorted(out, key=lambda x: -x["score"])

    def top_recordings_for_artist(self, artist_mbid: str) -> list[dict[str, Any]]:
        url = f"{LB_BASE}/popularity/top-recordings-for-artist/{quote(artist_mbid)}"
        rows = _flatten_dicts(self.http.get(url, allow_404=True))
        return [
            {"recording_mbid": r.get("recording_mbid"), "title": r.get("recording_name"),
             "artist_credit": r.get("artist_name"), "artist_mbids": list(r.get("artist_mbids") or []),
             "release_mbid": r.get("release_mbid"), "release_title": r.get("release_name"),
             "length_ms": r.get("length"),
             "listen_count": r.get("total_listen_count"),
             "listener_count": r.get("total_user_count")}
            for r in rows if r.get("recording_mbid")
        ]

    def recording_popularity(self, mbids: list[str]) -> dict[str, dict[str, Any]]:
        """mbid -> {listen_count, listener_count}; mbids LB doesn't know map to zeros/None."""
        result: dict[str, dict[str, Any]] = {}
        for batch in _chunks(sorted(set(mbids)), self.batch_size):
            rows = _flatten_dicts(self.http.post(f"{LB_BASE}/popularity/recording",
                                                 {"recording_mbids": batch}))
            for r in rows:
                if r.get("recording_mbid"):
                    result[r["recording_mbid"]] = {"listen_count": r.get("total_listen_count"),
                                                   "listener_count": r.get("total_user_count")}
        return result

    def recording_metadata(self, mbids: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for batch in _chunks(sorted(set(mbids)), self.batch_size):
            payload = self.http.post(f"{LB_BASE}/metadata/recording/",
                                     {"recording_mbids": batch, "inc": "artist tag release"}) or {}
            if isinstance(payload, dict):
                for mbid, meta in payload.items():
                    if isinstance(meta, dict):
                        result[mbid] = parse_lb_metadata(meta)
        return result


def _flatten_dicts(value: Any) -> list[dict[str, Any]]:
    """Accept list, list-of-lists, {'payload': ...} or {'data': ...} shapes."""
    if value is None:
        return []
    if isinstance(value, dict):
        if value.get("_not_found"):
            return []
        for key in ("payload", "data", "results"):
            if key in value:
                return _flatten_dicts(value[key])
        return [value]
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, list):
            out.extend(_flatten_dicts(item))
    return out


def parse_lb_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    recording = meta.get("recording") or {}
    release = meta.get("release") or {}
    artist = meta.get("artist") or {}
    tags = meta.get("tag") or {}

    def tag_list(key: str) -> list[tuple[str, int]]:
        return [(t.get("tag"), int(t.get("count") or 0)) for t in tags.get(key) or [] if t.get("tag")]

    artists = artist.get("artists") or []
    return {
        "title": recording.get("name"),
        "length_ms": recording.get("length"),
        "artist_credit": artist.get("name"),
        "artists": [{"name": a.get("name"), "mbid": a.get("artist_mbid"),
                     "country": a.get("area") if isinstance(a.get("area"), str) else None}
                    for a in artists],
        "release": {"title": release.get("name"), "mbid": release.get("mbid"),
                    "year": release.get("year"),
                    "release_group_mbid": release.get("release_group_mbid")},
        "recording_tags": tag_list("recording"),
        "artist_tags": tag_list("artist"),
        "release_group_tags": tag_list("release_group"),
    }

"""Retrieval documents: one short, fact-only text per catalog song.

Only structured catalog facts go in. doc-v2 describes a song by *who* and *how it
sounds* (artist, style tags, decade) and deliberately leaves out the song and
album titles: with bge-m3, doc-v1 (title first) matched queries on title words
("Time" -> "Take Your Time", "Supernova" -> "Supernova") instead of on style.
Popularity is also left out: it is metadata used by the tail/explore channels.
"""

from __future__ import annotations

import hashlib
from typing import Any

DOC_VERSION = "doc-v2"
MAX_TAGS = 12


def song_document(song: dict[str, Any]) -> str:
    artist = song.get("artist_credit") or ", ".join(
        a.get("name") or "" for a in song.get("artists", []) if a.get("name"))
    release = song.get("release") or {}
    tags = [t["name"] for t in song.get("tags", [])[:MAX_TAGS] if t.get("name")]
    year = release.get("year")
    era = f"{year // 10 * 10}s " if isinstance(year, int) and year > 0 else ""
    style = ", ".join(tags) if tags else "rock"
    return f"A {era}song by {artist}. Style: {style}."


def documents_digest(docs: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for song_id, text in docs:
        digest.update(song_id.encode() + b"\t" + text.encode() + b"\n")
    return digest.hexdigest()

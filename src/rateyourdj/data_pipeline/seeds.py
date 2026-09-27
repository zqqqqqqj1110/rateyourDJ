"""Resolve a participant's seed songs to MusicBrainz recording MBIDs.

Preference order for each seed:
1. the ListenBrainz top recording of the seed artist whose title matches and
   that has the most listeners (that is the MBID LB actually counts listens on);
2. otherwise the best MusicBrainz search hit that looks like a studio original.
Every decision is written to a review file so the user can confirm seeds.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from rateyourdj.contracts import make_song_id, normalize_name

from .sources import (ListenBrainzClient, MusicBrainzClient, is_studio_recording,
                      is_variant_title, split_title, title_parts)
from .user_context import load_user_context, new_user_context, save_user_context, UserContextNotFound


def _base_title(title: str) -> str:
    return normalize_name(split_title(unicodedata.normalize("NFKC", title or ""))[0])


def _title_ok(candidate: str, seed: dict[str, Any]) -> bool:
    """Same base title; if the seed names parts (e.g. [1, 5]) the candidate must name exactly them."""
    if _base_title(candidate) != _base_title(seed["title"]):
        return False
    if "parts" in seed:
        lo, hi = seed["parts"]
        return title_parts(candidate) == set(range(lo, hi + 1))
    return True


def _artist_ok(candidate_artists: list[dict[str, Any]], credit: str, artist: str) -> bool:
    target = normalize_name(artist)
    names = {normalize_name(a.get("name") or "") for a in candidate_artists}
    return target in names or normalize_name(credit or "") == target


def resolve_seed(mb: MusicBrainzClient, lb: ListenBrainzClient, artist: str,
                 seed: dict[str, Any]) -> dict[str, Any]:
    # same artist + same base title: enough to identify the artist (MB titles often omit
    # part numbers); the stricter seed rules (e.g. parts) only filter the final choice
    base_hits = [h for h in mb.search_recordings(seed["title"], artist)
                 if _artist_ok(h["artists"], h["artist_credit"], artist)
                 and _base_title(h["title"]) == _base_title(seed["title"])]
    hits = [h for h in base_hits if _title_ok(h["title"], seed)]
    studio = [h for h in hits if is_studio_recording(h)]
    artist_mbid = next((a["mbid"] for h in base_hits for a in h["artists"]
                        if normalize_name(a.get("name") or "") == normalize_name(artist)), None)

    chosen, method, lb_candidates = None, None, []
    if seed.get("recording_mbid"):  # explicit pin in the seed config wins
        chosen, method = seed["recording_mbid"], "pinned"
    elif artist_mbid:
        lb_candidates = [r for r in lb.top_recordings_for_artist(artist_mbid)
                         if _title_ok(r["title"] or "", seed)
                         and not is_variant_title(r["title"] or "")]
        lb_candidates.sort(key=lambda r: -(r.get("listener_count") or 0))
        if lb_candidates:
            chosen, method = lb_candidates[0]["recording_mbid"], "listenbrainz_top_recording"
    if chosen is None and studio:
        def rank(h: dict[str, Any]) -> tuple:
            dates = [r.get("date") or "9999" for r in h["releases"]
                     if r.get("primary_type") == "Album" and r.get("status") == "Official"]
            return (min(dates) if dates else "9999", -(h.get("score") or 0))
        chosen, method = sorted(studio, key=rank)[0]["mbid"], "musicbrainz_search"

    detail = mb.lookup_recording(chosen) if chosen else {}
    song_id = make_song_id(recording_mbid=chosen)[0] if chosen else None
    return {
        "query": {"artist": artist, **seed},
        "status": "resolved" if chosen else "unresolved",
        "method": method,
        "recording_mbid": chosen,
        "song_id": song_id,
        "artist_mbid": artist_mbid,
        "title": detail.get("title"),
        "artist_credit": detail.get("artist_credit"),
        "isrcs": detail.get("isrcs", []),
        "first_release_date": detail.get("first_release_date"),
        "alternatives": {
            "listenbrainz": [{k: r.get(k) for k in ("recording_mbid", "title", "listener_count")}
                             for r in lb_candidates[:5]],
            "musicbrainz_studio": [{k: h.get(k) for k in ("mbid", "title", "disambiguation", "score")}
                                   for h in studio[:5]],
        },
    }


def resolve_participant_seeds(config_path: str | Path, *, cache_root: str | Path,
                              users_root: str | Path = "data/users",
                              mb: MusicBrainzClient | None = None,
                              lb: ListenBrainzClient | None = None) -> dict[str, Any]:
    config = json.loads(Path(config_path).read_text("utf-8"))
    mb = mb or MusicBrainzClient(cache_root)
    lb = lb or ListenBrainzClient(cache_root)
    user_id = config["user_id"]
    try:
        context = load_user_context(user_id, users_root, legacy_root=None)
    except UserContextNotFound:
        context = new_user_context(user_id, exploration_level=config.get("exploration_level", 0.5))

    branches, review = [], []
    for branch in config["branches"]:
        resolved = [resolve_seed(mb, lb, branch["artist"], song) for song in branch["songs"]]
        review.extend({"branch_id": branch["branch_id"], **r} for r in resolved)
        artist_mbids = sorted({r["artist_mbid"] for r in resolved if r["artist_mbid"]})
        branches.append({
            "branch_id": branch["branch_id"],
            "label": branch.get("label", ""),
            "seed_song_ids": [r["song_id"] for r in resolved if r["song_id"]],
            "seed_artists_mbid": artist_mbids,
            "seed_recording_mbids": [r["recording_mbid"] for r in resolved if r["recording_mbid"]],
        })
    context["seed_branches"] = branches
    path = save_user_context(context, users_root)
    review_path = path.parent / "seeds_resolved.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return {"context_path": str(path), "review_path": str(review_path),
            "resolved": sum(r["status"] == "resolved" for r in review), "total": len(review),
            "unresolved": [r["query"] for r in review if r["status"] != "resolved"]}

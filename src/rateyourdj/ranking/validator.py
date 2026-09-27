"""validate_selection: the guard every final selection must pass (agent-tools-contract V2.8).

A selection is a list of picks ``{"song_id", "reason", "evidence_refs"}``. It is
rejected (and the caller falls back to deterministic ranking) when any check fails.
"""

from __future__ import annotations

from typing import Any


def validate_selection(picks: Any, candidate_set: dict[str, Any], *, count: int,
                       max_per_artist: int = 2, min_tail: int = 0,
                       exclude_song_ids: set[str] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    violations: list[dict[str, Any]] = []   # structured, used to write targeted repair hints
    if not isinstance(picks, list) or not picks:
        return {"ok": False, "errors": ["selection must be a non-empty list"], "out_of_set": 0,
                "violations": [{"type": "empty"}]}
    by_id = {c["song_id"]: c for c in candidate_set["candidates"]}
    exclude = exclude_song_ids or set()
    seen: set[str] = set()
    per_artist: dict[str, list[str]] = {}
    out_of_set = tail = 0
    for i, pick in enumerate(picks):
        if not isinstance(pick, dict):
            errors.append(f"pick {i}: not an object")
            continue
        sid = pick.get("song_id")
        if not isinstance(sid, str) or sid not in by_id:
            errors.append(f"pick {i}: song_id {sid!r} is not in candidate set")
            violations.append({"type": "out_of_set", "song_id": sid})
            out_of_set += 1
            continue
        if sid in seen:
            errors.append(f"pick {i}: duplicate song {sid}")
            violations.append({"type": "duplicate", "song_id": sid})
        seen.add(sid)
        if sid in exclude:
            errors.append(f"pick {i}: {sid} is excluded")
            violations.append({"type": "excluded", "song_id": sid})
        cand = by_id[sid]
        artist = (cand.get("artist_credit") or "").strip().lower()
        per_artist.setdefault(artist, []).append(sid)
        tail += cand["bucket"] == "tail"
        if not str(pick.get("reason") or "").strip():
            errors.append(f"pick {i}: empty reason")
            violations.append({"type": "empty_reason", "song_id": sid})
        refs = pick.get("evidence_refs")
        if (not isinstance(refs, list) or not refs
                or not all(isinstance(r, int) and not isinstance(r, bool) and 0 <= r < len(cand["evidence"])
                           for r in refs)):
            errors.append(f"pick {i}: evidence_refs must cite at least one valid evidence index")
            violations.append({"type": "bad_evidence_refs", "song_id": sid,
                               "valid_range": [0, len(cand["evidence"]) - 1]})
    if len(picks) != count:
        errors.append(f"expected {count} picks, got {len(picks)}")
        violations.append({"type": "count", "expected": count, "got": len(picks)})
    for artist, sids in per_artist.items():
        if len(sids) > max_per_artist:
            errors.append(f"artist {artist!r} appears {len(sids)} times (max {max_per_artist})")
            violations.append({"type": "artist_cap", "artist": artist, "song_ids": sids,
                               "max": max_per_artist, "remove": len(sids) - max_per_artist})
    if tail < min_tail:
        errors.append(f"only {tail} tail songs (min {min_tail})")
        violations.append({"type": "min_tail", "have": tail, "need": min_tail})
    return {"ok": not errors, "errors": errors, "violations": violations,
            "out_of_set": out_of_set, "tail": tail}

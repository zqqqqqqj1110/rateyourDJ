"""Offline metrics (metrics-v1). Pure functions, reusable as GRPO reward components.

``recs`` is an ordered list of song ids; ``positives`` a set of song ids;
``songs`` maps song_id -> SongProfileV2; ``sim(a, b)`` is a similarity in [-1, 1].
"""

from __future__ import annotations

import math
import re
from itertools import combinations
from typing import Any, Callable, Iterable

METRICS_VERSION = "metrics-v1"


def hits_at_k(recs: list[str], positives: set[str], k: int) -> int:
    return sum(1 for s in recs[:k] if s in positives)


def recall_at_k(recs: list[str], positives: set[str], k: int) -> float | None:
    """Capped recall: hits / min(k, |positives|); None when there are no positives."""
    if not positives:
        return None
    return hits_at_k(recs, positives, k) / min(k, len(positives))


def ndcg_at_k(recs: list[str], positives: set[str], k: int) -> float | None:
    if not positives:
        return None
    dcg = sum(1 / math.log2(i + 2) for i, s in enumerate(recs[:k]) if s in positives)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(positives))))
    return dcg / ideal if ideal else 0.0


def candidate_recall(candidates: Iterable[str], positives: set[str]) -> float | None:
    """Share of positives that made it into the candidate set (capped by its size)."""
    candidates = list(candidates)
    if not positives:
        return None
    return sum(1 for s in candidates if s in positives) / min(len(candidates), len(positives))


def tail_share(recs: list[str], songs: dict[str, Any]) -> float:
    return sum(songs[s]["popularity"]["bucket"] == "tail" for s in recs) / len(recs) if recs else 0.0


def novelty(recs: list[str], songs: dict[str, Any]) -> float:
    """Mean popularity self-information, normalised: 1 - global percentile (unknown -> 0.5)."""
    values = []
    for s in recs:
        pct = songs[s]["popularity"].get("global_percentile")
        values.append(0.5 if pct is None else 1 - float(pct))
    return sum(values) / len(values) if values else 0.0


def intra_list_diversity(recs: list[str], sim: Callable[[str, str], float] | None) -> float | None:
    if sim is None or len(recs) < 2:
        return None
    pairs = list(combinations(recs, 2))
    return sum(1 - sim(a, b) for a, b in pairs) / len(pairs)


def serendipity(recs: list[str], relevant: set[str], expected: set[str]) -> float:
    """Share of recs that are relevant AND not what an obvious baseline would recommend."""
    return sum(1 for s in recs if s in relevant and s not in expected) / len(recs) if recs else 0.0


def hallucination_rate(recs: list[str], allowed: set[str]) -> float:
    return sum(1 for s in recs if s not in allowed) / len(recs) if recs else 0.0


_NUMBER = re.compile(r"\d{2,}")


def evidence_accuracy(picks: list[dict[str, Any]], candidates: dict[str, dict[str, Any]]) -> float:
    """Share of picks whose evidence refs are valid AND whose numbers (years, counts,
    similarities) all appear in the cited evidence — a cheap check for invented facts."""
    if not picks:
        return 0.0
    ok = 0
    for p in picks:
        cand = candidates.get(p.get("song_id"))
        refs = p.get("evidence_refs") or []
        if cand is None or not refs or not all(isinstance(i, int) and 0 <= i < len(cand["evidence"]) for i in refs):
            continue
        cited = " ".join(cand["evidence"][i]["detail"] for i in refs)
        if all(n in cited for n in _NUMBER.findall(str(p.get("reason") or ""))):
            ok += 1
    return ok / len(picks)


def coverage(all_recs: Iterable[list[str]], songs: dict[str, Any]) -> dict[str, float]:
    recs = {s for rs in all_recs for s in rs}
    tails = {s for s, v in songs.items() if v["popularity"]["bucket"] == "tail"}
    artists_all = {v.get("artist_credit") for v in songs.values()}
    return {
        "catalog_coverage": len(recs) / len(songs) if songs else 0.0,
        "tail_coverage": len(recs & tails) / len(tails) if tails else 0.0,
        "artist_coverage": len({songs[s].get("artist_credit") for s in recs}) / len(artists_all) if artists_all else 0.0,
        "unique_songs": len(recs),
    }

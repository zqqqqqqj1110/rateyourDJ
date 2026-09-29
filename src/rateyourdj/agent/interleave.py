"""Team-draft interleaving (Radlinski, Kurup & Joachims, CIKM 2008).

Two strategies ("arms") each produce a ranked list for the same request. The
shown list is built in rounds: a fair coin decides which arm drafts first, then
each arm adds its highest-ranked song that is not already shown. Every shown
song is credited to the arm that drafted it, so feedback on that song counts for
that arm. Because the coin is flipped every round, neither arm owns the top
slots, and the viewer cannot tell which song came from which arm.

Arm names are also randomised to the labels "A"/"B" per request, so the label
carries no information about the strategy.
"""

from __future__ import annotations

import random
from typing import Any, Sequence

INTERLEAVING_VERSION = "team-draft-v1"


def team_draft(lists: dict[str, Sequence[str]], count: int, rng: random.Random) -> list[tuple[str, str]]:
    """Return [(song_id, arm), ...] of length <= count.

    ``lists`` maps arm label -> ranked song ids (exactly two arms). Stops early
    only if both arms run out of unshown songs.
    """
    if len(lists) != 2:
        raise ValueError("team-draft interleaving needs exactly two arms")
    arms = sorted(lists)
    shown: list[tuple[str, str]] = []
    seen: set[str] = set()
    picks = {arm: 0 for arm in arms}
    cursor = {arm: 0 for arm in arms}

    def next_song(arm: str) -> str | None:
        ranked = lists[arm]
        while cursor[arm] < len(ranked) and ranked[cursor[arm]] in seen:
            cursor[arm] += 1
        return ranked[cursor[arm]] if cursor[arm] < len(ranked) else None

    while len(shown) < count:
        a, b = arms
        # the arm with fewer picks drafts next; ties are broken by a coin flip
        if picks[a] != picks[b]:
            order = [a, b] if picks[a] < picks[b] else [b, a]
        else:
            order = [a, b] if rng.random() < 0.5 else [b, a]
        progressed = False
        for arm in order:
            if len(shown) >= count:
                break
            song = next_song(arm)
            if song is None:
                continue
            shown.append((song, arm))
            seen.add(song)
            picks[arm] += 1
            progressed = True
            break  # one draft per decision, then re-evaluate who is behind
        if not progressed:
            break
    return shown


def credit(shown: Sequence[tuple[str, str]], wins: dict[str, float]) -> dict[str, float]:
    """Sum per-arm credit for one interleaved list. ``wins`` maps song_id -> score."""
    out: dict[str, float] = {}
    for song, arm in shown:
        out[arm] = out.get(arm, 0.0) + float(wins.get(song, 0.0))
    return out


def assign_labels(strategies: Sequence[str], rng: random.Random) -> dict[str, str]:
    """Randomly map labels A/B to the two strategies."""
    if len(strategies) != 2 or strategies[0] == strategies[1]:
        raise ValueError("interleaving needs two different strategies")
    pair = list(strategies)
    rng.shuffle(pair)
    return {"A": pair[0], "B": pair[1]}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Count shown songs per strategy from impression records."""
    out: dict[str, int] = {}
    for r in records:
        inter = r.get("interleaving") or {}
        strategy = (inter.get("arms") or {}).get(inter.get("arm"))
        if strategy:
            out[strategy] = out.get(strategy, 0) + 1
    return out

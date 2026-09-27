"""Deterministic rankers over a stage 2 candidate set.

Components (all in [0, 1]):
  relevance        hybrid relevance percentile from retrieval
  tail_score       1 - global popularity percentile
  tail_relevance   relevance * tail_score
  novelty          0.5 * (artist is not a seed artist) + 0.5 * (1 - similarity to nearest seed)
  diversity        1 - max similarity to songs already picked (computed greedily)
  exploration_fit  1 - |tail_score - exploration_level|
Penalties: same artist already picked, heard before, near-duplicate (sim > 0.97).

Strategies:
  rag-rel-v1      relevance only (hard artist cap; heard / near-duplicate penalties only)
  rag-tailmix-v1  fixed slot mix: relevant / long-tail / exploration slots, sized by the
                  exploration level with lower/upper bounds; each slot filled greedily (MMR)

Same inputs -> same output. Every pick carries a full ``score_breakdown`` with the
weights version, so results can be recomputed exactly.
"""

from __future__ import annotations

import math
from typing import Any, Callable

WEIGHTS_VERSION = "rank-w1"
STRATEGIES = ("rag-rel-v1", "rag-tailmix-v1")
WEIGHTS = {
    "mmr_lambda": 0.2,            # diversity bonus inside a slot
    "same_artist_penalty": 0.3,
    "heard_penalty": 1.0,
    "near_duplicate_penalty": 0.5,
    "near_duplicate_sim": 0.97,
    "display_relevance": 0.6,     # final display order: 0.6 relevance + 0.4 tail relevance
}
Similarity = Callable[[str, str], float]


def slot_plan(count: int, exploration: float) -> dict[str, int]:
    """Slots for rag-tailmix-v1. count=10, e=0.5 -> 6 relevant / 3 tail / 1 explore.

    Bounds: tail in [1, 50%], explore in [0, 20%], relevant >= 30%.
    """
    e = min(1.0, max(0.0, exploration))
    tail = min(max(1, round(count * (0.1 + 0.4 * e))), math.ceil(count * 0.5))
    explore = min(round(count * 0.2 * e), math.floor(count * 0.2))
    relevant = count - tail - explore
    floor = math.ceil(count * 0.3)
    if relevant < floor:
        explore = max(0, explore - (floor - relevant))
        relevant = count - tail - explore
    return {"relevant": relevant, "tail": tail, "explore": explore}


def _artist(c: dict[str, Any]) -> str:
    return (c.get("artist_credit") or "").strip().lower()


def rank_candidates(candidate_set: dict[str, Any], context: dict[str, Any], *,
                    strategy: str = "rag-tailmix-v1", count: int = 10,
                    exploration_level: float | None = None, max_per_artist: int = 2,
                    similarity: Similarity | None = None,
                    seed_artists: set[str] | None = None) -> dict[str, Any]:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; known: {STRATEGIES}")
    cands = list(candidate_set["candidates"])
    e = (candidate_set.get("query", {}).get("exploration_level")
         if exploration_level is None else exploration_level)
    e = context.get("exploration_level", 0.5) if e is None else e
    heard = set(context.get("heard_song_ids", []))
    seeds = [sid for b in context.get("seed_branches", []) for sid in b["seed_song_ids"]]
    seed_artists = {a.lower() for a in (seed_artists or set())}
    sim = similarity or (lambda a, b: 0.0)

    base: dict[str, dict[str, float]] = {}
    for c in cands:
        seed_sim = max((sim(c["song_id"], s) for s in seeds), default=0.0)
        base[c["song_id"]] = {
            "relevance": float(c["relevance"]),
            "tail_score": float(c["tail_score"]),
            "tail_relevance": round(float(c["relevance"]) * float(c["tail_score"]), 4),
            "novelty": round(0.5 * (_artist(c) not in seed_artists) + 0.5 * (1 - max(0.0, seed_sim)), 4),
            "exploration_fit": round(1 - abs(float(c["tail_score"]) - e), 4),
            "seed_similarity": round(seed_sim, 4),
        }

    if strategy == "rag-rel-v1":
        plan = {"relevant": count, "tail": 0, "explore": 0}
    else:
        plan = slot_plan(count, e)

    def slot_ok(slot: str, c: dict[str, Any]) -> bool:
        if slot == "tail":
            return c["bucket"] == "tail"
        if slot == "explore":
            return any(ch["channel"] == "explore" for ch in c["channels"]) or c["bucket"] in ("mid", "tail")
        return True

    def slot_base(slot: str, f: dict[str, float]) -> float:
        if slot == "tail":
            return f["tail_relevance"]
        if slot == "explore":
            return 0.5 * f["exploration_fit"] + 0.3 * f["novelty"] + 0.2 * f["relevance"]
        return f["relevance"]

    picked: list[dict[str, Any]] = []
    per_artist: dict[str, int] = {}
    order = ["tail", "explore", "relevant"] if strategy != "rag-rel-v1" else ["relevant"]
    use_mmr = strategy != "rag-rel-v1"
    for slot in order:
        for _ in range(plan[slot]):
            best, best_score, best_detail = None, -math.inf, None
            for c in cands:
                sid = c["song_id"]
                if any(p["song_id"] == sid for p in picked) or not slot_ok(slot, c):
                    continue
                if per_artist.get(_artist(c), 0) >= max_per_artist:
                    continue
                f = base[sid]
                max_sim = max((sim(sid, p["song_id"]) for p in picked), default=0.0)
                diversity = 1 - max(0.0, max_sim)
                pen = {
                    # soft artist penalty only for the mix; the baseline keeps just the hard cap
                    "same_artist": (WEIGHTS["same_artist_penalty"] * per_artist.get(_artist(c), 0)
                                    if use_mmr else 0.0),
                    "heard": WEIGHTS["heard_penalty"] if sid in heard else 0.0,
                    "near_duplicate": WEIGHTS["near_duplicate_penalty"]
                    if max(max_sim, f["seed_similarity"]) > WEIGHTS["near_duplicate_sim"] else 0.0,
                }
                score = slot_base(slot, f) + (WEIGHTS["mmr_lambda"] * diversity if use_mmr else 0.0) \
                    - sum(pen.values())
                if score > best_score + 1e-12:
                    best, best_score = c, score
                    best_detail = {"diversity": round(diversity, 4), "penalties": pen}
            if best is None:
                break
            picked.append({"song_id": best["song_id"], "slot": slot, "slot_score": round(best_score, 4),
                           "candidate": best, **best_detail})
            per_artist[_artist(best)] = per_artist.get(_artist(best), 0) + 1

    if len(picked) < count and strategy != "rag-rel-v1":  # slots ran dry: top up with relevance
        extra = rank_candidates({**candidate_set, "candidates": [c for c in cands if c["song_id"]
                                                                not in {p["song_id"] for p in picked}]},
                                context, strategy="rag-rel-v1", count=count - len(picked),
                                exploration_level=e, max_per_artist=max_per_artist,
                                similarity=similarity, seed_artists=seed_artists)
        for r in extra["ranked"]:
            if per_artist.get(_artist(r["candidate"]), 0) < max_per_artist:
                picked.append({"song_id": r["song_id"], "slot": "relevant", "slot_score": r["score"],
                               "candidate": r["candidate"], "diversity": 0.0,
                               "penalties": r["score_breakdown"]["penalties"]})
                per_artist[_artist(r["candidate"])] = per_artist.get(_artist(r["candidate"]), 0) + 1

    w = WEIGHTS["display_relevance"]
    for p in picked:
        f = base[p["song_id"]]
        p["display_score"] = round(w * f["relevance"] + (1 - w) * f["tail_relevance"], 4)
    if strategy != "rag-rel-v1":
        picked.sort(key=lambda p: (-p["display_score"], p["song_id"]))

    ranked = []
    for i, p in enumerate(picked, 1):
        c = p["candidate"]
        ranked.append({
            "rank": i,
            "song_id": c["song_id"],
            "title": c.get("title"),
            "artist_credit": c.get("artist_credit"),
            "bucket": c["bucket"],
            "channel": c["channels"][0]["channel"],
            "score": p["slot_score"],
            "reason": default_reason(c),
            "evidence_refs": list(range(min(2, len(c["evidence"])))) or [0],
            "score_breakdown": {**base[p["song_id"]], "diversity": p["diversity"],
                                "penalties": p["penalties"], "slot": p["slot"],
                                "display_score": p["display_score"], "strategy": strategy,
                                "weights_version": WEIGHTS_VERSION},
            "candidate": c,
        })
    return {"strategy": strategy, "weights_version": WEIGHTS_VERSION, "exploration_level": e,
            "plan": plan, "candidate_set_id": candidate_set["candidate_set_id"], "ranked": ranked}


def default_reason(candidate: dict[str, Any]) -> str:
    """Template reason built only from the candidate's own evidence (used by the oracle)."""
    parts = [ev["detail"] for ev in candidate["evidence"][:2]]
    return "；".join(parts) if parts else "来自候选集的相关歌曲"

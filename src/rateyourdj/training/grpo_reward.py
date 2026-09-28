"""Programmatic GRPO reward for the selection step (``reward-v1``).

The policy's completion is the final ``submit_recommendations`` call. The reward is computed
only from the candidate set, the request constraints and catalog/embedding facts stored in the
GRPO sample. It never looks at hidden positives (eval_only) or at the oracle's picks.

    parse failure / wrong tool / wrong candidate set      -> -1.0
    parsed but violates a hard constraint (validator)     -> -0.5
    otherwise R in [0, 1] = weighted sum of components:

  relevance      mean relevance of the picks, scaled between the n least and n most relevant candidates
  tail_relevance mean relevance x tail score, scaled the same way
  tail_band      number of tail picks inside [min_tail, upper(exploration)]; decays linearly outside
  diversity      0.5 distinct-artist share + 0.5 embedding spread relative to the candidate set
  novelty        0.5 non-seed-artist + 0.5 (1 - similarity to the nearest seed)
  evidence       reasons cite valid evidence whose numbers support them, are short and non-empty;
                 the message's tail count matches the picks
Weights depend on the exploration level e: relevance 0.40 - 0.15e, tail_relevance 0.10 + 0.15e,
tail_band 0.15, diversity 0.10, novelty 0.05, evidence 0.20 (sum 1). A length penalty (up to 0.2)
applies beyond 400 + 200 n characters (oracle completions are about 250 + 130 n).
"""

from __future__ import annotations

import json
import re
from typing import Any

from rateyourdj.ranking import validate_selection

REWARD_VERSION = "reward-v1"
NUM = re.compile(r"\d+(?:\.\d+)?")
TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*\})\s*</tool_call>", re.S)
MAX_REASON_CHARS = 80


def tail_band(count: int, exploration: float, min_tail: int) -> tuple[int, int]:
    upper = max(min_tail, round(count * (0.2 + 0.6 * exploration)))
    return min_tail, min(count, upper)


def parse_completion(text: str) -> dict[str, Any] | None:
    """Arguments of the submit_recommendations call in a completion, or None."""
    if not isinstance(text, str):
        return None
    m = TOOL_CALL.search(text)
    raw = m.group(1) if m else text.strip()
    if not m:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start:end + 1]
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "name" in obj:
        if obj.get("name") != "submit_recommendations":
            return None
        args = obj.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return None
        obj = args
    return obj if isinstance(obj, dict) and isinstance(obj.get("picks"), list) else None


def _scaled(value: float, values: list[float], n: int) -> float:
    ordered = sorted(values)
    lo = sum(ordered[:n]) / n
    hi = sum(ordered[-n:]) / n
    return 1.0 if hi - lo < 1e-9 else min(1.0, max(0.0, (value - lo) / (hi - lo)))


def score(completion: str, sample: dict[str, Any]) -> dict[str, Any]:
    """Reward and its components for one completion of one GRPO sample."""
    cons = sample["constraints"]
    cands = sample["candidates"]
    by_id = {c["song_id"]: i for i, c in enumerate(cands)}
    n, e = int(cons["count"]), float(cons["exploration_level"])
    out: dict[str, Any] = {"reward_version": REWARD_VERSION, "valid": False}
    args = parse_completion(completion)
    if args is None or args.get("candidate_set_id") != cons["candidate_set_id"]:
        out.update(reward=-1.0, error="unparsable completion or wrong candidate set")
        return out
    candidate_set = {"candidates": [{"song_id": c["song_id"], "artist_credit": c["artist"], "bucket": c["bucket"],
                                     "evidence": [{"detail": d} for d in c["evidence"]]} for c in cands]}
    banned = {a.strip().lower() for a in cons.get("exclude_artists", [])}
    excluded = {c["song_id"] for c in cands if c["artist"].strip().lower() in banned}
    report = validate_selection(args["picks"], candidate_set, count=n, max_per_artist=int(cons["max_per_artist"]),
                                min_tail=int(cons["min_tail"]), exclude_song_ids=excluded)
    if not report["ok"]:
        out.update(reward=-0.5, error="; ".join(report["errors"][:3]),
                   violations=[v["type"] for v in report["violations"]])
        return out
    picks = args["picks"]
    idx = [by_id[p["song_id"]] for p in picks]
    rel = [cands[i]["relevance"] for i in idx]
    all_rel = [c["relevance"] for c in cands]
    tr = [cands[i]["relevance"] * cands[i]["tail_score"] for i in idx]
    all_tr = [c["relevance"] * c["tail_score"] for c in cands]
    comp: dict[str, float] = {
        "relevance": _scaled(sum(rel) / n, all_rel, n),
        "tail_relevance": _scaled(sum(tr) / n, all_tr, n),
    }
    tails = sum(cands[i]["bucket"] == "tail" for i in idx)
    lo, hi = tail_band(n, e, int(cons["min_tail"]))
    miss = max(0, lo - tails, tails - hi)
    comp["tail_band"] = max(0.0, 1.0 - miss / max(1.0, 0.3 * n))
    sim = sample["sim"]
    pairs = [(a, b) for k, a in enumerate(idx) for b in idx[k + 1:]]
    pick_sim = sum(sim[a][b] for a, b in pairs) / max(1, len(pairs))
    m = len(cands)
    cand_sim = sum(sim[a][b] for a in range(m) for b in range(a + 1, m)) / max(1, m * (m - 1) // 2)
    artists = {cands[i]["artist"].strip().lower() for i in idx}
    comp["diversity"] = 0.5 * len(artists) / n + 0.5 * min(1.0, max(0.0, 0.5 + (cand_sim - pick_sim) * 5))
    comp["novelty"] = sum(0.5 * (not cands[i]["seed_artist"]) + 0.5 * (1 - max(0.0, cands[i]["seed_similarity"]))
                          for i in idx) / n
    reasons_ok = 0
    for p, i in zip(picks, idx):
        reason = str(p.get("reason") or "").strip()
        refs = p.get("evidence_refs") or []
        cited = " ".join(cands[i]["evidence"][r] for r in refs if isinstance(r, int) and 0 <= r < len(cands[i]["evidence"]))
        reasons_ok += bool(reason) and len(reason) <= MAX_REASON_CHARS and bool(cited) \
            and set(NUM.findall(reason)) <= set(NUM.findall(cited))
    message = str(args.get("message") or "")
    claims = [int(x) for x in re.findall(r"(\d+) ?首(?:是听众较少的)?(?:冷门| tail|是听众较少)", message)]
    message_ok = bool(message.strip()) and all(c == tails for c in claims) and len(message) <= 200
    comp["evidence"] = 0.8 * reasons_ok / n + 0.2 * message_ok
    weights = {"relevance": 0.40 - 0.15 * e, "tail_relevance": 0.10 + 0.15 * e, "tail_band": 0.15,
               "diversity": 0.10, "novelty": 0.05, "evidence": 0.20}
    reward = sum(weights[k] * comp[k] for k in weights)
    budget = 400 + 200 * n          # oracle completions: ~250 + 130 n characters
    over = max(0, len(completion) - budget) / budget
    length_penalty = min(0.2, 0.2 * over)
    out.update(valid=True, reward=round(reward - length_penalty, 4), components={k: round(v, 4) for k, v in comp.items()},
               weights={k: round(v, 3) for k, v in weights.items()}, tails=tails, tail_band=[lo, hi],
               length_penalty=round(length_penalty, 4))
    return out


def reward_fn_factory(samples_by_id: dict[str, dict[str, Any]]):
    """TRL reward function: rewards for completions, looked up by the ``sample_id`` column."""
    def rateyourdj_reward(prompts, completions, sample_id, **kwargs) -> list[float]:
        out = []
        for completion, sid in zip(completions, sample_id):
            text = completion if isinstance(completion, str) else (completion[0].get("content") or "")
            out.append(float(score(text, samples_by_id[sid])["reward"]))
        return out
    return rateyourdj_reward

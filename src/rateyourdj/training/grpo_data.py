"""GRPO samples (grpo-sample/v2) for the selection step (stage 5).

A GRPO prompt is an SFT trajectory cut right after the ``retrieve_candidates`` observation:
the policy has to write the final ``submit_recommendations`` call itself. Nothing in the prompt
reveals a correct answer. Each sample also carries what the reward needs (candidate facts, a
candidate x candidate embedding-similarity matrix, the constraints) and, under ``eval_only``,
what only evaluation may use (hidden positives, weak positives, the oracle's picks).

    train  new trajectories from the SFT generator (another seed), train split only
    val    sft-v1 val (held-out phrasing / artists): checkpoint selection
    test   sft-v1 test, first 200 (the frozen stage 4 test set, incl. the 13 fixed queries)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rateyourdj.contracts import validate_record
from rateyourdj.contracts.v2 import SCHEMA_VERSIONS, strip_eval_only

from .grpo_reward import REWARD_VERSION, tail_band

SIM = re.compile(r"向量相似度 ([0-9.]+)")


def cut_prompt(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """(prompt messages up to the retrieve observation, retrieve arguments, retrieve observation)."""
    for i, m in enumerate(messages):
        if m["role"] == "tool" and m.get("name") == "retrieve_candidates":
            call = next(c for c in messages[i - 1]["tool_calls"] if c["id"] == m["tool_call_id"])
            prompt = [{k: v for k, v in x.items() if k != "weight"} for x in messages[: i + 1]]
            return prompt, json.loads(call["function"]["arguments"]), json.loads(m["content"])
    raise ValueError("sample has no retrieve_candidates observation")


def final_picks(messages: list[dict[str, Any]]) -> list[str]:
    args = json.loads(messages[-1]["tool_calls"][0]["function"]["arguments"])
    return [p["song_id"] for p in args["picks"]]


def positives_for_branches(positives: dict[str, Any] | None, branches: list[str]) -> list[str]:
    if not positives:
        return []
    names = branches or list(positives["branches"])
    return list(dict.fromkeys(s for b in names for s in positives["branches"].get(b, {}).get("song_ids", [])))


def convert(sample: dict[str, Any], *, split: str, index: Any, seed_artists: set[str],
            hidden: dict[str, Any] | None = None, weak: dict[str, Any] | None = None) -> dict[str, Any]:
    prompt, r_args, obs = cut_prompt(sample["messages"])
    meta = sample["meta"]
    data = obs["data"]
    cands = []
    for c in data["candidates"]:
        evidence = [e.split(": ", 1)[1] if ": " in e else e for e in c["evidence"]]
        m = SIM.search(evidence[0]) if evidence else None
        cands.append({"song_id": c["song_id"], "title": c.get("title"), "artist": c.get("artist") or "",
                      "bucket": c["bucket"], "relevance": c["relevance"], "tail_score": c["tail_score"],
                      "channel": c["channel"], "evidence": evidence,
                      "seed_similarity": float(m.group(1)) if m else 0.0,
                      "seed_artist": (c.get("artist") or "") in seed_artists})
    rows = [index.row[c["song_id"]] for c in cands]
    vecs = index.matrix[rows]
    sim = (vecs @ vecs.T).round(3).tolist()
    count, e = meta["count"], meta["exploration_level"]
    out = {
        "schema_version": SCHEMA_VERSIONS["grpo_sample"],
        "sample_id": "grpo_" + sample["sample_id"][4:],
        "prompt": prompt,
        "tools": sample["tools"],
        "candidates": cands,
        "sim": sim,
        "constraints": {"count": count, "max_per_artist": 2, "min_tail": meta["min_tail"],
                        "exploration_level": e, "tail_band": list(tail_band(count, e, meta["min_tail"])),
                        "exclude_artists": list(r_args.get("exclude_artists") or []),
                        "candidate_set_id": data["candidate_set_id"]},
        "reward_spec_version": REWARD_VERSION,
        "meta": {**{k: meta.get(k) for k in ("template_id", "mode", "lang", "branch_hint", "branches", "count",
                                             "exploration_level", "exclude_artists", "history", "fixed_query_id",
                                             "text")},
                 "source_sample_id": sample["sample_id"], "split": split},
        "eval_only": {
            "hidden_positives": positives_for_branches(hidden, meta.get("branches") or []),
            "hidden_positives_version": hidden.get("version") if hidden else None,
            "weak_positives": positives_for_branches(weak, meta.get("branches") or []),
            "oracle_picks": final_picks(sample["messages"]),
            "oracle_arguments": json.loads(sample["messages"][-1]["tool_calls"][0]["function"]["arguments"]),
            "oracle_strategy": meta.get("oracle_strategy"),
        },
    }
    validate_record("grpo_sample", out)
    return out


def load_jsonl(path: str | Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
                if limit and len(rows) >= limit:
                    break
    return rows


def training_view(sample: dict[str, Any]) -> dict[str, Any]:
    """What the trainer may see: eval_only removed (enforced by tests)."""
    return strip_eval_only(sample)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import random

    from rateyourdj.data_pipeline.catalog import read_jsonl
    from rateyourdj.data_pipeline.user_context import load_user_context
    from rateyourdj.rag.index import load_index

    parser = argparse.ArgumentParser(prog="python -m rateyourdj.training.grpo_data")
    parser.add_argument("--sft-train", default="data/training/sft/grpo-src-v1/train.jsonl",
                        help="SFT-format trajectories generated with another seed (train split used)")
    parser.add_argument("--sft-dir", default="data/training/sft/sft-v1", help="frozen val/test source")
    parser.add_argument("--out", default="data/training/grpo/grpo-v1")
    parser.add_argument("--train-limit", type=int, default=800)
    parser.add_argument("--val-limit", type=int, default=100)
    parser.add_argument("--test-limit", type=int, default=200)
    parser.add_argument("--hidden", default="eval/hidden_positives_v1.json")
    parser.add_argument("--weak", default="eval/weak_positives_v1.json")
    parser.add_argument("--seed", type=int, default=20260928)
    args = parser.parse_args(argv)

    songs = read_jsonl("data/catalog/processed/songs.jsonl")
    by_id = {s["song_id"]: s for s in songs}
    context = load_user_context("participant_001", "data/users", legacy_root=None)
    seed_artists = {by_id[s].get("artist_credit") or "" for b in context["seed_branches"] for s in b["seed_song_ids"]}
    index = load_index("data/index")
    hidden = json.loads(Path(args.hidden).read_text("utf-8")) if Path(args.hidden).is_file() else None
    weak = json.loads(Path(args.weak).read_text("utf-8")) if Path(args.weak).is_file() else None
    if hidden is None:
        print(f"warning: {args.hidden} missing - hidden positives will be empty")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    sources = {
        "train": [s for s in load_jsonl(args.sft_train) if s["meta"]["split"] == "train"],
        "val": load_jsonl(Path(args.sft_dir) / "val.jsonl", args.val_limit),
        "test": load_jsonl(Path(args.sft_dir) / "test.jsonl", args.test_limit),
    }
    rng.shuffle(sources["train"])
    sources["train"] = sources["train"][: args.train_limit]
    stats = {}
    for split, rows in sources.items():
        converted = [convert(s, split=split, index=index, seed_artists=seed_artists, hidden=hidden, weak=weak)
                     for s in rows]
        with (out / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for r in converted:
                handle.write(json.dumps(r, ensure_ascii=False) + "\n")
        hp = [len(set(r["eval_only"]["hidden_positives"]) & {c["song_id"] for c in r["candidates"]}) for r in converted]
        stats[split] = {"n": len(converted),
                        "candidates_mean": round(sum(len(r["candidates"]) for r in converted) / max(1, len(converted)), 1),
                        "hidden_positives_in_candidates_mean": round(sum(hp) / max(1, len(hp)), 2),
                        "with_hidden_positive_in_candidates": sum(x > 0 for x in hp)}
    manifest = {"version": "grpo-v1", "reward_spec_version": REWARD_VERSION,
                "hidden_positives": hidden.get("version") if hidden else None, "sources": {
                    "train": args.sft_train, "val": str(Path(args.sft_dir) / "val.jsonl"),
                    "test": str(Path(args.sft_dir) / "test.jsonl")}, "stats": stats}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Live evaluation of an agent model on SFT val/test scenarios (stage 4).

For every sample the exact tool environment is rebuilt (same user context, session
history and candidate retrieval), the model runs the real ReAct loop (agent.loop.run_agent)
against an OpenAI-compatible endpoint (vLLM serving the base model or the LoRA adapter,
or DeepSeek), and the run is compared with the oracle trajectory stored in the sample.

Metrics (per sample, then averaged):
  completed          the model's own submission passed validation (no deterministic fallback)
  first_try          ... without any repair round
  malformed_calls    tool calls with unparsable arguments or unknown tools
  out_of_set         any submitted song not in the candidate set (must stay 0)
  args_*             retrieve_candidates arguments vs the oracle: branch_hint, exploration_level,
                     exclude_artists, empty-vs-non-empty query, get_user_context first
  pick_overlap       Jaccard overlap of the final picks with the oracle picks
  tail_ok            at least min_tail tail songs
  evidence_ok        share of reasons whose numbers all come from the cited evidence
"""

from __future__ import annotations

import copy
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rateyourdj.agent.loop import AGENT_STRATEGY, run_agent
from rateyourdj.agent.tools import Toolbox

from .sft_data import MAX_PER_ARTIST, reason_numbers_ok


def oracle_view(sample: dict[str, Any]) -> dict[str, Any]:
    calls = [c for m in sample["messages"] if m["role"] == "assistant" and m.get("weight", 1.0) != 0
             for c in m.get("tool_calls") or []]
    names = [c["function"]["name"] for c in calls]
    retrieve = next(json.loads(c["function"]["arguments"]) for c in calls
                    if c["function"]["name"] == "retrieve_candidates")
    final = json.loads(calls[-1]["function"]["arguments"])
    return {"tools": names, "retrieve": retrieve, "picks": [p["song_id"] for p in final["picks"]]}


def evaluate_sample(llm: Any, sample: dict[str, Any], *, songs_by_id: dict[str, Any], base_context: dict[str, Any],
                    retriever: Any, similarity: Any, seed_artists: set[str],
                    heldout_song_ids: list[str] | None = None) -> dict[str, Any]:
    meta = sample["meta"]
    ctx = copy.deepcopy(base_context)
    for k in ("recommended_song_ids", "heard_song_ids", "recent_feedback_ids"):
        ctx[k] = []
    ctx["exploration_level"] = meta["exploration_level"]
    ctx["recommended_song_ids"] = list(meta.get("context", {}).get("recommended_song_ids", []))
    if meta.get("context", {}).get("heldout_artists_excluded") and heldout_song_ids:
        ctx.setdefault("exclusions", {}).setdefault("song_ids", [])
        ctx["exclusions"]["song_ids"] = sorted(set(ctx["exclusions"]["song_ids"]) | set(heldout_song_ids))
    retriever.context = ctx
    tb = Toolbox(songs_by_id, ctx, retriever, similarity=similarity, seed_artists=seed_artists,
                 count=meta["count"], max_per_artist=MAX_PER_ARTIST)
    run = run_agent(llm, tb, request_text=meta["text"], count=meta["count"],
                    exploration_level=meta["exploration_level"], branch_hint=meta.get("ui_branch_hint"))
    oracle = oracle_view(sample)
    steps = run["steps"]
    tools = [s["tool"] for s in steps if s["tool"] not in (None, "fallback")]
    first_retrieve = next((s for s in steps if s["tool"] == "retrieve_candidates" and s["status"] != "error"), None)
    completed = run["strategy_version"] == AGENT_STRATEGY
    out = {
        "sample_id": sample["sample_id"], "split": meta["split"], "template_id": meta["template_id"],
        "mode": meta["mode"], "lang": meta["lang"], "exploration_level": meta["exploration_level"],
        "count": meta["count"], "fixed_query_id": meta.get("fixed_query_id"),
        "completed": completed, "first_try": completed and run.get("repairs", 0) == 0,
        "repairs": run.get("repairs", 0), "fallback_reason": run["fallback_reason"],
        "steps": len(steps), "latency_ms": run["latency_ms"],
        "malformed_calls": sum(1 for s in steps if any("bad arguments" in d or "unknown tool" in d
                                                         for d in s.get("diagnostics", []))),
        "no_tool_call_turns": sum(1 for s in steps if s["status"] == "no_tool_call"),
        "out_of_set": sum((s.get("validation") or {}).get("out_of_set", 0) for s in steps),
        # violation types of every submission, e.g. [["count", "min_tail"], []]
        "violations": [[v.get("type") for v in (s.get("validation") or {}).get("violations", [])]
                       for s in steps if s["tool"] == "submit_recommendations"],
        "context_first_ok": (tools[:1] == ["get_user_context"]) == (oracle["tools"][:1] == ["get_user_context"]),
    }
    o = oracle["retrieve"]
    if first_retrieve is not None:
        a = first_retrieve.get("arguments", {})
        out.update({
            "args_branch_ok": (a.get("branch_hint") or None) == (o.get("branch_hint") or None),
            "args_exploration_ok": a.get("exploration_level") is not None
            and abs(float(a["exploration_level"]) - float(o["exploration_level"])) < 1e-6,
            "args_exclude_ok": {x.strip().lower() for x in a.get("exclude_artists") or []}
            == {x.strip().lower() for x in o.get("exclude_artists") or []},
            "args_query_empty_ok": (not str(a.get("query") or "").strip()) == (not o.get("query")),
            "query": a.get("query"),
        })
    else:
        out.update({"args_branch_ok": False, "args_exploration_ok": False, "args_exclude_ok": False,
                    "args_query_empty_ok": False, "query": None})
    if completed:
        cs = run["candidate_set"]
        by_id = {c["song_id"]: c for c in cs["candidates"]}
        picks = run["picks"]
        chosen = {p["song_id"] for p in picks}
        oracle_set = set(oracle["picks"])
        tail = sum(by_id[p["song_id"]]["bucket"] == "tail" for p in picks if p["song_id"] in by_id)
        out.update({
            "pick_overlap": round(len(chosen & oracle_set) / max(1, len(chosen | oracle_set)), 4),
            "tail": tail, "tail_ok": tail >= run["request"]["min_tail"],
            "evidence_ok": round(sum(reason_numbers_ok(p["reason"], by_id[p["song_id"]], p.get("evidence_refs") or [])
                                     for p in picks if p["song_id"] in by_id) / max(1, len(picks)), 4),
            "picks": [p["song_id"] for p in picks], "message": run["message"],
        })
    return out


BOOL_METRICS = ("completed", "first_try", "context_first_ok", "args_branch_ok", "args_exploration_ok",
                "args_exclude_ok", "args_query_empty_ok", "tail_ok")
MEAN_METRICS = ("repairs", "steps", "malformed_calls", "no_tool_call_turns", "out_of_set", "latency_ms",
                "pick_overlap", "evidence_ok")


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def summary(rs: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {"n": len(rs)}
        for k in BOOL_METRICS:
            vals = [r[k] for r in rs if k in r]
            out[k] = round(sum(vals) / len(vals), 4) if vals else None
        for k in MEAN_METRICS:
            vals = [r[k] for r in rs if r.get(k) is not None]
            out[k] = round(sum(vals) / len(vals), 4) if vals else None
        out["out_of_set_rate"] = round(sum(r["out_of_set"] > 0 for r in rs) / len(rs), 4) if rs else None
        first = Counter(t for r in rs for t in set((r.get("violations") or [[]])[0]))
        out["first_submit_violations"] = dict(first.most_common())
        return out
    groups: dict[str, dict[str, list]] = {"mode": defaultdict(list), "lang": defaultdict(list),
                                          "exploration_level": defaultdict(list), "fixed": defaultdict(list)}
    for r in rows:
        groups["mode"][r["mode"]].append(r)
        groups["lang"][r["lang"]].append(r)
        groups["exploration_level"][str(r["exploration_level"])].append(r)
        groups["fixed"]["fixed" if r["fixed_query_id"] else "generated"].append(r)
    return {"overall": summary(rows),
            "by": {g: {k: summary(v) for k, v in sorted(d.items())} for g, d in groups.items()}}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from rateyourdj.agent.factory import vector_similarity
    from rateyourdj.agent.llm import OpenAICompatibleChat
    from rateyourdj.config import load_dotenv
    from rateyourdj.data_pipeline.catalog import read_jsonl
    from rateyourdj.data_pipeline.user_context import load_user_context
    from rateyourdj.experiment import build_run_manifest, write_run_manifest
    from rateyourdj.rag.encoder import SentenceTransformerEncoder
    from rateyourdj.rag.index import load_index
    from rateyourdj.rag.retrieval import Retriever

    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m rateyourdj.training.sft_eval")
    parser.add_argument("--data", default="data/training/sft/sft-v1-smoke/test.jsonl")
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--model", required=True, help="served model name, e.g. rateyourdj (LoRA) or the base model id")
    parser.add_argument("--api-key", default="EMPTY")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--user-id", default="participant_001")
    parser.add_argument("--catalog-root", default="data/catalog")
    parser.add_argument("--users-root", default="data/users")
    parser.add_argument("--index-root", default="data/index")
    args = parser.parse_args(argv)

    songs = read_jsonl(Path(args.catalog_root) / "processed" / "songs.jsonl")
    songs_by_id = {s["song_id"]: s for s in songs}
    context = load_user_context(args.user_id, args.users_root, legacy_root=None)
    index = load_index(args.index_root)
    if index is None:
        print("需要向量索引", file=sys.stderr)
        return 2
    manifest_path = Path(args.catalog_root) / "manifest.json"
    catalog_version = json.loads(manifest_path.read_text("utf-8"))["catalog_version"] if manifest_path.is_file() else "unknown"
    retriever = Retriever(songs, context, index=index, encoder=SentenceTransformerEncoder(index.manifest["model"]),
                          catalog_version=catalog_version)
    seed_artists = {songs_by_id[s].get("artist_credit") or "" for b in context["seed_branches"]
                    for s in b["seed_song_ids"] if s in songs_by_id}
    heldout_path = Path(args.data).parent / "heldout_artists.json"
    heldout = set(json.loads(heldout_path.read_text("utf-8"))) if heldout_path.is_file() else set()
    heldout_ids = sorted(s["song_id"] for s in songs if (s.get("artist_credit") or "").strip().lower() in heldout)
    llm = OpenAICompatibleChat(args.api_key, model=args.model, base_url=args.base_url, temperature=0.0, timeout=120)
    samples = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()][: args.limit or None]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    started = time.time()
    with (out_dir / "per_sample.jsonl").open("w", encoding="utf-8") as handle:
        for i, sample in enumerate(samples, 1):
            row = evaluate_sample(llm, sample, songs_by_id=songs_by_id, base_context=context, retriever=retriever,
                                  similarity=vector_similarity(retriever), seed_artists=seed_artists,
                                  heldout_song_ids=heldout_ids)
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(samples)}] {row['sample_id']} completed={row['completed']} repairs={row['repairs']} "
                  f"overlap={row.get('pick_overlap')} {row['latency_ms']:.0f} ms"
                  + (f" fallback={row['fallback_reason']}" if row["fallback_reason"] else ""))
    report = aggregate(rows)
    report["model"] = {"name": args.model, "base_url": args.base_url}
    report["minutes"] = round((time.time() - started) / 60, 1)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8")
    write_run_manifest(out_dir, build_run_manifest(
        out_dir.name, kind="sft_eval", config=vars(args), data_files=[args.data],
        model={"served_name": args.model, "base_url": args.base_url}, metrics=report["overall"]))
    print(json.dumps(report["overall"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

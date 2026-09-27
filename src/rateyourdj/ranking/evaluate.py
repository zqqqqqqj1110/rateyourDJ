"""Stage 3 offline evaluation: rag-rel-v1 vs rag-tailmix-v1 (vs the ReAct agent).

Fixed query set, frozen user context (nothing recorded), fixed candidate sets,
metrics-v1, weak-pos-v1. Reports per query, per stratum (branch, exploration
level, bucket) and the pre-registered acceptance check:
  tailmix mean relevance >= 90% of rel-only, and tail exposure >= +50%.
"""

from __future__ import annotations

import copy
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from rateyourdj.agent.loop import run_agent
from rateyourdj.agent.tools import Toolbox

from . import metrics as M
from .positives import positives_for
from .validator import validate_selection
from .ranker import slot_plan

K = 10
RELEVANT_PCT = 0.85
ACCEPTANCE = {"min_relevance_ratio": 0.90, "min_tail_exposure_gain": 0.50}


def frozen_context(context: dict[str, Any]) -> dict[str, Any]:
    ctx = copy.deepcopy(context)
    ctx["recommended_song_ids"], ctx["heard_song_ids"], ctx["recent_feedback_ids"] = [], [], []
    return ctx


def evaluate(queries: list[dict[str, Any]], *, songs: list[dict[str, Any]], context: dict[str, Any],
             retriever: Any, positives: dict[str, Any], similarity=None, seed_artists: set[str] | None = None,
             llm: Any = None, log=print) -> dict[str, Any]:
    songs_by_id = {s["song_id"]: s for s in songs}
    rows: list[dict[str, Any]] = []
    recs_by_system: dict[str, list[list[str]]] = defaultdict(list)
    systems = ["rag-rel-v1", "rag-tailmix-v1"] + (["agent-react-v1"] if llm is not None else [])
    for q in queries:
        e = q["exploration"]
        cs = retriever.retrieve(q["text"], branch_hint=q["branch_hint"], exploration_level=e)
        pos = positives_for(positives, q["expect"])
        cand_by_id = {c["song_id"]: c for c in cs["candidates"]}
        relevant = pos | {c["song_id"] for c in cs["candidates"] if c["relevance"] >= RELEVANT_PCT}
        min_tail = slot_plan(K, e)["tail"]
        outputs: dict[str, dict[str, Any]] = {}
        for strategy in ("rag-rel-v1", "rag-tailmix-v1"):
            tb = Toolbox(songs_by_id, context, retriever, similarity=similarity, seed_artists=seed_artists, count=K)
            ranked = tb.rank(cs, strategy=strategy, count=K)["ranked"]
            picks = [{"song_id": r["song_id"], "reason": r["reason"], "evidence_refs": r["evidence_refs"]}
                     for r in ranked]
            outputs[strategy] = {"picks": picks, "cs": cs, "fallback": None}
        if llm is not None:
            tb = Toolbox(songs_by_id, context, retriever, similarity=similarity, seed_artists=seed_artists, count=K)
            run = run_agent(llm, tb, request_text=q["text"], count=K, exploration_level=e,
                            branch_hint=q["branch_hint"])
            outputs["agent-react-v1"] = {"picks": run["picks"], "cs": run["candidate_set"],
                                         "fallback": run["fallback_reason"], "steps": len(run["steps"]),
                                         "repairs": run.get("repairs"), "latency_ms": run["latency_ms"]}
            log(f"[agent] {q['id']}: {run['strategy_version']} fallback={run['fallback_reason']} "
                f"repairs={run.get('repairs')} {run['latency_ms']} ms")
        baseline = [p["song_id"] for p in outputs["rag-rel-v1"]["picks"]]
        for system in systems:
            out = outputs[system]
            used = out["cs"]
            used_by_id = {c["song_id"]: c for c in used["candidates"]}
            recs = [p["song_id"] for p in out["picks"]]
            recs_by_system[system].append(recs)
            report = validate_selection(out["picks"], used, count=K,
                                        min_tail=min_tail if system != "rag-rel-v1" else 0)
            rels = [used_by_id[s]["relevance"] for s in recs if s in used_by_id]
            rows.append({
                "query": q["id"], "system": system, "expect": q["expect"], "exploration": e,
                "positives": len(pos),
                "hits@10": M.hits_at_k(recs, pos, K),
                "recall@10": M.recall_at_k(recs, pos, K),
                "ndcg@10": M.ndcg_at_k(recs, pos, K),
                "candidate_recall": M.candidate_recall(cand_by_id, pos),
                "tail_candidate_recall": M.candidate_recall(
                    cand_by_id, {s for s in pos if songs_by_id[s]["popularity"]["bucket"] == "tail"}),
                "tail_exposure": M.tail_share(recs, songs_by_id),
                "mean_relevance": sum(rels) / len(rels) if rels else 0.0,
                "novelty": M.novelty(recs, songs_by_id),
                "ild": M.intra_list_diversity(recs, similarity),
                "serendipity": M.serendipity(recs, relevant, set(baseline)),
                "hallucination_rate": M.hallucination_rate(recs, set(used_by_id)),
                "constraint_pass": 1.0 if report["ok"] else 0.0,
                "evidence_accuracy": M.evidence_accuracy(out["picks"], used_by_id),
                "fallback": out.get("fallback"),
                "steps": out.get("steps"),
                "repairs": out.get("repairs"),
                "latency_ms": out.get("latency_ms"),
                "buckets": {b: sum(songs_by_id[s]["popularity"]["bucket"] == b for s in recs)
                            for b in ("head", "mid", "tail")},
            })

    numeric = ["hits@10", "recall@10", "ndcg@10", "candidate_recall", "tail_candidate_recall", "tail_exposure",
               "mean_relevance", "novelty", "ild", "serendipity", "hallucination_rate", "constraint_pass",
               "evidence_accuracy"]

    def mean(rs: list[dict[str, Any]], key: str) -> float | None:
        vals = [r[key] for r in rs if r.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    summary, strata = {}, {}
    for system in systems:
        rs = [r for r in rows if r["system"] == system]
        summary[system] = {k: mean(rs, k) for k in numeric}
        summary[system].update({k: round(v, 4) if isinstance(v, float) else v
                                for k, v in M.coverage(recs_by_system[system], songs_by_id).items()})
        if system == "agent-react-v1":
            summary[system]["fallback_rate"] = round(sum(1 for r in rs if r["fallback"]) / len(rs), 4)
            summary[system]["mean_latency_ms"] = mean(rs, "latency_ms")
        strata[system] = {
            "by_branch": {b: {k: mean([r for r in rs if r["expect"] == b], k)
                              for k in ("recall@10", "tail_exposure", "mean_relevance")}
                          for b in sorted({r["expect"] for r in rs})},
            "by_exploration": {str(x): {k: mean([r for r in rs if r["exploration"] == x and
                                                 r["query"].startswith("explore-")], k)
                                        for k in ("tail_exposure", "mean_relevance", "novelty")}
                               for x in sorted({r["exploration"] for r in rs if r["query"].startswith("explore-")})},
            "by_bucket_exposure": {b: round(sum(r["buckets"][b] for r in rs) / (K * len(rs)), 4)
                                   for b in ("head", "mid", "tail")},
        }
    rel, mix = summary["rag-rel-v1"], summary["rag-tailmix-v1"]
    ratio = mix["mean_relevance"] / rel["mean_relevance"] if rel["mean_relevance"] else None
    gain = (mix["tail_exposure"] / rel["tail_exposure"] - 1) if rel["tail_exposure"] else float("inf")
    acceptance = {"relevance_ratio": round(ratio, 4) if ratio is not None else None,
                  "tail_exposure_gain": round(gain, 4) if gain != float("inf") else "inf",
                  **ACCEPTANCE,
                  "passed": bool(ratio is not None and ratio >= ACCEPTANCE["min_relevance_ratio"]
                                 and gain >= ACCEPTANCE["min_tail_exposure_gain"])}
    return {"metrics_version": M.METRICS_VERSION, "k": K, "positives_version": positives["version"],
            "summary": summary, "strata": strata, "acceptance": acceptance, "per_query": rows}


def write_outputs(report: dict[str, Any], run_dir: Path, info: dict[str, Any]) -> None:
    import json

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8")
    fields = [k for k in report["per_query"][0] if k != "buckets"] + ["head", "mid", "tail"]
    with (run_dir / "per_query.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for r in report["per_query"]:
            writer.writerow({**{k: v for k, v in r.items() if k != "buckets"}, **r["buckets"]})
    systems = list(report["summary"])
    keys = list(report["summary"][systems[0]])
    lines = ["# 阶段 3 排序评估", "",
             f"- 指标版本：`{report['metrics_version']}`；正确答案：`{report['positives_version']}`；K = {report['k']}",
             f"- 索引：`{info.get('index_version')}`；查询：{info.get('query_count')} 条",
             "", "## 验收（事先约定）", "",
             f"- 固定混排 / 只看相关性 的平均相关性比值：**{report['acceptance']['relevance_ratio']}**（要求 ≥ 0.90）",
             f"- 长尾曝光提升：**{report['acceptance']['tail_exposure_gain']}**（要求 ≥ 0.50）",
             f"- 结果：**{'通过' if report['acceptance']['passed'] else '未通过'}**",
             "", "## 汇总", "", "| 指标 | " + " | ".join(systems) + " |", "|---|" + "---|" * len(systems)]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(report["summary"][s].get(k)) for s in systems) + " |")
    lines += ["", "## 分层", "", "```json", json.dumps(report["strata"], ensure_ascii=False, indent=1), "```", ""]
    (run_dir / "report.md").write_text("\n".join(lines), "utf-8")

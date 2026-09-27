"""Stage 2 retrieval evaluation: rule-only vs RAG on a fixed query set.

There is no ground truth yet (hidden positives arrive in stage 4/5), so
relevance is judged by two independent judges and both are reported:

  vec  cosine to the expected branch's interest centre (bge-m3 space)
  tag  cosine between the song's tags and the branch's seed-tag profile

"Relevant" = in the catalog's top 15% under that judge. RAG is favoured by the
vec judge and the rule channel by the tag judge, so a real improvement should
show up under both.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .retrieval import Retriever, _tag_cosine

TOP_SHARE = 0.15


class Judges:
    def __init__(self, retriever: Retriever) -> None:
        self.r = retriever
        self.vec: dict[str, np.ndarray] = {}
        self.tag: dict[str, np.ndarray] = {}
        for b in retriever.branch_ids:
            if retriever.matrix is not None and b in retriever.centres:
                self.vec[b] = retriever.matrix @ retriever.centres[b]
            self.tag[b] = np.array([_tag_cosine(t, retriever.branch_tags[b]) for t in retriever.tags])
        self.cut = {(kind, b): float(np.quantile(scores, 1 - TOP_SHARE))
                    for kind, table in (("vec", self.vec), ("tag", self.tag)) for b, scores in table.items()}

    def score(self, kind: str, branch: str, song_id: str) -> float:
        return float(getattr(self, kind)[branch][self.r.row[song_id]])

    def relevant(self, kind: str, expect: str, song_id: str) -> bool:
        branches = self.r.branch_ids if expect == "both" else [expect]
        return any(self.score(kind, b, song_id) >= self.cut[(kind, b)] for b in branches)

    def prefers(self, kind: str, expect: str, song_id: str) -> bool:
        scores = {b: self.score(kind, b, song_id) for b in self.r.branch_ids}
        return max(scores, key=scores.get) == expect


def evaluate_query(result: dict[str, Any], query: dict[str, Any], judges: Judges,
                   songs_by_id: dict[str, Any], seed_ids: set[str]) -> dict[str, Any]:
    cands = result["candidates"]
    n = len(cands) or 1
    kinds = [k for k in ("vec", "tag") if getattr(judges, k)]
    tails = [c for c in cands if c["bucket"] == "tail"]
    out: dict[str, Any] = {
        "id": query["id"], "n": len(cands),
        "tail_share": round(len(tails) / n, 3),
        "buckets": dict(Counter(c["bucket"] for c in cands)),
        "channels": result["channel_counts"],
        "unique_artist_share": round(len({c["artist_credit"] for c in cands}) / n, 3),
        "traceable": all(c["song_id"] in songs_by_id for c in cands),
        "evidence_valid": all(_ref_ok(ev["ref"], songs_by_id, seed_ids)
                              for c in cands for ev in c["evidence"]),
    }
    for k in kinds:
        out[f"relevant_share_{k}"] = round(sum(judges.relevant(k, query["expect"], c["song_id"])
                                               for c in cands) / n, 3)
        out[f"relevant_tail_{k}"] = sum(judges.relevant(k, query["expect"], c["song_id"]) for c in tails)
        if query["expect"] != "both":
            out[f"branch_accuracy_{k}"] = round(sum(judges.prefers(k, query["expect"], c["song_id"])
                                                    for c in cands) / n, 3)
    return out


def _ref_ok(ref: str, songs_by_id: dict[str, Any], seed_ids: set[str]) -> bool:
    if ref == "request":
        return True
    if ref in seed_ids:
        return True
    for prefix in ("tags:", "listenbrainz:"):
        if ref.startswith(prefix):
            return ref[len(prefix):] in songs_by_id
    return False


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [r[key] for r in rows if key in r]
    return round(float(np.mean(values)), 3) if values else None


def run_evaluation(queries: list[dict[str, Any]], systems: dict[str, Retriever], judges: Judges,
                   songs_by_id: dict[str, Any], seed_ids: set[str]) -> dict[str, Any]:
    report: dict[str, Any] = {"judges": {"top_share": TOP_SHARE,
                                         "kinds": [k for k in ("vec", "tag") if getattr(judges, k)]},
                              "systems": {}, "samples": {}}
    for name, retriever in systems.items():
        rows, sets = [], {}
        for q in queries:
            result = retriever.retrieve(q["text"], branch_hint=q["branch_hint"],
                                        exploration_level=q["exploration"])
            rows.append(evaluate_query(result, q, judges, songs_by_id, seed_ids))
            sets[q["id"]] = {c["song_id"] for c in result["candidates"]}
            report["samples"].setdefault(q["id"], {})[name] = [
                f"[{c['channels'][0]['channel']}/{c['bucket']}] {c['artist_credit']} - {c['title']}"
                for c in result["candidates"][:10]]
        keys = sorted({k for r in rows for k in r
                       if isinstance(r[k], (int, float)) and not isinstance(r[k], bool) and k != "n"})
        a, b = sets.get("pf-hint", set()), sets.get("oasis-hint", set())
        freq = Counter(sid for s in sets.values() for sid in s)
        hub_cut = max(2, len(sets) // 2)
        report["systems"][name] = {
            "summary": {k: _mean(rows, k) for k in keys},
            "all_traceable": all(r["traceable"] for r in rows),
            "all_evidence_valid": all(r["evidence_valid"] for r in rows),
            "branch_overlap_jaccard": round(len(a & b) / len(a | b), 3) if a | b else None,
            # hubness: songs returned for at least half of all queries
            "hub_songs": sum(1 for c in freq.values() if c >= hub_cut),
            "max_song_frequency": max(freq.values()) if freq else 0,
            "exploration_tail_share": {r["id"]: r["tail_share"] for r in rows if r["id"].startswith("explore-")},
            "per_query": rows,
        }
    return report


def render_markdown(report: dict[str, Any], manifest: dict[str, Any]) -> str:
    systems = list(report["systems"])
    lines = ["# 阶段 2 召回评估", "",
             f"- 索引：`{manifest.get('index_version')}`（{manifest.get('model')}）",
             f"- 查询集：{manifest.get('queries')}（{manifest.get('query_count')} 条）",
             f"- 相关性裁判：vec（兴趣中心向量）、tag（种子标签重合），“相关”= 该裁判下曲库前 {int(TOP_SHARE * 100)}%",
             "", "## 汇总（各查询平均）", "", "| 指标 | " + " | ".join(systems) + " |",
             "|---|" + "---|" * len(systems)]
    keys = sorted({k for s in systems for k in report["systems"][s]["summary"]})
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(report["systems"][s]["summary"].get(k)) for s in systems) + " |")
    for extra in ("branch_overlap_jaccard", "hub_songs", "max_song_frequency", "all_traceable",
                  "all_evidence_valid"):
        lines.append(f"| {extra} | " + " | ".join(str(report["systems"][s][extra]) for s in systems) + " |")
    lines.append(f"| exploration → tail_share | " + " | ".join(
        str(report["systems"][s]["exploration_tail_share"]) for s in systems) + " |")
    lines += ["", "## 样例（每个查询前 10）", ""]
    for qid, per in report["samples"].items():
        lines.append(f"### {qid}")
        for s in systems:
            lines.append(f"**{s}**")
            lines += [f"{i + 1}. {row}" for i, row in enumerate(per.get(s, []))]
        lines.append("")
    return "\n".join(lines)

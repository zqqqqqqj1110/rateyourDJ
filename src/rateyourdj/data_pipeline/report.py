"""Catalog quality report: missing / duplicate / match rates and bucket distribution."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rateyourdj.contracts import normalize_name


def _rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def catalog_report(songs: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    n = len(songs)
    missing = {
        "title": sum(not s.get("title") for s in songs),
        "artist_mbid": sum(not any(a.get("mbid") for a in s.get("artists", [])) for s in songs),
        "release_year": sum(not (s.get("release") or {}).get("year") for s in songs),
        "tags": sum(not s.get("tags") for s in songs),
        "listener_count": sum(s["popularity"].get("listener_count") is None for s in songs),
    }
    ids = Counter(s["song_id"] for s in songs)
    names = Counter(normalize_name(s.get("artist_credit") or "") + "|" + normalize_name(s.get("title") or "")
                    for s in songs)
    seed_ids = [sid for b in context["seed_branches"] for sid in b["seed_song_ids"]]
    catalog_ids = set(ids)
    buckets = Counter(s["popularity"]["bucket"] for s in songs)
    per_branch: dict[str, Counter] = defaultdict(Counter)
    tags_per_branch: dict[str, Counter] = defaultdict(Counter)
    for s in songs:
        for branch, affinity in (s.get("branch_affinity") or {}).items():
            if affinity >= 0.2:
                per_branch[branch][s["popularity"]["bucket"]] += 1
                tags_per_branch[branch].update(s.get("genres", [])[:3])
    playback = Counter(s.get("playback", {}).get("source", "none") for s in songs)
    return {
        "songs": n,
        "missing_rate": {k: _rate(v, n) for k, v in missing.items()},
        "duplicate_song_ids": sum(c - 1 for c in ids.values() if c > 1),
        "duplicate_name_rate": _rate(sum(c - 1 for c in names.values() if c > 1), n),
        "seed_match": {"seeds": len(seed_ids), "in_catalog": sum(s in catalog_ids for s in seed_ids)},
        "bucket_distribution": dict(buckets),
        "bucket_share": {k: _rate(v, n) for k, v in buckets.items()},
        "per_branch": {b: {"songs": sum(c.values()), "buckets": dict(c),
                           "top_genres": [g for g, _ in tags_per_branch[b].most_common(8)]}
                       for b, c in per_branch.items()},
        "playback_sources": dict(playback),
    }


def render_markdown(report: dict[str, Any], manifest: dict[str, Any] | None = None) -> str:
    lines = ["# 曲库质量报告", ""]
    if manifest:
        lines += [f"- 版本：`{manifest.get('catalog_version')}`，构建于 {manifest.get('built_at')}",
                  f"- 艺人 {manifest['counts'].get('artists')}，歌曲 {manifest['counts'].get('songs')}", ""]
    lines += ["## 缺失率", "", "| 字段 | 缺失率 |", "|---|---|"]
    lines += [f"| {k} | {v:.1%} |" for k, v in report["missing_rate"].items()]
    lines += ["", "## 重复与种子", "",
              f"- 重复 song_id：{report['duplicate_song_ids']}",
              f"- 同名（艺人+歌名）重复率：{report['duplicate_name_rate']:.1%}",
              f"- 种子在曲库中：{report['seed_match']['in_catalog']}/{report['seed_match']['seeds']}",
              "", "## 冷门程度分档", "", "| bucket | 歌曲数 | 占比 |", "|---|---|---|"]
    for k, v in sorted(report["bucket_distribution"].items()):
        lines.append(f"| {k} | {v} | {report['bucket_share'][k]:.1%} |")
    lines += ["", "## 各兴趣分支", "", "| 分支 | 歌曲数 | 分档 | 主要流派 |", "|---|---|---|---|"]
    for b, v in report["per_branch"].items():
        lines.append(f"| {b} | {v['songs']} | {v['buckets']} | {', '.join(v['top_genres'])} |")
    lines += ["", "## 播放来源", "", f"{report['playback_sources']}", ""]
    return "\n".join(lines)


def write_report(catalog_root: str | Path, songs: list[dict[str, Any]],
                 context: dict[str, Any]) -> dict[str, Any]:
    root = Path(catalog_root)
    report = catalog_report(songs, context)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.is_file() else None
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (root / "report.md").write_text(render_markdown(report, manifest), "utf-8")
    return report

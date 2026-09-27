"""Stage 0 baseline archive (run on your Mac: needs network + API keys).

Archives, under runs/baseline-v0/:
  1. regression: unit tests + L7 50-case eval suite (pass/fail + logs)
  2. legacy path: the old "DeepSeek nominates -> Spotify/Last.fm grounds"
     model-mode agent over eval/queries_v1.jsonl (50 queries), with
     per-query results, trajectories and aggregate metrics
  3. manifest.json: git commit, data fingerprints, model and config

Rule-mode recommendation baselines are intentionally NOT archived.

NOTE (stage 3): the legacy "DeepSeek nominates -> Spotify grounds" path was
removed from the codebase. This script reproduces baseline-v0 only when run on
commit 15a0efa (stage 0); on later commits the legacy section just falls back.
All writes go to an isolated copy of the data; data/ is never modified.

Usage (from the repo root):
    PYTHONPATH=src python scripts/baseline_v0.py
    PYTHONPATH=src python scripts/baseline_v0.py --limit 3   # quick check
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rateyourdj.config import load_dotenv  # noqa: E402
from rateyourdj.experiment import build_run_manifest, write_run_manifest  # noqa: E402


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return round(float(ordered[index]), 3)


def run_regression(run_dir: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    results = {}
    commands = {
        "unit_tests": [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        "eval_suite": [sys.executable, "-m", "rateyourdj.l7.cli", "run-eval-suite"],
    }
    for name, command in commands.items():
        started = time.perf_counter()
        proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        log = run_dir / f"{name}.log"
        log.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
        tail = [line for line in proc.stdout.strip().splitlines() if line.strip()][-1:] or [""]
        results[name] = {
            "passed": proc.returncode == 0,
            "returncode": proc.returncode,
            "summary": tail[0],
            "seconds": round(time.perf_counter() - started, 1),
            "log": log.name,
        }
        print(f"[regression] {name}: {'PASS' if proc.returncode == 0 else 'FAIL'}  {tail[0]}")
    return results


def run_legacy_path(run_dir: Path, queries: list[dict], user_id: str, top_k: int) -> dict:
    from rateyourdj.l6.deepseek import (
        DEFAULT_DEEPSEEK_BASE_URL,
        DEFAULT_DEEPSEEK_MODEL,
        configured_llm_provider,
    )
    from rateyourdj.l6.tools import request_recommendations
    from rateyourdj.l7.trajectory_quality import compute_trajectory_quality
    from rateyourdj.providers import configured_music_provider_from_env

    work = run_dir / "raw"
    profile_dir = work / "user_profiles"
    song_dir = work / "song_profiles"
    for name, target in (("user_profiles", profile_dir), ("song_profiles", song_dir)):
        source = ROOT / "data" / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".*.lock"))
        target.mkdir(parents=True, exist_ok=True)

    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
    llm = configured_llm_provider(
        "deepseek", model=model,
        base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
    )
    music = configured_music_provider_from_env()

    records, rows, latencies = [], [], []
    for index, item in enumerate(queries, 1):
        started = time.perf_counter()
        error = None
        try:
            response = request_recommendations(
                user_id, item["query"],
                profile_dir=profile_dir, song_dir=song_dir,
                trajectory_dir=work / "trajectories", session_dir=work / "sessions",
                default_top_k=top_k, agent_mode="model",
                llm_provider=llm, music_provider=music,
            ).to_dict()
        except Exception as exc:  # keep going; failures are part of the baseline
            response, error = {}, f"{type(exc).__name__}: {exc}"
        wall_ms = round((time.perf_counter() - started) * 1000, 1)
        latencies.append(wall_ms)
        songs = response.get("ranked_songs") or []
        decisions = response.get("agent_decisions") or [{}]
        row = {
            "query_id": item.get("query_id"),
            "category": item.get("category"),
            "query": item["query"],
            "error": error,
            "agent_mode": response.get("agent_mode"),
            "provider": response.get("provider"),
            "fallback_reason": response.get("fallback_reason"),
            "stop_reason": response.get("stop_reason"),
            "action": decisions[0].get("action"),
            "message": (response.get("message") or "")[:300],
            "num_songs": len(songs),
            "requested": (response.get("parsed_request") or {}).get("top_k"),
            "songs": [
                {"title": s.get("title"), "artist": s.get("artist"), "song_id": s.get("song_id")}
                for s in songs
            ],
            "wall_ms": wall_ms,
            "trajectory_id": response.get("trajectory_id"),
        }
        rows.append(row)
        records.append(response)
        status = error or (
            f"{len(songs)} songs, action={row['action']}, fallback={row['fallback_reason']}"
        )
        print(f"[legacy {index:>2}/{len(queries)}] {item.get('query_id')}: {status}")
        if index == 3 and all(r["agent_mode"] != "model" for r in rows):
            raise SystemExit(
                "first 3 queries all fell back to rules mode "
                f"({rows[-1]['fallback_reason'] or rows[-1]['error']}); "
                "check network / DEEPSEEK_API_KEY before spending the full run."
            )

    with open(run_dir / "legacy_results.jsonl", "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    # The answer-first path does not persist trajectories, so keep full responses.
    with open(work / "responses.jsonl", "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    actions: dict[str, int] = {}
    for row in rows:
        actions[str(row["action"])] = actions.get(str(row["action"]), 0) + 1

    quality = compute_trajectory_quality([r for r in records if r])
    quality_dict = quality.to_dict() if hasattr(quality, "to_dict") else dict(vars(quality))
    n = len(rows)
    ok = [r for r in rows if not r["error"]]
    return {
        "queries": n,
        "errors": n - len(ok),
        "model_mode_rate": round(sum(r["agent_mode"] == "model" for r in ok) / n, 4) if n else 0.0,
        "fallback_rate": round(sum(bool(r["fallback_reason"]) for r in ok) / n, 4) if n else 0.0,
        "empty_result_rate": round(sum(r["num_songs"] == 0 for r in rows) / n, 4) if n else 0.0,
        "actions": actions,
        # proxy for grounding loss: the answer-first path does not log
        # discover_tracks, so generated/grounded counts are unavailable.
        "fill_rate": round(
            sum(min(r["num_songs"], r["requested"]) for r in ok if r["requested"])
            / max(1, sum(r["requested"] for r in ok if r["requested"])), 4),
        "avg_songs": round(sum(r["num_songs"] for r in rows) / n, 3) if n else 0.0,
        "wall_ms_p50": _percentile(latencies, 50),
        "wall_ms_p95": _percentile(latencies, 95),
        "trajectory_quality": quality_dict,
        "llm_model": model,
        "music_provider": type(music).__name__ if music else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", default="runs/baseline-v0")
    parser.add_argument("--queries", default="eval/queries_v1.jsonl")
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int, help="only run the first N queries")
    parser.add_argument("--skip-regression", action="store_true")
    args = parser.parse_args()

    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set (.env); the legacy path needs it.", file=sys.stderr)
        return 2
    for key in ("SPOTIFY_CLIENT_ID", "LASTFM_API_KEY"):
        if not os.getenv(key):
            print(f"warning: {key} not set; grounding will be weaker", file=sys.stderr)

    run_dir = ROOT / args.run_dir
    if run_dir.exists():
        print(f"{run_dir} already exists; move it away or pass --run-dir", file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True)

    queries = [json.loads(line) for line in open(args.queries, encoding="utf-8") if line.strip()]
    if args.limit:
        queries = queries[: args.limit]

    metrics = {}
    if not args.skip_regression:
        metrics["regression"] = run_regression(run_dir)
    metrics["legacy_deepseek_grounding"] = run_legacy_path(run_dir, queries, args.user_id, args.top_k)

    manifest = build_run_manifest(
        run_dir.name,
        kind="baseline",
        config={
            "user_id": args.user_id, "top_k": args.top_k, "agent_mode": "model",
            "queries": args.queries, "limit": args.limit,
            "rule_mode_baseline": "not archived (by decision)",
        },
        data_files=[args.queries],
        model={"provider": "deepseek",
               "name": metrics["legacy_deepseek_grounding"]["llm_model"]},
        metrics=metrics,
        notes="Stage 0 baseline: regression gates + legacy DeepSeek nominate/Spotify grounding path.",
        cwd=ROOT,
    )
    path = write_run_manifest(run_dir, manifest)
    print(json.dumps(metrics["legacy_deepseek_grounding"], ensure_ascii=False, indent=2)[:2000])
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

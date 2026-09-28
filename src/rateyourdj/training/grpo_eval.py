"""Selection-step evaluation for stage 5: SFT vs SFT + fixed mix vs SFT + GRPO.

All systems choose from the same frozen candidate sets (GRPO test / val samples):
  fixed_mix   the deterministic rag-tailmix-v1 picks stored with the sample (no model)
  <model>     completions from an OpenAI-compatible /v1/completions endpoint (vLLM), temperature 0,
              prompt = the sample rendered with the model's chat template

Metrics (per sample, then averaged; hidden positives are used here and nowhere in training):
  valid, reward (reward-v1, reported but not the success criterion), reward components
  hp_recall        |picks ∩ hidden positives| / min(n, |hidden positives ∩ candidates|)
  hp_tail_recall   the same restricted to tail songs
  weak_recall      the same with weak-pos-v1 (popular co-listens)
  tail_exposure, mean_relevance, ild (1 - mean pairwise similarity), seed_artist_share
Composite C = 0.6 hp_recall + 0.4 min(1, tail_exposure / fixed-mix tail_exposure)
(hidden-pos-v1 contains no tail songs, see stage-5.md).
Paired bootstrap gives a 95% interval for C(system) - C(fixed_mix).
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .grpo_data import load_jsonl
from .grpo_reward import parse_completion, score


def picks_metrics(pick_ids: list[str], sample: dict[str, Any]) -> dict[str, Any]:
    cands = {c["song_id"]: (i, c) for i, c in enumerate(sample["candidates"])}
    ev = sample["eval_only"]
    n = sample["constraints"]["count"]
    chosen = [p for p in pick_ids if p in cands]
    out: dict[str, Any] = {}
    for key, pos in (("hp", set(ev.get("hidden_positives") or [])), ("weak", set(ev.get("weak_positives") or []))):
        avail = pos & set(cands)
        out[f"{key}_possible"] = min(n, len(avail))
        out[f"{key}_hits"] = len(set(chosen) & avail)
        tail_avail = {s for s in avail if cands[s][1]["bucket"] == "tail"}
        if key == "hp":
            out["hp_tail_possible"] = min(n, len(tail_avail))
            out["hp_tail_hits"] = len(set(chosen) & tail_avail)
    if chosen:
        rows = [cands[s][0] for s in chosen]
        sim = sample["sim"]
        pairs = [(a, b) for k, a in enumerate(rows) for b in rows[k + 1:]]
        out.update({
            "tail_exposure": sum(cands[s][1]["bucket"] == "tail" for s in chosen) / len(chosen),
            "mean_relevance": sum(cands[s][1]["relevance"] for s in chosen) / len(chosen),
            "ild": 1 - sum(sim[a][b] for a, b in pairs) / max(1, len(pairs)),
            "seed_artist_share": sum(cands[s][1]["seed_artist"] for s in chosen) / len(chosen),
        })
    return out


def _ratio(rows: list[dict[str, Any]], hits: str, possible: str) -> float | None:
    rs = [r[hits] / r[possible] for r in rows if r.get(possible)]
    return round(sum(rs) / len(rs), 4) if rs else None


def summarize(rows: list[dict[str, Any]], ref_tail: float | None = None) -> dict[str, Any]:
    valid = [r for r in rows if r.get("valid")]
    out: dict[str, Any] = {"n": len(rows), "valid": round(len(valid) / max(1, len(rows)), 4),
                           "reward": round(sum(r["reward"] for r in rows) / max(1, len(rows)), 4),
                           "hp_recall": _ratio(valid, "hp_hits", "hp_possible"),
                           "hp_tail_recall": _ratio(valid, "hp_tail_hits", "hp_tail_possible"),
                           "weak_recall": _ratio(valid, "weak_hits", "weak_possible"),
                           "samples_with_hp": sum(1 for r in rows if r.get("hp_possible")),
                           "samples_with_tail_hp": sum(1 for r in rows if r.get("hp_tail_possible"))}
    for k in ("tail_exposure", "mean_relevance", "ild", "seed_artist_share"):
        vals = [r[k] for r in valid if k in r]
        out[k] = round(sum(vals) / len(vals), 4) if vals else None
    comps = [r["components"] for r in valid if r.get("components")]
    if comps:
        out["components"] = {k: round(sum(c[k] for c in comps) / len(comps), 4) for k in comps[0]}
    out["distinct_songs"] = len({s for r in valid for s in r.get("picks", [])})
    return out


def composite(row: dict[str, Any], ref_tail: float) -> float:
    if not row.get("valid"):
        return 0.0
    # hidden-pos-v1 has no tail songs (co-listen data only covers popular recordings), so the
    # tail term of the originally registered composite was dropped before any result was seen
    hp = row["hp_hits"] / row["hp_possible"] if row.get("hp_possible") else 0.0
    return 0.6 * hp + 0.4 * min(1.0, row.get("tail_exposure", 0) / max(1e-9, ref_tail))


def paired_bootstrap(a: list[float], b: list[float], iters: int = 2000, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    n = len(a)
    diffs = sorted(sum(a[i] - b[i] for i in (rng.randrange(n) for _ in range(n))) / n for _ in range(iters))
    return [round(diffs[int(0.025 * iters)], 4), round(diffs[int(0.975 * iters)], 4)]


def complete(base_url: str, model: str, prompt: str, max_tokens: int = 1400, timeout: float = 180) -> str:
    payload = {"model": model, "prompt": prompt, "max_tokens": max_tokens, "temperature": 0.0,
               "stop": ["<|im_end|>"], "skip_special_tokens": False}
    req = Request(f"{base_url}/completions", data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json", "Authorization": "Bearer EMPTY"}, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["choices"][0]["text"]


def evaluate(samples: list[dict[str, Any]], systems: dict[str, Any], log=print, workers: int = 8) -> dict[str, Any]:
    """systems: name -> callable(sample) returning a completion string, or None for fixed_mix."""
    per: dict[str, list[dict[str, Any]]] = {name: [] for name in systems}
    # generate all completions first, concurrently (vLLM batches parallel requests)
    from concurrent.futures import ThreadPoolExecutor
    generated: dict[tuple[str, str], tuple[str, float]] = {}

    def run_one(name_fn_sample):
        name, fn, s = name_fn_sample
        t0 = time.time()
        return (name, s["sample_id"]), (fn(s), round(time.time() - t0, 2))
    jobs = [(name, fn, s) for name, fn in systems.items() if fn is not None for s in samples]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for k, (key, value) in enumerate(pool.map(run_one, jobs), 1):
            generated[key] = value
            if k % 50 == 0:
                log(f"  generated {k}/{len(jobs)}")
    for i, s in enumerate(samples, 1):
        for name, fn in systems.items():
            if fn is None:
                oracle = json.dumps({"name": "submit_recommendations", "arguments": s["eval_only"]["oracle_arguments"]},
                                    ensure_ascii=False)
                r = score(f"<tool_call>\n{oracle}\n</tool_call>", s)
                picks = list(s["eval_only"]["oracle_picks"])
                row = {"valid": r["valid"], "reward": r["reward"], "components": r.get("components")}
            else:
                completion, latency = generated[(name, s["sample_id"])]
                r = score(completion, s)
                args = parse_completion(completion) or {}
                picks = [p.get("song_id") for p in args.get("picks", []) if isinstance(p, dict)]
                row = {"valid": r["valid"], "reward": r["reward"], "components": r.get("components"),
                       "error": r.get("error"), "latency_s": latency, "chars": len(completion)}
            row.update(picks_metrics(picks, s))
            row.update({"sample_id": s["sample_id"], "picks": picks, "mode": s["meta"].get("mode"),
                        "exploration_level": s["constraints"]["exploration_level"], "count": s["constraints"]["count"]})
            per[name].append(row)
    ref_rows = per.get("fixed_mix") or next(iter(per.values()))
    ref_tail = sum(r.get("tail_exposure", 0) for r in ref_rows) / max(1, len(ref_rows))
    report: dict[str, Any] = {"systems": {}, "reference_tail_exposure": round(ref_tail, 4)}
    for name, rows in per.items():
        summ = summarize(rows)
        cs = [composite(r, ref_tail) for r in rows]
        summ["composite"] = round(sum(cs) / len(cs), 4)
        if "fixed_mix" in per and name != "fixed_mix":
            base = [composite(r, ref_tail) for r in per["fixed_mix"]]
            summ["composite_minus_fixed_mix_ci95"] = paired_bootstrap(cs, base)
        by_mode: dict[str, list] = {}
        for r in rows:
            by_mode.setdefault(r["mode"], []).append(r)
        summ["by_mode"] = {m: {"n": len(v), "valid": round(sum(x["valid"] for x in v) / len(v), 4),
                               "hp_recall": _ratio([x for x in v if x["valid"]], "hp_hits", "hp_possible"),
                               "tail_exposure": round(sum(x.get("tail_exposure", 0) for x in v) / len(v), 4)}
                           for m, v in sorted(by_mode.items())}
        report["systems"][name] = summ
    return {"report": report, "per_sample": per}


def main(argv: list[str] | None = None) -> int:
    import argparse

    from rateyourdj.experiment import build_run_manifest, write_run_manifest

    parser = argparse.ArgumentParser(prog="python -m rateyourdj.training.grpo_eval")
    parser.add_argument("--data", default="data/training/grpo/grpo-v1/test.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--models", default="sft=base,grpo=grpo",
                        help="name=served_model pairs; fixed_mix is always included")
    parser.add_argument("--tokenizer", required=True, help="path of the served base model (chat template)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer)
    samples = load_jsonl(args.data, args.limit)

    def caller(served: str):
        def fn(sample):
            prompt = tokenizer.apply_chat_template(sample["prompt"], tools=sample["tools"], tokenize=False,
                                                   add_generation_prompt=True)
            return complete(args.base_url, served, prompt)
        return fn

    systems: dict[str, Any] = {"fixed_mix": None}
    for pair in filter(None, args.models.split(",")):
        name, served = pair.split("=", 1)
        systems[name] = caller(served)
    result = evaluate(samples, systems)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(result["report"], ensure_ascii=False, indent=1) + "\n", "utf-8")
    with (out / "per_sample.jsonl").open("w", encoding="utf-8") as handle:
        for name, rows in result["per_sample"].items():
            for r in rows:
                handle.write(json.dumps({"system": name, **r}, ensure_ascii=False) + "\n")
    write_run_manifest(out, build_run_manifest(out.name, kind="grpo_eval", config=vars(args), data_files=[args.data],
                                               metrics={k: {m: v.get(m) for m in ("composite", "hp_recall", "valid")}
                                                        for k, v in result["report"]["systems"].items()}))
    for name, s in result["report"]["systems"].items():
        print(name, json.dumps({k: s.get(k) for k in ("valid", "reward", "composite", "composite_minus_fixed_mix_ci95",
                                                     "hp_recall", "hp_tail_recall", "weak_recall", "tail_exposure",
                                                     "mean_relevance", "ild", "distinct_songs")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

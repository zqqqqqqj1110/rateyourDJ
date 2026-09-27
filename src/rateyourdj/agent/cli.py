"""rateyourdj-agent: try the V2 recommender from the terminal.

  recommend "text" [--mode auto|agent|pipeline] [--strategy rag-rel-v1|rag-tailmix-v1]
  feedback IMPRESSION_ID [--event liked] [--heard-before no --relevance 4 ...]
"""

from __future__ import annotations

import argparse
import json
import sys

from rateyourdj.config import load_dotenv

from .factory import build_recommender


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="rateyourdj-agent")
    parser.add_argument("--user-id", default="participant_001")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("recommend")
    rec.add_argument("text", nargs="?", default="")
    rec.add_argument("--count", type=int, default=10)
    rec.add_argument("--exploration", type=float)
    rec.add_argument("--branch")
    rec.add_argument("--mode", choices=["auto", "agent", "pipeline"], default="auto")
    rec.add_argument("--strategy", choices=["rag-rel-v1", "rag-tailmix-v1"])
    rec.add_argument("--no-record", action="store_true", help="do not write impressions / context")
    fb = sub.add_parser("feedback")
    fb.add_argument("impression_id")
    fb.add_argument("--event", choices=["play_start", "play_progress", "completed", "quick_skip",
                                        "liked", "saved", "hide"])
    fb.add_argument("--seconds", type=float)
    fb.add_argument("--heard-before", choices=["yes", "no", "unsure"])
    fb.add_argument("--relevance", type=int, choices=range(1, 6))
    fb.add_argument("--discovery-value", type=int, choices=range(1, 6))
    fb.add_argument("--too-unfamiliar", action="store_true")
    fb.add_argument("--reject-reason", choices=["dislike_song", "not_in_mood_to_explore", "other"])
    args = parser.parse_args(argv)

    service = build_recommender(use_llm=args.command == "recommend" and args.mode != "pipeline")
    if args.command == "recommend":
        out = service.recommend(args.user_id, args.text, count=args.count, exploration_level=args.exploration,
                                branch_hint=args.branch, mode=args.mode, strategy=args.strategy,
                                record=not args.no_record)
        print(f"{out['strategy_version']}  model={out['model_version']}  fallback={out['fallback_reason']}"
              f"  {out['latency_ms']} ms")
        print(out["message"])
        for step in out["trace"]["steps"]:
            print(f"  step {step['step']}: {step.get('tool')} [{step.get('status')}] {step.get('summary') or ''}")
        for r in out["recommendations"]:
            print(f"{r['rank']:>2}. [{r['source_label']}/{r['bucket']}] {r['track']['artist']} - {r['track']['title']}"
                  f"   ({r['impression_id']})")
            print(f"      {r['reason']}")
        return 0
    survey = {k: v for k, v in {"heard_before": args.heard_before, "relevance": args.relevance,
                                "discovery_value": args.discovery_value,
                                "too_unfamiliar": True if args.too_unfamiliar else None,
                                "reject_reason": args.reject_reason}.items() if v is not None}
    record = service.record_feedback(args.user_id, args.impression_id, event=args.event,
                                     seconds=args.seconds, survey=survey)
    print(json.dumps(record, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

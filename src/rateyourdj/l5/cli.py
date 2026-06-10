from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore

from .models import feedback_schema
from .service import FeedbackService


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _load_context(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("context JSON must contain an object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="rateyourDJ L5 feedback loop")
    parser.add_argument("--profile-dir", default="data/user_profiles")
    parser.add_argument("--song-dir", default="data/song_profiles")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("schema", help="print feedback types and rewards")

    record = subparsers.add_parser("record", help="record one feedback event")
    record.add_argument("user_id")
    record.add_argument("song_id")
    record.add_argument("feedback_type")
    record.add_argument("--timestamp")
    record.add_argument("--reward-score", type=float)
    record.add_argument("--context-json", type=Path)

    summary = subparsers.add_parser("summary", help="summarize user feedback")
    summary.add_argument("user_id")

    score = subparsers.add_parser(
        "score",
        help="score one song using stored feedback",
    )
    score.add_argument("user_id")
    score.add_argument("song_id")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "schema":
        _print_json(feedback_schema())
        return

    service = FeedbackService(
        JsonProfileStore(args.profile_dir),
        JsonSongStore(args.song_dir),
    )
    if args.command == "record":
        _print_json(
            service.record(
                args.user_id,
                args.song_id,
                args.feedback_type,
                timestamp=args.timestamp,
                reward_score=args.reward_score,
                recommendation_context=_load_context(args.context_json),
            ).to_dict()
        )
        return
    if args.command == "summary":
        _print_json(service.summary(args.user_id).to_dict())
        return
    if args.command == "score":
        _print_json(
            {
                "user_id": args.user_id,
                "song_id": args.song_id,
                "feedback_score": service.score_song(
                    args.user_id,
                    args.song_id,
                ),
            }
        )


if __name__ == "__main__":
    main()

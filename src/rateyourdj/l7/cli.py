from __future__ import annotations

import argparse
import json
import os
from typing import Any

from .models import l7_schema
from .service import TrajectoryDatasetService
from .synthetic import SyntheticTrajectoryGenerator


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="rateyourDJ L7 trajectory export and offline evaluation"
    )
    parser.add_argument("--trajectory-dir", default="data/trajectories")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("schema", help="print the L7 data contract")

    export = subparsers.add_parser(
        "export",
        help="export trajectories as JSONL or CSV",
    )
    export.add_argument("output_path")
    export.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    export.add_argument("--user-id")
    export.add_argument("--feedback-only", action="store_true")
    export.add_argument("--include-user-id", action="store_true")
    export.add_argument(
        "--anonymization-salt",
        default=os.getenv("RATEYOURDJ_EXPORT_SALT", ""),
    )

    evaluate = subparsers.add_parser(
        "evaluate",
        help="calculate offline metrics from trajectories and feedback",
    )
    evaluate.add_argument("--user-id")
    evaluate.add_argument("--feedback-only", action="store_true")

    generate = subparsers.add_parser(
        "generate-synthetic",
        help="generate isolated synthetic trajectories for pipeline testing",
    )
    generate.add_argument(
        "output_dir",
        nargs="?",
        default="data/synthetic/trajectories",
    )
    generate.add_argument("--song-dir", default="data/song_profiles")
    generate.add_argument("--count", type=int, default=500)
    generate.add_argument("--users", type=int, default=25)
    generate.add_argument("--seed", type=int, default=20260615)
    generate.add_argument("--feedback-rate", type=float, default=0.7)

    split = subparsers.add_parser(
        "split",
        help="split trajectories by user into train, validation and test",
    )
    split.add_argument("output_dir")
    split.add_argument("--train-ratio", type=float, default=0.8)
    split.add_argument("--validation-ratio", type=float, default=0.1)
    split.add_argument("--test-ratio", type=float, default=0.1)
    split.add_argument("--seed", type=int, default=20260615)
    split.add_argument("--feedback-only", action="store_true")
    split.add_argument("--include-user-id", action="store_true")
    split.add_argument(
        "--anonymization-salt",
        default=os.getenv("RATEYOURDJ_EXPORT_SALT", ""),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "schema":
        _print_json(l7_schema())
        return
    if args.command == "generate-synthetic":
        result = SyntheticTrajectoryGenerator(args.song_dir).generate(
            args.output_dir,
            count=args.count,
            users=args.users,
            seed=args.seed,
            feedback_rate=args.feedback_rate,
        )
        _print_json(result.to_dict())
        return
    service = TrajectoryDatasetService(args.trajectory_dir)
    if args.command == "split":
        result = service.split_by_user(
            args.output_dir,
            train_ratio=args.train_ratio,
            validation_ratio=args.validation_ratio,
            test_ratio=args.test_ratio,
            seed=args.seed,
            feedback_only=args.feedback_only,
            anonymize=not args.include_user_id,
            anonymization_salt=args.anonymization_salt,
        )
        _print_json(result.to_dict())
        return
    if args.command == "export":
        result = service.export(
            args.output_path,
            format=args.format,
            user_id=args.user_id,
            feedback_only=args.feedback_only,
            anonymize=not args.include_user_id,
            anonymization_salt=args.anonymization_salt,
        )
        _print_json(result.to_dict())
        return
    report = service.evaluate(
        user_id=args.user_id,
        feedback_only=args.feedback_only,
    )
    _print_json(report.to_dict())


if __name__ == "__main__":
    main()

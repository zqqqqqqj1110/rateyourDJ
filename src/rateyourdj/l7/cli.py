from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from typing import Any

from .models import l7_schema
from .eval_runner import RecommendationEvalSuite
from .service import TrajectoryDatasetService
from .synthetic import SyntheticTrajectoryGenerator
from .trajectory_quality import check_quality_gate, load_trajectory_quality
from .ab_compare import ABVariant, run_ab_comparison


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _run_default_ab_comparison(queries: list[str] | None) -> Any:
    """Default offline A/B: rules-only vs model(ReAct) with a mock model.

    Uses a MockLLMProvider that immediately finishes, so the model arm exercises
    the agent loop (program-first discovery + ReAct decision recording) without
    any API key or network. Both arms share the same seeded rock profile.
    """
    from rateyourdj.l6 import MockLLMProvider
    from rateyourdj.l6.provider import AgentDecision

    resolved_queries = queries or [
        "推荐一些英伦摇滚",
        "再来一些摇滚",
        "想要一些有吉他感的歌",
    ]

    def _mock_llm() -> Any:
        # One finish decision per query is enough; the program runs ranking
        # before consulting the model in rules-style seeded environments.
        return MockLLMProvider(
            [
                AgentDecision(
                    kind="finish",
                    summary="finish after seeded ranking",
                    thought="seed collection already yields enough candidates",
                )
                for _ in range(len(resolved_queries) * 4)
            ]
        )

    variant_a = ABVariant(label="rules", agent_mode="rules", seed_profile=True)
    variant_b = ABVariant(
        label="model",
        agent_mode="model",
        seed_profile=True,
        llm_provider_factory=_mock_llm,
    )
    return run_ab_comparison(
        resolved_queries,
        variant_a,
        variant_b,
        comparison="rules-only vs model(ReAct)",
    )


def _print_eval_suite_summary(report: Any) -> None:
    print(
        f"[eval-suite] {report.suite_name}: "
        f"{report.passed_count}/{report.case_count} passed, "
        f"{report.failed_count} failed"
    )
    if report.category_counts:
        counts = ", ".join(
            f"{category}={count}"
            for category, count in sorted(report.category_counts.items())
        )
        print(f"[eval-suite] categories: {counts}")
    if report.passed:
        print("[eval-suite] status: PASS")
        return
    print("[eval-suite] status: FAIL")
    for case in report.cases:
        if case.passed:
            continue
        print(
            f"[eval-suite] {case.case_id} "
            f"(category={case.category}, stop_reason={case.stop_reason})"
        )
        if case.tool_names:
            print("[eval-suite]   tools: " + " -> ".join(case.tool_names))
        for reason in case.failure_reasons:
            print(f"[eval-suite]   - {reason}")


def _run_regression_tests(*, verbosity: int) -> unittest.result.TestResult:
    suite = unittest.defaultTestLoader.discover("tests")
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=verbosity)
    return runner.run(suite)


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

    analyze_ranking = subparsers.add_parser(
        "analyze-ranking",
        help="summarize trajectory feedback signals for ranking-weight tuning",
    )
    analyze_ranking.add_argument("--user-id")
    analyze_ranking.add_argument(
        "--all-trajectories",
        action="store_true",
        help="include trajectories without any feedback events",
    )

    eval_suite = subparsers.add_parser(
        "run-eval-suite",
        help="run the fixed 50-case regression evaluation suite",
    )
    eval_suite.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="run only the specified case id; may be repeated",
    )
    eval_suite.add_argument(
        "--json",
        action="store_true",
        help="print the full report as JSON instead of a human summary",
    )

    regression = subparsers.add_parser(
        "run-regression",
        help="run eval-suite first, then the unit-test regression suite",
    )
    regression.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="run only the specified eval case id; may be repeated",
    )
    regression.add_argument(
        "--json",
        action="store_true",
        help="also print the eval-suite report as JSON before the test run",
    )
    regression.add_argument(
        "--test-verbosity",
        type=int,
        choices=(1, 2),
        default=1,
        help="unittest runner verbosity for the regression test phase",
    )

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

    quality = subparsers.add_parser(
        "trajectory-quality",
        help=(
            "aggregate grounding (hallucination) and ReAct trace quality "
            "metrics; optionally enforce CI thresholds with --gate"
        ),
    )
    quality.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero when metrics breach the quality thresholds",
    )
    quality.add_argument("--max-hallucination-rate", type=float, default=0.5)
    quality.add_argument("--min-grounding-rate", type=float, default=0.3)
    quality.add_argument("--min-thought-coverage-rate", type=float, default=0.9)
    quality.add_argument("--max-missing-thought", type=int, default=0)

    ab = subparsers.add_parser(
        "ab-compare",
        help=(
            "offline A/B comparison of two agent configs on a shared query "
            "batch (default: rules-only vs model/ReAct, runs without API keys)"
        ),
    )
    ab.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="a query to run under both variants; may be repeated",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "schema":
        _print_json(l7_schema())
        return 0
    if args.command == "generate-synthetic":
        result = SyntheticTrajectoryGenerator(args.song_dir).generate(
            args.output_dir,
            count=args.count,
            users=args.users,
            seed=args.seed,
            feedback_rate=args.feedback_rate,
        )
        _print_json(result.to_dict())
        return 0
    if args.command == "ab-compare":
        report = _run_default_ab_comparison(args.queries)
        _print_json(report.to_dict())
        return 0
    if args.command == "run-eval-suite":
        report = RecommendationEvalSuite().run(case_ids=args.case_ids)
        if args.json:
            _print_json(report.to_dict())
        else:
            _print_eval_suite_summary(report)
        return 0 if report.passed else 1
    if args.command == "run-regression":
        report = RecommendationEvalSuite().run(case_ids=args.case_ids)
        if args.json:
            _print_json(report.to_dict())
        else:
            _print_eval_suite_summary(report)
        if not report.passed:
            print("[regression] aborted: eval-suite failed")
            return 1
        print("[regression] eval-suite passed, running unit tests")
        test_result = _run_regression_tests(verbosity=args.test_verbosity)
        return 0 if test_result.wasSuccessful() else 1
    service = TrajectoryDatasetService(args.trajectory_dir)
    if args.command == "trajectory-quality":
        report = load_trajectory_quality(args.trajectory_dir)
        if args.gate:
            gate = check_quality_gate(
                report,
                max_hallucination_rate=args.max_hallucination_rate,
                min_grounding_rate=args.min_grounding_rate,
                min_thought_coverage_rate=args.min_thought_coverage_rate,
                max_missing_thought=args.max_missing_thought,
            )
            _print_json(
                {"metrics": report.to_dict(), "gate": gate.to_dict()}
            )
            if not gate.passed:
                print("[trajectory-quality] gate: FAIL")
                return 1
            print("[trajectory-quality] gate: PASS")
            return 0
        _print_json(report.to_dict())
        return 0
    if args.command == "analyze-ranking":
        report = service.analyze_ranking_feedback(
            user_id=args.user_id,
            feedback_only=not args.all_trajectories,
        )
        _print_json(report.to_dict())
        return 0
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
        return 0
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
        return 0
    report = service.evaluate(
        user_id=args.user_id,
        feedback_only=args.feedback_only,
    )
    _print_json(report.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

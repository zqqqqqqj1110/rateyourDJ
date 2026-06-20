import csv
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rateyourdj.l6 import AgentTrajectory, JsonTrajectoryStore
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l7 import (
    EVAL_CASES_V1,
    RecommendationEvalSuite,
    SyntheticTrajectoryGenerator,
    TrajectoryDatasetService,
    l7_schema,
)
from rateyourdj.l7.cli import main as l7_cli_main
from rateyourdj.l7.models import EvalCaseResult, EvalSuiteReport


def make_trajectory(
    trajectory_id: str,
    *,
    user_id: str = "user-1",
    stop_reason: str = "goal_satisfied",
    recommendations: int = 2,
    feedback_events: list[dict] | None = None,
    feedback_contexts: list[dict] | None = None,
    collection_writes: list[dict] | None = None,
    trajectory_schema_version: str = "trajectory_v1",
    loop_contract_version: str | None = "recommendation_loop_v1",
    tool_schema_version: str | None = "agent_tool_schema_v1",
    user_memory_snapshot: dict | None = None,
    session_memory_snapshot: dict | None = None,
    artist_expansion_snapshot: dict | None = None,
    retrieval_snapshot: dict | None = None,
    fallback_reason: str | None = None,
) -> AgentTrajectory:
    songs = [
        {
            "song_id": f"song-{index}",
            "title": f"Song {index}",
            "artist": "Artist A" if index == 0 else "Artist B",
            "rank": index + 1,
            "final_score": 0.5,
        }
        for index in range(recommendations)
    ]
    return AgentTrajectory(
        trajectory_id=trajectory_id,
        session_id="session-1",
        turn_index=1,
        user_id=user_id,
        query="推荐两首摇滚",
        parsed_request={"top_k": 2},
        plan=[{"tool": "L1.inspect_user_profile"}],
        tool_calls=[
            {
                "tool": "L1.inspect_user_profile",
                "arguments": {"user_id": user_id},
                "observation": {"status": "ok"},
            },
            {
                "tool": "L4.rank_candidates",
                "observation": {"status": "partial"},
            },
        ],
        recommendations=songs,
        response_text="完成推荐",
        feedback_events=feedback_events or [],
        feedback_contexts=feedback_contexts or [],
        collection_writes=collection_writes or [],
        trajectory_schema_version=trajectory_schema_version,
        loop_contract_version=loop_contract_version,
        tool_schema_version=tool_schema_version,
        user_memory_snapshot=user_memory_snapshot or {},
        session_memory_snapshot=session_memory_snapshot or {},
        artist_expansion_snapshot=artist_expansion_snapshot or {},
        retrieval_snapshot=retrieval_snapshot or {},
        stop_reason=stop_reason,
        fallback_reason=fallback_reason,
        created_at=f"2026-06-15T00:00:0{trajectory_id[-1]}+00:00",
    )


class L7ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.trajectory_dir = self.root / "trajectories"
        self.store = JsonTrajectoryStore(self.trajectory_dir)
        self.service = TrajectoryDatasetService(self.trajectory_dir)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_exports_anonymized_jsonl_and_flat_csv(self) -> None:
        self.store.save(
            make_trajectory(
                "trajectory-1",
                feedback_contexts=[
                    {
                        "trajectory_id": "trajectory-1",
                        "user_id": "user-1",
                        "feedback_type": "favorite",
                        "song_id": "song-0",
                        "recommendation_context": {
                            "trajectory_id": "trajectory-1",
                            "source": "web",
                            "track": {
                                "title": "Song 0",
                                "artist": "Artist A",
                            },
                        },
                    }
                ],
                collection_writes=[
                    {
                        "action": "add_track",
                        "scope": "collection",
                        "source": "feedback",
                        "feedback_type": "favorite",
                        "song_id": "song-0",
                        "trajectory_id": "trajectory-1",
                        "user_id": "user-1",
                    }
                ],
                user_memory_snapshot={
                    "user_id": "user-1",
                    "collection_song_ids": ["seed", "song-0"],
                },
                session_memory_snapshot={
                    "session_id": "session-1",
                    "last_user_query": "推荐两首摇滚",
                    "last_recommendation_ids": ["song-0", "song-1"],
                },
                artist_expansion_snapshot={
                    "reference_artists": ["oasis"],
                    "expanded_artists": ["pulp", "suede"],
                },
                retrieval_snapshot={
                    "seed_song_ids": ["seed"],
                    "retrieval_sources": ["local_cache"],
                },
            )
        )
        jsonl_path = self.root / "exports" / "dataset.jsonl"
        csv_path = self.root / "exports" / "dataset.csv"

        jsonl_result = self.service.export(
            jsonl_path,
            anonymization_salt="test-salt",
        )
        csv_result = self.service.export(csv_path, format="csv")

        record = json.loads(jsonl_path.read_text(encoding="utf-8"))
        self.assertEqual(jsonl_result.trajectory_count, 1)
        self.assertTrue(record["user_key"].startswith("user_"))
        self.assertNotEqual(record["user_key"], "user-1")
        self.assertNotIn(
            "user-1",
            jsonl_path.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            record["tool_calls"][0]["arguments"]["user_id"],
            record["user_key"],
        )
        self.assertEqual(
            record["feedback_contexts"][0]["user_id"],
            record["user_key"],
        )
        self.assertEqual(
            record["collection_writes"][0]["user_id"],
            record["user_key"],
        )
        self.assertEqual(
            record["trajectory_schema_version"],
            "trajectory_v1",
        )
        self.assertEqual(
            record["loop_contract_version"],
            "recommendation_loop_v1",
        )
        self.assertEqual(
            record["tool_schema_version"],
            "agent_tool_schema_v1",
        )
        self.assertEqual(
            record["user_memory_snapshot"]["user_id"],
            record["user_key"],
        )
        self.assertEqual(
            record["session_memory_snapshot"]["session_id"],
            "session-1",
        )
        self.assertEqual(
            record["artist_expansion_snapshot"]["expanded_artists"],
            ["pulp", "suede"],
        )
        self.assertEqual(
            record["retrieval_snapshot"]["seed_song_ids"],
            ["seed"],
        )
        self.assertEqual(
            record["feedback_contexts"][0]["recommendation_context"]["source"],
            "web",
        )
        self.assertEqual(
            record["collection_writes"][0]["action"],
            "add_track",
        )
        self.assertEqual(record["recommendation_count"], 2)
        with csv_path.open(encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(csv_result.trajectory_count, 1)
        self.assertEqual(rows[0]["trajectory_id"], "trajectory-1")
        self.assertEqual(rows[0]["quantity_satisfied"], "True")
        self.assertNotIn("feedback_contexts", rows[0])
        self.assertNotIn("collection_writes", rows[0])
        self.assertNotIn("trajectory_schema_version", rows[0])
        self.assertNotIn("loop_contract_version", rows[0])
        self.assertNotIn("tool_schema_version", rows[0])
        self.assertNotIn("user_memory_snapshot", rows[0])
        self.assertNotIn("session_memory_snapshot", rows[0])
        self.assertNotIn("artist_expansion_snapshot", rows[0])
        self.assertNotIn("retrieval_snapshot", rows[0])

    def test_evaluates_feedback_and_agent_metrics(self) -> None:
        self.store.save(
            make_trajectory(
                "trajectory-1",
                feedback_events=[
                    {
                        "feedback_type": "like",
                        "reward_score": 0.6,
                    },
                    {
                        "feedback_type": "skip",
                        "reward_score": -0.4,
                    },
                ],
            )
        )
        self.store.save(
            make_trajectory(
                "trajectory-2",
                stop_reason="insufficient_candidates",
                recommendations=1,
                fallback_reason="provider failed",
            )
        )

        report = self.service.evaluate()

        self.assertEqual(report.trajectory_count, 2)
        self.assertEqual(report.goal_satisfied_rate, 0.5)
        self.assertEqual(report.quantity_satisfied_rate, 0.5)
        self.assertEqual(report.feedback_coverage_rate, 0.5)
        self.assertEqual(report.tool_call_success_rate, 1.0)
        self.assertEqual(report.fallback_rate, 0.5)
        self.assertEqual(report.average_reward, 0.1)
        self.assertEqual(report.positive_feedback_rate, 0.5)
        self.assertEqual(report.negative_feedback_rate, 0.5)
        self.assertEqual(report.skip_rate, 0.5)
        self.assertEqual(report.favorite_rate, 0.0)
        self.assertEqual(report.artist_diversity, 1.0)

    def test_analyzes_ranking_feedback_signals(self) -> None:
        self.store.save(
            make_trajectory(
                "trajectory-1",
                feedback_events=[
                    {"feedback_type": "like", "reward_score": 0.6},
                    {"feedback_type": "skip", "reward_score": -0.4},
                ],
                feedback_contexts=[
                    {
                        "reward_score": 0.6,
                        "recommendation_rank": 1,
                        "recommended_final_score": 0.9,
                        "feedback_source": "web",
                    },
                    {
                        "reward_score": -0.4,
                        "recommendation_rank": 2,
                        "recommended_final_score": 0.3,
                        "feedback_source": "spotify_embed",
                    },
                ],
                collection_writes=[
                    {
                        "action": "add_track",
                        "feedback_type": "favorite",
                    }
                ],
            )
        )

        report = self.service.analyze_ranking_feedback()

        self.assertEqual(report.trajectory_count, 1)
        self.assertEqual(report.feedback_event_count, 2)
        self.assertEqual(report.contextual_feedback_count, 2)
        self.assertEqual(report.collection_write_count, 1)
        self.assertEqual(report.average_reward, 0.1)
        self.assertEqual(report.reward_by_rank["1"], 0.6)
        self.assertEqual(report.reward_by_rank["2"], -0.4)
        self.assertEqual(report.feedback_count_by_source["web"], 1)
        self.assertEqual(report.feedback_count_by_source["spotify_embed"], 1)
        self.assertIn("base_score_weights", report.current_weights)

    def test_feedback_filter_and_invalid_files(self) -> None:
        self.store.save(make_trajectory("trajectory-1"))
        self.store.save(
            make_trajectory(
                "trajectory-2",
                feedback_events=[
                    {"feedback_type": "favorite", "reward_score": 0.8}
                ],
            )
        )
        invalid = self.trajectory_dir / "user-1" / "broken.json"
        invalid.write_text("{", encoding="utf-8")

        report = self.service.evaluate(feedback_only=True)

        self.assertEqual(report.trajectory_count, 1)
        self.assertEqual(report.favorite_rate, 1.0)
        self.assertEqual(report.skipped_files, [str(invalid)])

    def test_rejects_unsafe_user_scope(self) -> None:
        with self.assertRaises(ValueError):
            self.service.evaluate(user_id="../other")

    def test_splits_by_user_without_cross_split_leakage(self) -> None:
        for user_index in range(10):
            user_id = f"user-{user_index}"
            for trajectory_index in range(2):
                self.store.save(
                    make_trajectory(
                        f"trajectory-{user_index}-{trajectory_index}",
                        user_id=user_id,
                    )
                )
        output_dir = self.root / "split"

        result = self.service.split_by_user(
            output_dir,
            train_ratio=0.6,
            validation_ratio=0.2,
            test_ratio=0.2,
            seed=42,
        )

        self.assertEqual(result.trajectory_count, 20)
        self.assertEqual(
            result.split_user_counts,
            {"train": 6, "validation": 2, "test": 2},
        )
        user_sets = {}
        for split_name in ("train", "validation", "test"):
            records = [
                json.loads(line)
                for line in (
                    output_dir / f"{split_name}.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            user_sets[split_name] = {
                record["user_key"] for record in records
            }
        self.assertTrue(user_sets["train"].isdisjoint(user_sets["validation"]))
        self.assertTrue(user_sets["train"].isdisjoint(user_sets["test"]))
        self.assertTrue(
            user_sets["validation"].isdisjoint(user_sets["test"])
        )
        manifest = json.loads(
            (output_dir / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["split_trajectory_counts"]["train"], 12)

    def test_split_validates_ratios_and_existing_output(self) -> None:
        self.store.save(make_trajectory("trajectory-1"))
        with self.assertRaises(ValueError):
            self.service.split_by_user(
                self.root / "invalid-ratio",
                train_ratio=0.7,
                validation_ratio=0.2,
                test_ratio=0.2,
            )
        output_dir = self.root / "split"
        self.service.split_by_user(output_dir)
        with self.assertRaises(FileExistsError):
            self.service.split_by_user(output_dir)


class SyntheticTrajectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.song_dir = self.root / "songs"
        song_store = JsonSongStore(self.song_dir)
        for index, genre in enumerate(("rock", "jazz", "soul", "rock")):
            song = SongProfile.empty(f"song-{index}")
            song.metadata.update(
                {
                    "title": f"Song {index}",
                    "artist": f"Artist {index}",
                    "album": "Synthetic Album",
                    "release_year": 2000,
                    "duration_ms": 200_000,
                    "version_type": "original",
                }
            )
            song.genres = {genre: 1.0}
            song_store.save(song)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_generates_reproducible_isolated_trajectories(self) -> None:
        first_dir = self.root / "first"
        second_dir = self.root / "second"
        generator = SyntheticTrajectoryGenerator(self.song_dir)

        first = generator.generate(
            first_dir,
            count=12,
            users=3,
            seed=42,
            feedback_rate=1.0,
        )
        second = generator.generate(
            second_dir,
            count=12,
            users=3,
            seed=42,
            feedback_rate=1.0,
        )

        first_files = sorted(first_dir.glob("*/*.json"))
        second_files = sorted(second_dir.glob("*/*.json"))
        self.assertEqual(first.trajectory_count, 12)
        self.assertEqual(first.user_count, 3)
        self.assertEqual(len(first_files), 12)
        self.assertGreater(first.feedback_event_count, 0)
        self.assertEqual(
            [path.name for path in first_files],
            [path.name for path in second_files],
        )
        trajectory = AgentTrajectory.from_dict(
            json.loads(first_files[0].read_text(encoding="utf-8"))
        )
        self.assertTrue(trajectory.user_id.startswith("synthetic-user-"))
        self.assertTrue(
            trajectory.trajectory_id.startswith("synthetic-trajectory-")
        )
        self.assertTrue(trajectory.feedback_events)
        report = TrajectoryDatasetService(first_dir).evaluate()
        self.assertEqual(report.trajectory_count, 12)
        self.assertEqual(report.user_count, 3)
        self.assertEqual(report.feedback_coverage_rate, 1.0)

    def test_rejects_existing_output_and_invalid_limits(self) -> None:
        output_dir = self.root / "output"
        generator = SyntheticTrajectoryGenerator(self.song_dir)
        generator.generate(output_dir, count=1, users=1)

        with self.assertRaises(FileExistsError):
            generator.generate(output_dir, count=1, users=1)
        with self.assertRaises(ValueError):
            generator.generate(self.root / "invalid", count=2, users=3)
        with self.assertRaises(ValueError):
            generator.generate(
                self.root / "invalid-rate",
                count=2,
                users=1,
                feedback_rate=1.1,
            )


class EvalSuiteTests(unittest.TestCase):
    def test_eval_case_catalog_contains_50_unique_cases(self) -> None:
        case_ids = [case["id"] for case in EVAL_CASES_V1]

        self.assertEqual(len(EVAL_CASES_V1), 50)
        self.assertEqual(len(case_ids), len(set(case_ids)))

    def test_eval_suite_runs_cross_category_subset(self) -> None:
        report = RecommendationEvalSuite().run(
            case_ids=[
                "basic-rock-1",
                "session-more-1",
                "feedback-skip-1",
                "provider-britpop-1",
                "edge-empty-profile-1",
            ]
        )

        self.assertEqual(report.case_count, 5)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.passed_count, 5)

    def test_eval_suite_runs_full_catalog(self) -> None:
        report = RecommendationEvalSuite().run()

        self.assertEqual(report.case_count, 50)
        self.assertEqual(report.failed_count, 0)


class L7CliTests(unittest.TestCase):
    def test_schema_command_prints_l7_contract(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rateyourdj.l7.cli",
                "schema",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload, l7_schema())

    def test_evaluate_command_prints_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = JsonTrajectoryStore(root / "trajectories")
            store.save(make_trajectory("trajectory-1"))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "rateyourdj.l7.cli",
                    "--trajectory-dir",
                    str(root / "trajectories"),
                    "evaluate",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["trajectory_count"], 1)
        self.assertEqual(payload["goal_satisfied_rate"], 1.0)

    def test_analyze_ranking_command_prints_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = JsonTrajectoryStore(root / "trajectories")
            store.save(
                make_trajectory(
                    "trajectory-1",
                    feedback_events=[{"feedback_type": "like", "reward_score": 0.6}],
                    feedback_contexts=[
                        {
                            "reward_score": 0.6,
                            "recommendation_rank": 1,
                            "recommended_final_score": 0.8,
                            "feedback_source": "web",
                        }
                    ],
                )
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "rateyourdj.l7.cli",
                    "--trajectory-dir",
                    str(root / "trajectories"),
                    "analyze-ranking",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["trajectory_count"], 1)
        self.assertEqual(payload["feedback_event_count"], 1)
        self.assertEqual(payload["reward_by_rank"]["1"], 0.6)

    def test_run_eval_suite_prints_human_summary_and_returns_success(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rateyourdj.l7.cli",
                "run-eval-suite",
                "--case-id",
                "basic-rock-1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("[eval-suite] eval_cases_v1: 1/1 passed, 0 failed", result.stdout)
        self.assertIn("[eval-suite] status: PASS", result.stdout)

    def test_run_eval_suite_returns_failure_and_prints_failure_summary(self) -> None:
        report = EvalSuiteReport(
            suite_name="eval_cases_v1",
            case_count=1,
            passed_count=0,
            failed_count=1,
            category_counts={"basic": 1},
            failed_case_ids=["broken-case"],
            cases=[
                EvalCaseResult(
                    case_id="broken-case",
                    category="basic",
                    passed=False,
                    failure_reasons=["missing expected track rock-a"],
                    stop_reason="goal_satisfied",
                    recommendation_count=1,
                    tool_names=["get_user_memory", "rank_candidates"],
                )
            ],
        )
        stdout = StringIO()
        with (
            patch("rateyourdj.l7.cli.RecommendationEvalSuite.run", return_value=report),
            patch.object(
                sys,
                "argv",
                ["rateyourdj-l7", "run-eval-suite"],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = l7_cli_main()

        output = stdout.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("[eval-suite] status: FAIL", output)
        self.assertIn("[eval-suite] broken-case", output)
        self.assertIn("missing expected track rock-a", output)
        self.assertIn("get_user_memory -> rank_candidates", output)

    def test_run_regression_runs_tests_after_eval_suite_passes(self) -> None:
        report = EvalSuiteReport(
            suite_name="eval_cases_v1",
            case_count=1,
            passed_count=1,
            failed_count=0,
            category_counts={"basic": 1},
            failed_case_ids=[],
            cases=[
                EvalCaseResult(
                    case_id="basic-rock-1",
                    category="basic",
                    passed=True,
                    failure_reasons=[],
                    stop_reason="goal_satisfied",
                    recommendation_count=5,
                    tool_names=["get_user_memory", "rank_candidates"],
                )
            ],
        )
        fake_result = unittest.TestResult()
        stdout = StringIO()
        with (
            patch("rateyourdj.l7.cli.RecommendationEvalSuite.run", return_value=report),
            patch("rateyourdj.l7.cli._run_regression_tests", return_value=fake_result) as test_run,
            patch.object(
                sys,
                "argv",
                ["rateyourdj-l7", "run-regression"],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = l7_cli_main()

        self.assertEqual(exit_code, 0)
        test_run.assert_called_once_with(verbosity=1)
        self.assertIn("[regression] eval-suite passed, running unit tests", stdout.getvalue())

    def test_run_regression_aborts_when_eval_suite_fails(self) -> None:
        report = EvalSuiteReport(
            suite_name="eval_cases_v1",
            case_count=1,
            passed_count=0,
            failed_count=1,
            category_counts={"basic": 1},
            failed_case_ids=["broken-case"],
            cases=[
                EvalCaseResult(
                    case_id="broken-case",
                    category="basic",
                    passed=False,
                    failure_reasons=["result count below minimum"],
                    stop_reason="goal_satisfied",
                    recommendation_count=0,
                    tool_names=[],
                )
            ],
        )
        stdout = StringIO()
        with (
            patch("rateyourdj.l7.cli.RecommendationEvalSuite.run", return_value=report),
            patch("rateyourdj.l7.cli._run_regression_tests") as test_run,
            patch.object(
                sys,
                "argv",
                ["rateyourdj-l7", "run-regression"],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = l7_cli_main()

        self.assertEqual(exit_code, 1)
        test_run.assert_not_called()
        self.assertIn("[regression] aborted: eval-suite failed", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

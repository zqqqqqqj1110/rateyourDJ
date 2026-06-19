import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rateyourdj.l6 import AgentTrajectory, JsonTrajectoryStore
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l7 import (
    SyntheticTrajectoryGenerator,
    TrajectoryDatasetService,
    l7_schema,
)


def make_trajectory(
    trajectory_id: str,
    *,
    user_id: str = "user-1",
    stop_reason: str = "goal_satisfied",
    recommendations: int = 2,
    feedback_events: list[dict] | None = None,
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
        self.store.save(make_trajectory("trajectory-1"))
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
        self.assertEqual(record["recommendation_count"], 2)
        with csv_path.open(encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(csv_result.trajectory_count, 1)
        self.assertEqual(rows[0]["trajectory_id"], "trajectory-1")
        self.assertEqual(rows[0]["quantity_satisfied"], "True")

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


if __name__ == "__main__":
    unittest.main()

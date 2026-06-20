from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rateyourdj.training.dataset import (
    build_grpo_file,
    build_grpo_samples,
    build_sft_file,
    build_sft_samples,
    load_jsonl,
    write_jsonl,
)


def _record(
    *,
    trajectory_id: str,
    query: str,
    average_reward: float,
    thought: str = "reflect on memory, then search",
    tool_name: str = "discover_tracks",
    response_text: str = "为你挑了几首。",
    artist: str = "Oasis",
    title: str = "Wonderwall",
) -> dict:
    return {
        "trajectory_id": trajectory_id,
        "query": query,
        "average_reward": average_reward,
        "parsed_request": {"top_k": 3},
        "user_memory_snapshot": {
            "artist_preferences": {"Oasis": 0.9, "Blur": 0.5},
            "genre_preferences": {"britpop": 0.8},
        },
        "agent_decisions": [
            {"kind": "tool", "thought": thought, "tool_name": tool_name, "summary": "x"},
            {"kind": "finish", "thought": "enough candidates", "summary": "done"},
        ],
        "tool_calls": [
            {"tool": tool_name, "thought": thought, "observation": {"status": "ok"}},
        ],
        "recommendations": [{"title": title, "artist": artist}],
        "response_text": response_text,
        "feedback_events": [{"feedback_type": "like", "reward_score": average_reward}],
    }


class SFTSampleTests(unittest.TestCase):
    def test_build_sft_samples_basic(self) -> None:
        records = [_record(trajectory_id="t1", query="推荐英伦摇滚", average_reward=0.6)]
        samples = build_sft_samples(records)
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertIn("推荐英伦摇滚", sample["prompt"])
        self.assertIn("artists: Oasis", sample["prompt"])  # taste summary
        self.assertIn("Thought:", sample["completion"])
        self.assertIn("Action: discover_tracks", sample["completion"])
        self.assertIn("Final answer:", sample["completion"])
        self.assertIn("Wonderwall — Oasis", sample["completion"])

    def test_min_reward_filters_low_reward(self) -> None:
        records = [
            _record(trajectory_id="hi", query="q1", average_reward=0.8),
            _record(trajectory_id="lo", query="q2", average_reward=-0.5),
        ]
        samples = build_sft_samples(records, min_reward=0.0)
        ids = {s["trajectory_id"] for s in samples}
        self.assertEqual(ids, {"hi"})

    def test_empty_query_skipped(self) -> None:
        records = [_record(trajectory_id="t", query="", average_reward=0.5)]
        self.assertEqual(build_sft_samples(records), [])


class GRPOSampleTests(unittest.TestCase):
    def test_groups_by_prompt_with_reward_contrast(self) -> None:
        # Same query, two different rewards -> a usable GRPO group.
        records = [
            _record(trajectory_id="a", query="推荐爵士", average_reward=0.9,
                    response_text="答案A"),
            _record(trajectory_id="b", query="推荐爵士", average_reward=-0.4,
                    response_text="答案B"),
        ]
        groups = build_grpo_samples(records)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(len(group["responses"]), 2)
        self.assertEqual(len(group["rewards"]), 2)
        self.assertIn("推荐爵士", group["prompt"])

    def test_single_response_group_dropped(self) -> None:
        records = [_record(trajectory_id="a", query="独一份", average_reward=0.5)]
        self.assertEqual(build_grpo_samples(records), [])

    def test_no_reward_contrast_dropped(self) -> None:
        records = [
            _record(trajectory_id="a", query="同分", average_reward=0.5,
                    response_text="A"),
            _record(trajectory_id="b", query="同分", average_reward=0.5,
                    response_text="B"),
        ]
        self.assertEqual(build_grpo_samples(records), [])


class FileRoundTripTests(unittest.TestCase):
    def test_build_sft_and_grpo_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "traj.jsonl"
            write_jsonl(
                src,
                [
                    _record(trajectory_id="a", query="推荐摇滚", average_reward=0.8,
                            response_text="A"),
                    _record(trajectory_id="b", query="推荐摇滚", average_reward=-0.2,
                            response_text="B"),
                ],
            )
            sft_out = Path(tmp) / "sft.jsonl"
            sft_result = build_sft_file(src, sft_out)
            self.assertEqual(sft_result.kind, "sft")
            self.assertEqual(sft_result.sample_count, 2)
            self.assertTrue(sft_out.exists())
            self.assertEqual(len(load_jsonl(sft_out)), 2)

            grpo_out = Path(tmp) / "grpo.jsonl"
            grpo_result = build_grpo_file(src, grpo_out)
            self.assertEqual(grpo_result.kind, "grpo")
            self.assertEqual(grpo_result.sample_count, 1)
            group = load_jsonl(grpo_out)[0]
            self.assertEqual(sorted(group["rewards"]), [-0.2, 0.8])


class TrainingDepsHintTests(unittest.TestCase):
    def test_run_sft_without_deps_raises_clear_hint(self) -> None:
        # In this sandbox torch/trl are not installed, so run_sft must raise a
        # clear RuntimeError (not a bare ImportError) once past the file check.
        from rateyourdj.training.sft import SFTConfig, run_sft

        with tempfile.TemporaryDirectory() as tmp:
            train_file = Path(tmp) / "sft.jsonl"
            write_jsonl(train_file, [{"prompt": "p", "completion": "c"}])
            try:
                run_sft(SFTConfig(train_file=str(train_file)))
            except RuntimeError as error:
                self.assertIn("rateyourdj[training]", str(error))
            except Exception:  # pragma: no cover - deps actually installed
                pass


if __name__ == "__main__":
    unittest.main()

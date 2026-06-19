from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ExportResult:
    format: str
    output_path: str
    trajectory_count: int
    skipped_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "output_path": self.output_path,
            "trajectory_count": self.trajectory_count,
            "skipped_files": list(self.skipped_files),
        }


@dataclass(frozen=True, slots=True)
class SyntheticGenerationResult:
    output_dir: str
    trajectory_count: int
    user_count: int
    session_count: int
    feedback_event_count: int
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_dir,
            "trajectory_count": self.trajectory_count,
            "user_count": self.user_count,
            "session_count": self.session_count,
            "feedback_event_count": self.feedback_event_count,
            "seed": self.seed,
            "synthetic": True,
        }


@dataclass(frozen=True, slots=True)
class DatasetSplitResult:
    output_dir: str
    trajectory_count: int
    user_count: int
    split_trajectory_counts: dict[str, int]
    split_user_counts: dict[str, int]
    skipped_files: list[str]
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_dir,
            "trajectory_count": self.trajectory_count,
            "user_count": self.user_count,
            "split_trajectory_counts": dict(self.split_trajectory_counts),
            "split_user_counts": dict(self.split_user_counts),
            "skipped_files": list(self.skipped_files),
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    trajectory_count: int
    user_count: int
    session_count: int
    goal_satisfied_rate: float
    quantity_satisfied_rate: float
    feedback_coverage_rate: float
    average_recommendations: float
    average_tool_calls: float
    tool_call_success_rate: float
    fallback_rate: float
    average_reward: float
    positive_feedback_rate: float
    negative_feedback_rate: float
    skip_rate: float
    favorite_rate: float
    artist_diversity: float
    stop_reason_counts: dict[str, int]
    agent_mode_counts: dict[str, int]
    feedback_type_counts: dict[str, int]
    skipped_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_count": self.trajectory_count,
            "user_count": self.user_count,
            "session_count": self.session_count,
            "goal_satisfied_rate": self.goal_satisfied_rate,
            "quantity_satisfied_rate": self.quantity_satisfied_rate,
            "feedback_coverage_rate": self.feedback_coverage_rate,
            "average_recommendations": self.average_recommendations,
            "average_tool_calls": self.average_tool_calls,
            "tool_call_success_rate": self.tool_call_success_rate,
            "fallback_rate": self.fallback_rate,
            "average_reward": self.average_reward,
            "positive_feedback_rate": self.positive_feedback_rate,
            "negative_feedback_rate": self.negative_feedback_rate,
            "skip_rate": self.skip_rate,
            "favorite_rate": self.favorite_rate,
            "artist_diversity": self.artist_diversity,
            "stop_reason_counts": dict(self.stop_reason_counts),
            "agent_mode_counts": dict(self.agent_mode_counts),
            "feedback_type_counts": dict(self.feedback_type_counts),
            "skipped_files": list(self.skipped_files),
        }


def l7_schema() -> dict[str, Any]:
    return {
        "input": "L6 trajectory JSON files",
        "exports": {
            "jsonl": "one anonymized, training-ready trajectory per line",
            "csv": "one flattened summary row per trajectory",
        },
        "filters": {
            "user_id": "optional exact user scope",
            "feedback_only": "include only trajectories with feedback",
        },
        "synthetic_generation": {
            "purpose": "pipeline and evaluation testing only",
            "isolation": "write under a separate synthetic trajectory root",
            "identifiers": "users and trajectories use synthetic prefixes",
        },
        "dataset_split": {
            "unit": "user_id",
            "outputs": ["train.jsonl", "validation.jsonl", "test.jsonl"],
            "guarantee": "one user appears in exactly one split",
        },
        "evaluation": {
            "goal_satisfied_rate": "stop_reason == goal_satisfied",
            "quantity_satisfied_rate": (
                "recommendation count reaches parsed top_k"
            ),
            "feedback_coverage_rate": (
                "trajectories containing at least one feedback event"
            ),
            "tool_call_success_rate": (
                "tool observations with status ok or partial"
            ),
            "skip_rate": "skip and quick_skip share of feedback events",
            "favorite_rate": (
                "favorite and playlist_add share of feedback events"
            ),
            "artist_diversity": (
                "unique recommended artists divided by recommendations"
            ),
        },
    }

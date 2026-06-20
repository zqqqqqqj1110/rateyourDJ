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
    average_latency_ms: float
    p95_latency_ms: float
    latency_sample_count: int
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
            "average_latency_ms": self.average_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "latency_sample_count": self.latency_sample_count,
            "stop_reason_counts": dict(self.stop_reason_counts),
            "agent_mode_counts": dict(self.agent_mode_counts),
            "feedback_type_counts": dict(self.feedback_type_counts),
            "skipped_files": list(self.skipped_files),
        }


@dataclass(frozen=True, slots=True)
class EvalCaseResult:
    case_id: str
    category: str
    passed: bool
    failure_reasons: list[str]
    stop_reason: str
    recommendation_count: int
    tool_names: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "failure_reasons": list(self.failure_reasons),
            "stop_reason": self.stop_reason,
            "recommendation_count": self.recommendation_count,
            "tool_names": list(self.tool_names),
        }


@dataclass(frozen=True, slots=True)
class EvalSuiteReport:
    suite_name: str
    case_count: int
    passed_count: int
    failed_count: int
    category_counts: dict[str, int]
    failed_case_ids: list[str]
    cases: list[EvalCaseResult]

    @property
    def passed(self) -> bool:
        return self.failed_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "category_counts": dict(self.category_counts),
            "failed_case_ids": list(self.failed_case_ids),
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True, slots=True)
class RankingTuningReport:
    trajectory_count: int
    feedback_event_count: int
    contextual_feedback_count: int
    collection_write_count: int
    average_reward: float
    current_weights: dict[str, Any]
    reward_by_rank: dict[str, float]
    positive_rate_by_rank: dict[str, float]
    negative_rate_by_rank: dict[str, float]
    reward_by_score_bucket: dict[str, float]
    feedback_count_by_source: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_count": self.trajectory_count,
            "feedback_event_count": self.feedback_event_count,
            "contextual_feedback_count": self.contextual_feedback_count,
            "collection_write_count": self.collection_write_count,
            "average_reward": self.average_reward,
            "current_weights": dict(self.current_weights),
            "reward_by_rank": dict(self.reward_by_rank),
            "positive_rate_by_rank": dict(self.positive_rate_by_rank),
            "negative_rate_by_rank": dict(self.negative_rate_by_rank),
            "reward_by_score_bucket": dict(self.reward_by_score_bucket),
            "feedback_count_by_source": dict(self.feedback_count_by_source),
        }


@dataclass(frozen=True, slots=True)
class ABVariantMetrics:
    """Aggregated metrics for one variant (arm) of an offline A/B comparison."""

    label: str
    query_count: int
    average_recommendations: float
    non_empty_rate: float
    tool_call_success_rate: float
    grounding_rate: float
    average_hallucination_rate: float
    thought_coverage_rate: float
    average_latency_ms: float
    p95_latency_ms: float
    fallback_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "query_count": self.query_count,
            "average_recommendations": self.average_recommendations,
            "non_empty_rate": self.non_empty_rate,
            "tool_call_success_rate": self.tool_call_success_rate,
            "grounding_rate": self.grounding_rate,
            "average_hallucination_rate": self.average_hallucination_rate,
            "thought_coverage_rate": self.thought_coverage_rate,
            "average_latency_ms": self.average_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "fallback_rate": self.fallback_rate,
        }


@dataclass(frozen=True, slots=True)
class ABComparisonReport:
    """Side-by-side offline comparison of two agent configurations."""

    comparison: str
    query_count: int
    variant_a: ABVariantMetrics
    variant_b: ABVariantMetrics
    deltas: dict[str, float]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparison": self.comparison,
            "query_count": self.query_count,
            "variant_a": self.variant_a.to_dict(),
            "variant_b": self.variant_b.to_dict(),
            "deltas": dict(self.deltas),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class TrajectoryQualityReport:
    """Grounding-quality and ReAct-trace-quality metrics over trajectories."""

    trajectory_count: int
    # grounding (hallucination) quality
    discovery_trajectory_count: int
    discovery_coverage_rate: float
    total_generated: int
    total_grounded: int
    total_dropped: int
    average_hallucination_rate: float
    grounding_rate: float
    # ReAct trace quality
    model_decision_count: int
    decisions_with_thought: int
    thought_coverage_rate: float
    average_thoughts_per_trajectory: float
    average_thought_length: float
    model_decisions_missing_thought: int
    skipped_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_count": self.trajectory_count,
            "discovery_trajectory_count": self.discovery_trajectory_count,
            "discovery_coverage_rate": self.discovery_coverage_rate,
            "total_generated": self.total_generated,
            "total_grounded": self.total_grounded,
            "total_dropped": self.total_dropped,
            "average_hallucination_rate": self.average_hallucination_rate,
            "grounding_rate": self.grounding_rate,
            "model_decision_count": self.model_decision_count,
            "decisions_with_thought": self.decisions_with_thought,
            "thought_coverage_rate": self.thought_coverage_rate,
            "average_thoughts_per_trajectory": (
                self.average_thoughts_per_trajectory
            ),
            "average_thought_length": self.average_thought_length,
            "model_decisions_missing_thought": (
                self.model_decisions_missing_thought
            ),
            "skipped_files": list(self.skipped_files),
        }


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    """Pass/fail outcome of checking quality metrics against thresholds."""

    passed: bool
    failures: list[str]
    checked: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failures": list(self.failures),
            "checked": dict(self.checked),
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
        "eval_suite": {
            "format": "fixed case list with session setup and property assertions",
            "default_case_count": 50,
            "goal": "regression checks for loop, session memory, and ranking",
        },
    }

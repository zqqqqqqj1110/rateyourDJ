"""Grounding-quality and ReAct-trace-quality metrics over agent trajectories.

The existing eval suite checks *behavioral correctness* (stop reasons, tool
paths, exclusions). This module adds two complementary quality lenses that the
project's narrative leans on — grounding integrity and explicit reasoning — and
a threshold gate so they can guard CI.

Grounding quality
    `discover_tracks` records `generated`/`grounded`/`dropped`/
    `hallucination_rate` in its observation. We aggregate those across
    trajectories: how often discovery ran, how many proposed tracks survived
    provider confirmation, and the mean hallucination rate. A *rising*
    hallucination rate is an early warning that the generator drifted from real
    catalog tracks.

ReAct trace quality
    Each model decision should carry an explicit `thought` (the Reasoning half
    of ReAct). We measure thought coverage, average thoughts per trajectory and
    average thought length, and count model decisions that are missing a thought
    (which should be ~0 for a true ReAct agent). Program-first steps
    (decision_source != "model") are excluded — they legitimately have no model
    thought.

Both inputs accept either L7 dataset records (from `dataset_record`) or raw
trajectory dicts (`AgentTrajectory.to_dict`), since both expose the same
`tool_calls` / `agent_decisions` shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .models import QualityGateResult, TrajectoryQualityReport


def compute_trajectory_quality(
    records: Iterable[dict[str, Any]],
    *,
    skipped_files: list[str] | None = None,
) -> TrajectoryQualityReport:
    records = [record for record in records if isinstance(record, dict)]
    trajectory_count = len(records)

    discovery_trajectory_count = 0
    total_generated = 0
    total_grounded = 0
    total_dropped = 0
    hallucination_rates: list[float] = []

    model_decision_count = 0
    decisions_with_thought = 0
    thought_lengths: list[int] = []
    thoughts_per_trajectory: list[int] = []

    for record in records:
        # --- grounding quality (from discover_tracks observations) ---
        had_discovery = False
        for call in _as_list(record.get("tool_calls")):
            data = _observation_data(call)
            if call.get("tool") != "discover_tracks" or data is None:
                continue
            had_discovery = True
            generated = _as_int(data.get("generated"))
            grounded = _as_int(data.get("grounded"))
            dropped = _as_int(data.get("dropped"))
            total_generated += generated
            total_grounded += grounded
            total_dropped += dropped
            rate = data.get("hallucination_rate")
            if _is_number(rate):
                hallucination_rates.append(float(rate))
            elif generated > 0:
                hallucination_rates.append(round(dropped / generated, 6))
        if had_discovery:
            discovery_trajectory_count += 1

        # --- ReAct trace quality (from model decisions) ---
        trajectory_thoughts = 0
        for decision in _as_list(record.get("agent_decisions")):
            if not _is_model_decision(decision):
                continue
            model_decision_count += 1
            thought = str(decision.get("thought") or "").strip()
            if thought:
                decisions_with_thought += 1
                thought_lengths.append(len(thought))
                trajectory_thoughts += 1
        thoughts_per_trajectory.append(trajectory_thoughts)

    return TrajectoryQualityReport(
        trajectory_count=trajectory_count,
        discovery_trajectory_count=discovery_trajectory_count,
        discovery_coverage_rate=_ratio(discovery_trajectory_count, trajectory_count),
        total_generated=total_generated,
        total_grounded=total_grounded,
        total_dropped=total_dropped,
        average_hallucination_rate=_average(hallucination_rates),
        grounding_rate=_ratio(total_grounded, total_generated),
        model_decision_count=model_decision_count,
        decisions_with_thought=decisions_with_thought,
        thought_coverage_rate=_ratio(decisions_with_thought, model_decision_count),
        average_thoughts_per_trajectory=_average(thoughts_per_trajectory),
        average_thought_length=_average(thought_lengths),
        model_decisions_missing_thought=(
            model_decision_count - decisions_with_thought
        ),
        skipped_files=list(skipped_files or []),
    )


def check_quality_gate(
    report: TrajectoryQualityReport,
    *,
    max_hallucination_rate: float = 0.5,
    min_grounding_rate: float = 0.3,
    min_thought_coverage_rate: float = 0.9,
    max_missing_thought: int = 0,
) -> QualityGateResult:
    """Fail (for CI) when quality metrics breach thresholds.

    Metrics whose denominator is empty (e.g. no discovery ran) are skipped
    rather than failed, so the gate is meaningful on small/partial datasets.
    """
    failures: list[str] = []
    checked: dict[str, Any] = {}

    if report.total_generated > 0:
        checked["average_hallucination_rate"] = report.average_hallucination_rate
        checked["grounding_rate"] = report.grounding_rate
        if report.average_hallucination_rate > max_hallucination_rate:
            failures.append(
                f"average_hallucination_rate {report.average_hallucination_rate} "
                f"> max {max_hallucination_rate}"
            )
        if report.grounding_rate < min_grounding_rate:
            failures.append(
                f"grounding_rate {report.grounding_rate} < min {min_grounding_rate}"
            )

    if report.model_decision_count > 0:
        checked["thought_coverage_rate"] = report.thought_coverage_rate
        checked["model_decisions_missing_thought"] = (
            report.model_decisions_missing_thought
        )
        if report.thought_coverage_rate < min_thought_coverage_rate:
            failures.append(
                f"thought_coverage_rate {report.thought_coverage_rate} "
                f"< min {min_thought_coverage_rate}"
            )
        if report.model_decisions_missing_thought > max_missing_thought:
            failures.append(
                f"model_decisions_missing_thought "
                f"{report.model_decisions_missing_thought} > max {max_missing_thought}"
            )

    return QualityGateResult(
        passed=not failures,
        failures=failures,
        checked=checked,
    )


def load_trajectory_quality(
    trajectory_dir: str | Path,
) -> TrajectoryQualityReport:
    """Aggregate quality directly from a directory of L6 trajectory JSON files."""
    root = Path(trajectory_dir)
    records: list[dict[str, Any]] = []
    skipped: list[str] = []
    if root.exists():
        for path in sorted(root.glob("*/*.json")):
            try:
                with path.open("r", encoding="utf-8") as file:
                    value = json.load(file)
            except (OSError, ValueError, json.JSONDecodeError):
                skipped.append(str(path))
                continue
            if isinstance(value, dict):
                records.append(value)
    return compute_trajectory_quality(records, skipped_files=skipped)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _observation_data(call: Any) -> dict[str, Any] | None:
    if not isinstance(call, dict):
        return None
    observation = call.get("observation")
    if not isinstance(observation, dict):
        return None
    data = observation.get("data")
    return data if isinstance(data, dict) else None


def _is_model_decision(decision: Any) -> bool:
    if not isinstance(decision, dict):
        return False
    # Program-injected decisions use a "kind" like program_discovery_first /
    # program_finish / fallback; real model steps use tool/update/finish.
    return decision.get("kind") in {"tool", "update", "finish"}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _average(values: Iterable[float | int]) -> float:
    items = [float(value) for value in values]
    if not items:
        return 0.0
    return round(sum(items) / len(items), 6)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)

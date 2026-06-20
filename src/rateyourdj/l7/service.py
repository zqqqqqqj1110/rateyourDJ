from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from rateyourdj.l4 import RankingWeights
from rateyourdj.l6 import AgentTrajectory

from .models import (
    DatasetSplitResult,
    EvaluationReport,
    ExportResult,
    RankingTuningReport,
)


CSV_FIELDS = [
    "trajectory_id",
    "user_key",
    "session_id",
    "turn_index",
    "created_at",
    "query",
    "top_k",
    "recommendation_count",
    "feedback_count",
    "average_reward",
    "stop_reason",
    "agent_mode",
    "provider",
    "fallback_used",
    "tool_call_count",
    "goal_satisfied",
    "quantity_satisfied",
    "artist_diversity",
]


class TrajectoryDatasetService:
    def __init__(
        self,
        trajectory_dir: str | Path = "data/trajectories",
    ) -> None:
        self.trajectory_dir = Path(trajectory_dir)

    def export(
        self,
        output_path: str | Path,
        *,
        format: str = "jsonl",
        user_id: str | None = None,
        feedback_only: bool = False,
        anonymize: bool = True,
        anonymization_salt: str = "",
    ) -> ExportResult:
        if format not in {"jsonl", "csv"}:
            raise ValueError("format must be jsonl or csv")
        trajectories, skipped = self.load(
            user_id=user_id,
            feedback_only=feedback_only,
        )
        rows = [
            self.dataset_record(
                trajectory,
                anonymize=anonymize,
                anonymization_salt=anonymization_salt,
            )
            for trajectory in trajectories
        ]
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if format == "jsonl":
            self._write_jsonl(destination, rows)
        else:
            self._write_csv(destination, rows)
        return ExportResult(
            format=format,
            output_path=str(destination),
            trajectory_count=len(rows),
            skipped_files=skipped,
        )

    def evaluate(
        self,
        *,
        user_id: str | None = None,
        feedback_only: bool = False,
    ) -> EvaluationReport:
        trajectories, skipped = self.load(
            user_id=user_id,
            feedback_only=feedback_only,
        )
        records = [
            self.dataset_record(trajectory, anonymize=False)
            for trajectory in trajectories
        ]
        trajectory_count = len(records)
        feedback_events = [
            event
            for trajectory in trajectories
            for event in trajectory.feedback_events
        ]
        rewards = [
            float(event["reward_score"])
            for event in feedback_events
            if _is_number(event.get("reward_score"))
        ]
        feedback_types = Counter(
            str(event.get("feedback_type", "unknown"))
            for event in feedback_events
        )
        stop_reasons = Counter(
            trajectory.stop_reason for trajectory in trajectories
        )
        agent_modes = Counter(
            trajectory.agent_mode for trajectory in trajectories
        )
        tool_statuses = [
            str(call.get("observation", {}).get("status", "unknown"))
            for trajectory in trajectories
            for call in trajectory.tool_calls
        ]
        feedback_count = len(feedback_events)
        recommendation_count = sum(
            len(trajectory.recommendations) for trajectory in trajectories
        )
        unique_artist_count = sum(
            len(
                {
                    _normalized_artist(item.get("artist"))
                    for item in trajectory.recommendations
                    if _normalized_artist(item.get("artist"))
                }
            )
            for trajectory in trajectories
        )
        return EvaluationReport(
            trajectory_count=trajectory_count,
            user_count=len(
                {trajectory.user_id for trajectory in trajectories}
            ),
            session_count=len(
                {
                    trajectory.session_id
                    for trajectory in trajectories
                    if trajectory.session_id
                }
            ),
            goal_satisfied_rate=_ratio(
                stop_reasons["goal_satisfied"],
                trajectory_count,
            ),
            quantity_satisfied_rate=_ratio(
                sum(bool(record["quantity_satisfied"]) for record in records),
                trajectory_count,
            ),
            feedback_coverage_rate=_ratio(
                sum(
                    bool(trajectory.feedback_events)
                    for trajectory in trajectories
                ),
                trajectory_count,
            ),
            average_recommendations=_average(
                [
                    len(trajectory.recommendations)
                    for trajectory in trajectories
                ]
            ),
            average_tool_calls=_average(
                [len(trajectory.tool_calls) for trajectory in trajectories]
            ),
            tool_call_success_rate=_ratio(
                sum(status in {"ok", "partial"} for status in tool_statuses),
                len(tool_statuses),
            ),
            fallback_rate=_ratio(
                sum(bool(trajectory.fallback_reason) for trajectory in trajectories),
                trajectory_count,
            ),
            average_reward=_average(rewards),
            positive_feedback_rate=_ratio(
                sum(reward > 0 for reward in rewards),
                feedback_count,
            ),
            negative_feedback_rate=_ratio(
                sum(reward < 0 for reward in rewards),
                feedback_count,
            ),
            skip_rate=_ratio(
                feedback_types["skip"] + feedback_types["quick_skip"],
                feedback_count,
            ),
            favorite_rate=_ratio(
                feedback_types["favorite"]
                + feedback_types["playlist_add"],
                feedback_count,
            ),
            artist_diversity=_ratio(
                unique_artist_count,
                recommendation_count,
            ),
            stop_reason_counts=dict(sorted(stop_reasons.items())),
            agent_mode_counts=dict(sorted(agent_modes.items())),
            feedback_type_counts=dict(sorted(feedback_types.items())),
            skipped_files=skipped,
        )

    def analyze_ranking_feedback(
        self,
        *,
        user_id: str | None = None,
        feedback_only: bool = True,
    ) -> RankingTuningReport:
        trajectories, _skipped = self.load(
            user_id=user_id,
            feedback_only=feedback_only,
        )
        rewards: list[float] = []
        rank_rewards: dict[str, list[float]] = {}
        score_bucket_rewards: dict[str, list[float]] = {}
        source_counts: Counter[str] = Counter()
        contextual_feedback_count = 0

        for trajectory in trajectories:
            for context in trajectory.feedback_contexts:
                reward = context.get("reward_score")
                if not _is_number(reward):
                    continue
                numeric_reward = float(reward)
                rewards.append(numeric_reward)
                contextual_feedback_count += 1

                rank_key = str(context.get("recommendation_rank") or "unknown")
                rank_rewards.setdefault(rank_key, []).append(numeric_reward)

                score_bucket = _score_bucket(
                    context.get("recommended_final_score")
                )
                score_bucket_rewards.setdefault(score_bucket, []).append(
                    numeric_reward
                )

                source_key = str(context.get("feedback_source") or "unknown")
                source_counts[source_key] += 1

        sorted_rank_items = sorted(
            rank_rewards.items(),
            key=lambda item: _sort_rank_key(item[0]),
        )
        return RankingTuningReport(
            trajectory_count=len(trajectories),
            feedback_event_count=sum(
                len(trajectory.feedback_events) for trajectory in trajectories
            ),
            contextual_feedback_count=contextual_feedback_count,
            collection_write_count=sum(
                len(trajectory.collection_writes)
                for trajectory in trajectories
            ),
            average_reward=_average(rewards),
            current_weights=RankingWeights().to_dict(),
            reward_by_rank={
                key: _average(values) for key, values in sorted_rank_items
            },
            positive_rate_by_rank={
                key: _ratio(sum(value > 0 for value in values), len(values))
                for key, values in sorted_rank_items
            },
            negative_rate_by_rank={
                key: _ratio(sum(value < 0 for value in values), len(values))
                for key, values in sorted_rank_items
            },
            reward_by_score_bucket={
                key: _average(values)
                for key, values in sorted(score_bucket_rewards.items())
            },
            feedback_count_by_source=dict(sorted(source_counts.items())),
        )

    def split_by_user(
        self,
        output_dir: str | Path,
        *,
        train_ratio: float = 0.8,
        validation_ratio: float = 0.1,
        test_ratio: float = 0.1,
        seed: int = 20260615,
        feedback_only: bool = False,
        anonymize: bool = True,
        anonymization_salt: str = "",
    ) -> DatasetSplitResult:
        ratios = {
            "train": train_ratio,
            "validation": validation_ratio,
            "test": test_ratio,
        }
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
            for value in ratios.values()
        ):
            raise ValueError("split ratios must be non-negative numbers")
        if not math.isclose(sum(ratios.values()), 1.0, abs_tol=1e-9):
            raise ValueError("split ratios must sum to 1")
        trajectories, skipped = self.load(feedback_only=feedback_only)
        users = sorted(
            {trajectory.user_id for trajectory in trajectories},
            key=lambda user_id: hashlib.sha256(
                f"{seed}:{user_id}".encode("utf-8")
            ).hexdigest(),
        )
        user_counts = _allocate_split_counts(len(users), ratios)
        user_splits: dict[str, str] = {}
        cursor = 0
        for split_name in ("train", "validation", "test"):
            split_count = user_counts[split_name]
            for user_id in users[cursor : cursor + split_count]:
                user_splits[user_id] = split_name
            cursor += split_count

        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        output_paths = {
            split_name: destination / f"{split_name}.jsonl"
            for split_name in ("train", "validation", "test")
        }
        if any(path.exists() for path in output_paths.values()):
            raise FileExistsError(
                f"split output already exists under: {destination}"
            )
        split_rows: dict[str, list[dict[str, Any]]] = {
            name: [] for name in output_paths
        }
        for trajectory in trajectories:
            split_name = user_splits[trajectory.user_id]
            split_rows[split_name].append(
                self.dataset_record(
                    trajectory,
                    anonymize=anonymize,
                    anonymization_salt=anonymization_salt,
                )
            )
        for split_name, path in output_paths.items():
            self._write_jsonl(path, split_rows[split_name])
        manifest = {
            "seed": seed,
            "ratios": ratios,
            "trajectory_count": len(trajectories),
            "user_count": len(users),
            "split_trajectory_counts": {
                name: len(rows) for name, rows in split_rows.items()
            },
            "split_user_counts": user_counts,
            "feedback_only": feedback_only,
            "anonymized": anonymize,
            "skipped_files": skipped,
        }
        with (destination / "manifest.json").open("w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
        return DatasetSplitResult(
            output_dir=str(destination),
            trajectory_count=len(trajectories),
            user_count=len(users),
            split_trajectory_counts=manifest["split_trajectory_counts"],
            split_user_counts=user_counts,
            skipped_files=skipped,
            seed=seed,
        )

    def load(
        self,
        *,
        user_id: str | None = None,
        feedback_only: bool = False,
    ) -> tuple[list[AgentTrajectory], list[str]]:
        files = self._trajectory_files(user_id)
        trajectories: list[AgentTrajectory] = []
        skipped: list[str] = []
        for path in files:
            try:
                with path.open("r", encoding="utf-8") as file:
                    trajectory = AgentTrajectory.from_dict(json.load(file))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                skipped.append(str(path))
                continue
            if feedback_only and not trajectory.feedback_events:
                continue
            trajectories.append(trajectory)
        trajectories.sort(
            key=lambda item: (item.created_at, item.trajectory_id)
        )
        return trajectories, skipped

    def dataset_record(
        self,
        trajectory: AgentTrajectory,
        *,
        anonymize: bool = True,
        anonymization_salt: str = "",
    ) -> dict[str, Any]:
        user_key = (
            _anonymous_user_key(trajectory.user_id, anonymization_salt)
            if anonymize
            else trajectory.user_id
        )
        rewards = [
            float(event["reward_score"])
            for event in trajectory.feedback_events
            if _is_number(event.get("reward_score"))
        ]
        top_k = _top_k(trajectory.parsed_request)
        recommendation_count = len(trajectory.recommendations)
        artists = {
            _normalized_artist(item.get("artist"))
            for item in trajectory.recommendations
            if _normalized_artist(item.get("artist"))
        }
        summary = {
            "trajectory_id": trajectory.trajectory_id,
            "user_key": user_key,
            "session_id": trajectory.session_id,
            "turn_index": trajectory.turn_index,
            "created_at": trajectory.created_at,
            "query": trajectory.query,
            "top_k": top_k,
            "recommendation_count": recommendation_count,
            "feedback_count": len(trajectory.feedback_events),
            "average_reward": _average(rewards),
            "stop_reason": trajectory.stop_reason,
            "agent_mode": trajectory.agent_mode,
            "provider": trajectory.provider,
            "fallback_used": bool(trajectory.fallback_reason),
            "tool_call_count": len(trajectory.tool_calls),
            "goal_satisfied": trajectory.stop_reason == "goal_satisfied",
            "quantity_satisfied": (
                recommendation_count >= top_k if top_k > 0 else False
            ),
            "artist_diversity": _ratio(
                len(artists),
                recommendation_count,
            ),
        }
        parsed_request = dict(trajectory.parsed_request)
        plan = [dict(item) for item in trajectory.plan]
        tool_calls = [dict(item) for item in trajectory.tool_calls]
        recommendations = [
            dict(item) for item in trajectory.recommendations
        ]
        user_memory_snapshot = dict(trajectory.user_memory_snapshot)
        session_memory_snapshot = dict(trajectory.session_memory_snapshot)
        artist_expansion_snapshot = dict(trajectory.artist_expansion_snapshot)
        retrieval_snapshot = dict(trajectory.retrieval_snapshot)
        feedback_events = [
            dict(item) for item in trajectory.feedback_events
        ]
        feedback_contexts = [
            dict(item) for item in trajectory.feedback_contexts
        ]
        collection_writes = [
            dict(item) for item in trajectory.collection_writes
        ]
        agent_decisions = [
            dict(item) for item in trajectory.agent_decisions
        ]
        if anonymize:
            parsed_request = _redact_user_ids(parsed_request, user_key)
            plan = _redact_user_ids(plan, user_key)
            tool_calls = _redact_user_ids(tool_calls, user_key)
            recommendations = _redact_user_ids(recommendations, user_key)
            user_memory_snapshot = _redact_user_ids(
                user_memory_snapshot,
                user_key,
            )
            session_memory_snapshot = _redact_user_ids(
                session_memory_snapshot,
                user_key,
            )
            artist_expansion_snapshot = _redact_user_ids(
                artist_expansion_snapshot,
                user_key,
            )
            retrieval_snapshot = _redact_user_ids(
                retrieval_snapshot,
                user_key,
            )
            feedback_events = _redact_user_ids(feedback_events, user_key)
            feedback_contexts = _redact_user_ids(feedback_contexts, user_key)
            collection_writes = _redact_user_ids(collection_writes, user_key)
            agent_decisions = _redact_user_ids(agent_decisions, user_key)
        return {
            **summary,
            "trajectory_schema_version": trajectory.trajectory_schema_version,
            "loop_contract_version": trajectory.loop_contract_version,
            "tool_schema_version": trajectory.tool_schema_version,
            "parsed_request": parsed_request,
            "plan": plan,
            "tool_calls": tool_calls,
            "recommendations": recommendations,
            "user_memory_snapshot": user_memory_snapshot,
            "session_memory_snapshot": session_memory_snapshot,
            "artist_expansion_snapshot": artist_expansion_snapshot,
            "retrieval_snapshot": retrieval_snapshot,
            "response_text": trajectory.response_text,
            "feedback_events": feedback_events,
            "feedback_contexts": feedback_contexts,
            "collection_writes": collection_writes,
            "fallback_reason": trajectory.fallback_reason,
            "agent_decisions": agent_decisions,
        }

    def _trajectory_files(self, user_id: str | None) -> list[Path]:
        if user_id is not None:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", user_id):
                raise ValueError(
                    "user_id may contain only letters, numbers, '.', '_' and '-'"
                )
        if not self.trajectory_dir.exists():
            return []
        if user_id is not None:
            return sorted((self.trajectory_dir / user_id).glob("*.json"))
        return sorted(self.trajectory_dir.glob("*/*.json"))

    @staticmethod
    def _write_jsonl(
        destination: Path,
        rows: Iterable[dict[str, Any]],
    ) -> None:
        with destination.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                )

    @staticmethod
    def _write_csv(
        destination: Path,
        rows: Iterable[dict[str, Any]],
    ) -> None:
        with destination.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field) for field in CSV_FIELDS})


def _anonymous_user_key(user_id: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).hexdigest()
    return f"user_{digest[:16]}"


def _redact_user_ids(value: Any, user_key: str) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                user_key
                if key == "user_id"
                else _redact_user_ids(item, user_key)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_user_ids(item, user_key) for item in value]
    return value


def _normalized_artist(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.casefold().split())


def _top_k(parsed_request: dict[str, Any]) -> int:
    value = parsed_request.get("top_k", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


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


def _score_bucket(value: Any) -> str:
    if not _is_number(value):
        return "unknown"
    score = max(0.0, min(float(value), 1.0))
    lower = math.floor(score * 5) / 5
    upper = min(1.0, lower + 0.2)
    return f"{lower:.1f}-{upper:.1f}"


def _sort_rank_key(value: str) -> tuple[int, str]:
    if value.isdigit():
        return (0, f"{int(value):06d}")
    return (1, value)


def _allocate_split_counts(
    total: int,
    ratios: dict[str, float],
) -> dict[str, int]:
    raw = {name: total * ratio for name, ratio in ratios.items()}
    counts = {name: int(value) for name, value in raw.items()}
    remaining = total - sum(counts.values())
    order = sorted(
        ratios,
        key=lambda name: (raw[name] - counts[name], ratios[name], name),
        reverse=True,
    )
    for name in order[:remaining]:
        counts[name] += 1
    positive = [name for name, ratio in ratios.items() if ratio > 0]
    if total >= len(positive):
        for empty_name in [name for name in positive if counts[name] == 0]:
            donor = max(
                (
                    name
                    for name in positive
                    if counts[name] > 1
                ),
                key=lambda name: counts[name],
            )
            counts[donor] -= 1
            counts[empty_name] += 1
    return counts

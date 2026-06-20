"""Turn collected agent trajectories into model-training samples.

This module is the bridge between the L7 trajectory dataset and model training.
It is deliberately dependency-free (standard library only) so it can run and be
tested anywhere — the heavy training loops (transformers/trl/peft) live in
``sft.py`` / ``grpo.py`` and import their dependencies lazily.

Two sample shapes are produced:

* **SFT** (supervised fine-tuning): ``{"prompt", "completion", ...}`` where the
  prompt is the user query plus light context and the completion is the agent's
  ReAct trace (Thought -> Action -> ... -> final response). This teaches a model
  to imitate the orchestration behavior the live agent already produces.

* **GRPO** (group-relative preference optimization): ``{"prompt", "responses",
  "rewards"}`` where, for a given prompt, several candidate responses are scored
  by the reward signal derived from real user feedback (play/like/skip/save…).
  GRPO uses the *relative* reward within each group to push the policy toward
  higher-reward behavior.

Reward signal: each trajectory already carries ``feedback_events`` with a
``reward_score`` in [-1, 1] (see L5 ``REWARD_BY_FEEDBACK_TYPE``) plus a
precomputed ``average_reward``. We reuse those directly so the training reward
is exactly the production reward — no re-derivation drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SampleBuildResult:
    """Outcome of building a training file from a trajectory dataset."""

    kind: str  # "sft" | "grpo"
    output_path: str
    source_record_count: int
    sample_count: int
    skipped_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "output_path": self.output_path,
            "source_record_count": self.source_record_count,
            "sample_count": self.sample_count,
            "skipped_count": self.skipped_count,
        }


# --------------------------------------------------------------------------- #
# SFT
# --------------------------------------------------------------------------- #
def build_sft_samples(
    records: Iterable[dict[str, Any]],
    *,
    min_reward: float | None = None,
) -> list[dict[str, Any]]:
    """Build supervised prompt/completion samples from dataset records.

    ``records`` are the JSONL rows produced by ``TrajectoryDatasetService``
    (``dataset_record``). When ``min_reward`` is set, trajectories whose
    ``average_reward`` is below it are dropped — a simple way to fine-tune only
    on the behavior that earned positive user feedback (reward-filtered SFT).
    """
    samples: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if min_reward is not None:
            reward = record.get("average_reward")
            if not _is_number(reward) or float(reward) < min_reward:
                continue
        prompt = _sft_prompt(record)
        completion = _sft_completion(record)
        if not prompt or not completion:
            continue
        samples.append(
            {
                "prompt": prompt,
                "completion": completion,
                "trajectory_id": record.get("trajectory_id"),
                "average_reward": (
                    float(record["average_reward"])
                    if _is_number(record.get("average_reward"))
                    else 0.0
                ),
            }
        )
    return samples


def _sft_prompt(record: dict[str, Any]) -> str:
    query = str(record.get("query") or "").strip()
    if not query:
        return ""
    lines = [
        "You are rateyourDJ, a tool-using music recommendation agent.",
        "Reason step by step (ReAct), call tools, then answer.",
        "",
        f"User request: {query}",
    ]
    memory = record.get("user_memory_snapshot")
    taste = _taste_summary(memory)
    if taste:
        lines.append(f"Known listener taste: {taste}")
    parsed = record.get("parsed_request")
    if isinstance(parsed, dict):
        top_k = parsed.get("top_k")
        if _is_number(top_k):
            lines.append(f"Requested count: {int(top_k)}")
    return "\n".join(lines).strip()


def _sft_completion(record: dict[str, Any]) -> str:
    """Render the agent's ReAct trace as the target completion."""
    steps: list[str] = []
    for decision in record.get("agent_decisions", []) or []:
        if not isinstance(decision, dict):
            continue
        thought = str(decision.get("thought") or "").strip()
        if thought:
            steps.append(f"Thought: {thought}")
        tool_name = decision.get("tool_name")
        if tool_name:
            steps.append(f"Action: {tool_name}")

    # Fall back to tool_calls when decisions carry no thoughts (e.g. program
    # -first discovery), so the completion still reflects the action sequence.
    if not steps:
        for call in record.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                continue
            thought = str(call.get("thought") or "").strip()
            if thought:
                steps.append(f"Thought: {thought}")
            tool = call.get("tool")
            if tool:
                steps.append(f"Action: {tool}")

    answer = str(record.get("response_text") or "").strip()
    tracks = _recommended_track_lines(record.get("recommendations"))
    if tracks:
        steps.append("Observation: grounded candidate tracks confirmed.")
    if answer:
        steps.append(f"Final answer: {answer}")
    if tracks:
        steps.append("Recommended tracks:\n" + "\n".join(tracks))
    return "\n".join(steps).strip()


# --------------------------------------------------------------------------- #
# GRPO
# --------------------------------------------------------------------------- #
def build_grpo_samples(
    records: Iterable[dict[str, Any]],
    *,
    min_group_size: int = 2,
) -> list[dict[str, Any]]:
    """Group trajectories by prompt and attach per-response rewards.

    GRPO optimizes a policy using the *relative* reward of several responses to
    the same prompt. We group dataset records by their normalized query, use the
    rendered ReAct trace as each response, and the trajectory's reward as the
    scalar reward. Only groups with at least ``min_group_size`` distinct
    responses (and some reward variation) are emitted — a single response gives
    GRPO nothing to compare against.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        prompt = _sft_prompt(record)
        response = _sft_completion(record)
        if not prompt or not response:
            continue
        reward = record.get("average_reward")
        reward_value = float(reward) if _is_number(reward) else 0.0
        bucket = grouped.setdefault(
            prompt, {"prompt": prompt, "responses": [], "rewards": []}
        )
        bucket["responses"].append(response)
        bucket["rewards"].append(round(reward_value, 6))

    samples: list[dict[str, Any]] = []
    for bucket in grouped.values():
        rewards = bucket["rewards"]
        if len(bucket["responses"]) < min_group_size:
            continue
        if len(set(rewards)) < 2:
            # No reward contrast in this group -> no learning signal for GRPO.
            continue
        samples.append(bucket)
    return samples


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #
def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    source = Path(path)
    with source.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_sft_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    min_reward: float | None = None,
) -> SampleBuildResult:
    records = load_jsonl(input_path)
    samples = build_sft_samples(records, min_reward=min_reward)
    write_jsonl(output_path, samples)
    return SampleBuildResult(
        kind="sft",
        output_path=str(output_path),
        source_record_count=len(records),
        sample_count=len(samples),
        skipped_count=len(records) - len(samples),
    )


def build_grpo_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    min_group_size: int = 2,
) -> SampleBuildResult:
    records = load_jsonl(input_path)
    samples = build_grpo_samples(records, min_group_size=min_group_size)
    write_jsonl(output_path, samples)
    return SampleBuildResult(
        kind="grpo",
        output_path=str(output_path),
        source_record_count=len(records),
        sample_count=len(samples),
        skipped_count=0,
    )


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def _recommended_track_lines(recommendations: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(recommendations, list):
        return lines
    for item in recommendations:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        artist = str(item.get("artist") or "").strip()
        if title and artist:
            lines.append(f"- {title} — {artist}")
    return lines


def _taste_summary(memory: Any) -> str:
    if not isinstance(memory, dict):
        return ""
    parts: list[str] = []
    for field_name, label in (
        ("artist_preferences", "artists"),
        ("genre_preferences", "genres"),
    ):
        prefs = memory.get(field_name)
        if isinstance(prefs, dict) and prefs:
            top = sorted(
                prefs.items(),
                key=lambda kv: (-_as_float(kv[1]), str(kv[0])),
            )[:3]
            names = ", ".join(str(name) for name, _ in top)
            if names:
                parts.append(f"{label}: {names}")
    return "; ".join(parts)


def _as_float(value: Any) -> float:
    return float(value) if _is_number(value) else 0.0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

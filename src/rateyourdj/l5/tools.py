from __future__ import annotations

from pathlib import Path
from typing import Any

from rateyourdj.agent_tools import ToolObservation
from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore

from .models import FeedbackRecord, FeedbackSummary
from .service import FeedbackService, FeedbackTrajectorySink


def _service(
    profile_dir: str | Path,
    song_dir: str | Path,
    trajectory_sink: FeedbackTrajectorySink | None = None,
) -> FeedbackService:
    return FeedbackService(
        JsonProfileStore(profile_dir),
        JsonSongStore(song_dir),
        trajectory_sink,
    )


def collect_feedback(
    user_id: str,
    song_id: str,
    feedback_type: str,
    *,
    timestamp: str | None = None,
    reward_score: float | None = None,
    recommendation_context: dict[str, Any] | None = None,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    trajectory_sink: FeedbackTrajectorySink | None = None,
) -> FeedbackRecord:
    return _service(profile_dir, song_dir, trajectory_sink).record(
        user_id,
        song_id,
        feedback_type,
        timestamp=timestamp,
        reward_score=reward_score,
        recommendation_context=recommendation_context,
    )


def record_feedback_tool(
    user_id: str,
    song_id: str,
    feedback_type: str,
    *,
    timestamp: str | None = None,
    reward_score: float | None = None,
    recommendation_context: dict[str, Any] | None = None,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    trajectory_sink: FeedbackTrajectorySink | None = None,
) -> ToolObservation:
    record = collect_feedback(
        user_id,
        song_id,
        feedback_type,
        timestamp=timestamp,
        reward_score=reward_score,
        recommendation_context=recommendation_context,
        profile_dir=profile_dir,
        song_dir=song_dir,
        trajectory_sink=trajectory_sink,
    )
    trajectory_id = record.recommendation_context.get("trajectory_id")
    linked = bool(trajectory_id and trajectory_sink is not None)
    diagnostics = (
        []
        if linked or not trajectory_id
        else ["trajectory_id was stored in L1 but no trajectory sink was configured"]
    )
    return ToolObservation(
        tool="L5.record_feedback",
        status="partial" if diagnostics else "ok",
        data={
            "feedback": record.to_dict(),
            "trajectory_linked": linked,
        },
        diagnostics=diagnostics,
        retryable=False,
        suggested_actions=(
            [
                {
                    "tool": "L5.configure_trajectory_sink",
                    "reason": "append reward to the referenced trajectory",
                }
            ]
            if diagnostics
            else []
        ),
    )


def get_feedback_summary(
    user_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
) -> FeedbackSummary:
    return _service(profile_dir, song_dir).summary(user_id)


def get_feedback_score(
    user_id: str,
    song_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
) -> float:
    return _service(profile_dir, song_dir).score_song(user_id, song_id)


def inspect_feedback_state(
    user_id: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
) -> ToolObservation:
    summary = get_feedback_summary(
        user_id,
        profile_dir=profile_dir,
        song_dir=song_dir,
    )
    diagnostics = (
        [f"{len(summary.missing_song_ids)} feedback songs lack L2 profiles"]
        if summary.missing_song_ids
        else []
    )
    return ToolObservation(
        tool="L5.inspect_feedback_state",
        status="partial" if diagnostics else "ok",
        data=summary.to_dict(),
        diagnostics=diagnostics,
        retryable=bool(diagnostics),
        suggested_actions=(
            [
                {
                    "tool": "L2.collect_or_import",
                    "song_ids": list(summary.missing_song_ids),
                    "reason": "restore feedback song profiles",
                }
            ]
            if diagnostics
            else []
        ),
    )

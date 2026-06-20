from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Cap stored conversation turns so a long session file stays bounded.
MAX_SESSION_MESSAGES = 100


@dataclass(slots=True)
class AgentSession:
    schema_version: int
    session_id: str
    user_id: str
    turn_count: int = 0
    current_intent: str = "recommend"
    last_user_query: str | None = None
    preference_terms: list[str] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    seen_track_ids: list[str] = field(default_factory=list)
    seen_track_signatures: list[str] = field(default_factory=list)
    seed_track_ids: list[str] = field(default_factory=list)
    active_constraints: dict[str, Any] = field(default_factory=dict)
    last_run_id: str | None = None
    last_recommendation_ids: list[str] = field(default_factory=list)
    last_recommended_tracks: list[dict[str, Any]] = field(default_factory=list)
    temporary_feedback: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def append_message(
        self,
        role: str,
        text: str,
        *,
        max_messages: int = MAX_SESSION_MESSAGES,
    ) -> None:
        """Append one conversation turn (role: 'user' or 'dj') with a timestamp.

        Keeps only the most recent ``max_messages`` entries so the session file
        does not grow without bound over a long conversation.
        """
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        self.messages.append(
            {"role": str(role), "text": cleaned, "ts": _now()}
        )
        if len(self.messages) > max_messages:
            del self.messages[: len(self.messages) - max_messages]

    @property
    def seen_song_ids(self) -> list[str]:
        return self.seen_track_ids

    @seen_song_ids.setter
    def seen_song_ids(self, value: list[str]) -> None:
        self.seen_track_ids = value

    @property
    def last_top_k(self) -> int | None:
        return _optional_int(self.active_constraints.get("limit"))

    @last_top_k.setter
    def last_top_k(self, value: int | None) -> None:
        _set_constraint(self.active_constraints, "limit", value)

    @property
    def last_max_per_artist(self) -> int | None:
        return _optional_int(self.active_constraints.get("max_per_artist"))

    @last_max_per_artist.setter
    def last_max_per_artist(self, value: int | None) -> None:
        _set_constraint(self.active_constraints, "max_per_artist", value)

    @property
    def last_min_retrieval_score(self) -> float | None:
        return _optional_float(
            self.active_constraints.get("min_retrieval_score")
        )

    @last_min_retrieval_score.setter
    def last_min_retrieval_score(self, value: float | None) -> None:
        _set_constraint(self.active_constraints, "min_retrieval_score", value)

    @property
    def last_trajectory_id(self) -> str | None:
        return self.last_run_id

    @last_trajectory_id.setter
    def last_trajectory_id(self, value: str | None) -> None:
        self.last_run_id = value


class JsonSessionStore:
    def __init__(self, root: str | Path = "data/sessions") -> None:
        self.root = Path(root)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def load_or_create(
        self,
        user_id: str,
        session_id: str | None = None,
    ) -> AgentSession:
        resolved_id = session_id or str(uuid4())
        with self._lock_for(resolved_id):
            path = self._path_for(resolved_id)
            if not path.exists():
                session = AgentSession(
                    schema_version=1,
                    session_id=resolved_id,
                    user_id=user_id,
                )
                self._save_unlocked(session)
                return session
            with path.open("r", encoding="utf-8") as file:
                value = json.load(file)
            session = AgentSession(**_migrate_session_payload(value))
            if session.user_id != user_id:
                raise ValueError("session does not belong to this user")
            return session

    def save(self, session: AgentSession) -> Path:
        session.updated_at = _now()
        with self._lock_for(session.session_id):
            return self._save_unlocked(session)

    def _path_for(self, session_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", session_id):
            raise ValueError(
                "session_id may contain only letters, numbers, '.', '_' and '-'"
            )
        return self.root / f"{session_id}.json"

    def _save_unlocked(self, session: AgentSession) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path_for(session.session_id)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root,
            prefix=f".{session.session_id}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(
                    asdict(session),
                    file,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_name, destination)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return destination

    def _lock_for(self, session_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.RLock())


def _is_seen_song_reference(term: str) -> bool:
    normalized = " ".join(term.casefold().split())
    return any(
        marker in normalized
        for marker in (
            "刚才推荐过",
            "刚刚推荐过",
            "之前推荐过",
            "上次推荐过",
            "刚才那些",
            "刚刚那些",
            "之前那些",
            "already recommended",
            "shown before",
        )
    )


def _migrate_session_payload(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("session payload must be an object")
    exclude_terms = [
        str(term).strip()
        for term in value.get("exclude_terms", [])
        if isinstance(term, str)
        and str(term).strip()
        and not _is_seen_song_reference(str(term))
    ]
    active_constraints = value.get("active_constraints")
    if not isinstance(active_constraints, dict):
        active_constraints = {}
    active_constraints = dict(active_constraints)
    _merge_legacy_constraint(active_constraints, "limit", value.get("last_top_k"))
    _merge_legacy_constraint(
        active_constraints,
        "max_per_artist",
        value.get("last_max_per_artist"),
    )
    _merge_legacy_constraint(
        active_constraints,
        "min_retrieval_score",
        value.get("last_min_retrieval_score"),
    )
    if value.get("seen_song_ids") and not value.get("seen_track_ids"):
        seen_track_ids = value.get("seen_song_ids", [])
    else:
        seen_track_ids = value.get("seen_track_ids", [])
    return {
        "schema_version": int(value.get("schema_version", 1)),
        "session_id": str(value["session_id"]),
        "user_id": str(value["user_id"]),
        "turn_count": int(value.get("turn_count", 0)),
        "current_intent": str(value.get("current_intent", "recommend")),
        "last_user_query": _optional_string(value.get("last_user_query")),
        "preference_terms": _string_list(value.get("preference_terms", [])),
        "exclude_terms": exclude_terms,
        "seen_track_ids": _string_list(seen_track_ids),
        "seen_track_signatures": _string_list(
            value.get("seen_track_signatures", [])
        ),
        "seed_track_ids": _string_list(value.get("seed_track_ids", [])),
        "active_constraints": active_constraints,
        "last_run_id": _optional_string(
            value.get("last_run_id", value.get("last_trajectory_id"))
        ),
        "last_recommendation_ids": _string_list(
            value.get("last_recommendation_ids", [])
        ),
        "last_recommended_tracks": _track_list(
            value.get("last_recommended_tracks", [])
        ),
        "temporary_feedback": _feedback_list(
            value.get("temporary_feedback", [])
        ),
        "messages": _message_list(value.get("messages", [])),
        "created_at": _optional_string(value.get("created_at")) or _now(),
        "updated_at": _optional_string(value.get("updated_at")) or _now(),
    }


def _merge_legacy_constraint(
    constraints: dict[str, Any],
    field_name: str,
    legacy_value: Any,
) -> None:
    if field_name not in constraints and legacy_value is not None:
        constraints[field_name] = legacy_value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
    return result


def _feedback_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _track_list(value: Any) -> list[dict[str, Any]]:
    """Normalize stored last-recommended tracks (title/artist/reason)."""
    if not isinstance(value, list):
        return []
    tracks: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        artist = str(item.get("artist") or "").strip()
        if not title and not artist:
            continue
        tracks.append(
            {
                "title": title,
                "artist": artist,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return tracks


def _message_list(value: Any) -> list[dict[str, Any]]:
    """Normalize stored conversation turns; tolerate legacy/missing data."""
    if not isinstance(value, list):
        return []
    messages: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        text = str(item.get("text") or "").strip()
        if not role or not text:
            continue
        entry: dict[str, Any] = {"role": role, "text": text}
        ts = _optional_string(item.get("ts"))
        if ts:
            entry["ts"] = ts
        messages.append(entry)
    return messages


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _set_constraint(
    constraints: dict[str, Any],
    field_name: str,
    value: Any,
) -> None:
    if value is None:
        constraints.pop(field_name, None)
    else:
        constraints[field_name] = value

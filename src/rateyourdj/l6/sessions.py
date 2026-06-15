from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class AgentSession:
    session_id: str
    user_id: str
    turn_count: int = 0
    preference_terms: list[str] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    seen_song_ids: list[str] = field(default_factory=list)
    last_top_k: int | None = None
    last_max_per_artist: int | None = None
    last_min_retrieval_score: float | None = None
    last_trajectory_id: str | None = None
    updated_at: str = field(default_factory=_now)


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
                    session_id=resolved_id,
                    user_id=user_id,
                )
                self._save_unlocked(session)
                return session
            with path.open("r", encoding="utf-8") as file:
                value = json.load(file)
            value["exclude_terms"] = [
                term
                for term in value.get("exclude_terms", [])
                if not _is_seen_song_reference(str(term))
            ]
            session = AgentSession(**value)
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

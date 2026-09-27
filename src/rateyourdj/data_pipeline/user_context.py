"""UserContextV2 storage (data/users/<user_id>/context.json) with v1 fallback."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rateyourdj.contracts import user_context_from_v1, validate_record
from rateyourdj.contracts.v2 import SCHEMA_VERSIONS, is_valid_internal_id

DEFAULT_ROOT = "data/users"
LEGACY_ROOT = "data/user_profiles"


class UserContextNotFound(KeyError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def context_path(user_id: str, root: str | Path = DEFAULT_ROOT) -> Path:
    if not is_valid_internal_id(user_id):
        raise ValueError("user_id may contain only letters, numbers, '.', '_' and '-'")
    return Path(root) / user_id / "context.json"


def save_user_context(context: dict[str, Any], root: str | Path = DEFAULT_ROOT) -> Path:
    context = dict(context)
    context["updated_at"] = _now()
    validate_record("user_context", context)
    path = context_path(context["user_id"], root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(path)
    return path


def load_user_context(user_id: str, root: str | Path = DEFAULT_ROOT,
                      legacy_root: str | Path | None = LEGACY_ROOT) -> dict[str, Any]:
    """Load a V2 context; fall back to a v1 L1 profile read through the adapter."""
    path = context_path(user_id, root)
    if path.is_file():
        context = json.loads(path.read_text("utf-8"))
        validate_record("user_context", context)
        return context
    if legacy_root is not None:
        legacy = Path(legacy_root) / f"{user_id}.json"
        if legacy.is_file():
            context, _candidates = user_context_from_v1(json.loads(legacy.read_text("utf-8")))
            return context
    raise UserContextNotFound(user_id)


def new_user_context(user_id: str, *, exploration_level: float = 0.5) -> dict[str, Any]:
    now = _now()
    return {
        "schema_version": SCHEMA_VERSIONS["user_context"],
        "user_id": user_id,
        "seed_branches": [],
        "exploration_level": exploration_level,
        "exclusions": {"song_ids": [], "artists_mbid": [], "tags": []},
        "heard_song_ids": [],
        "recommended_song_ids": [],
        "recent_feedback_ids": [],
        "created_at": now,
        "updated_at": now,
    }

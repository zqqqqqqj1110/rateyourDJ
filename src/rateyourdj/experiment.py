"""Run manifests for reproducible experiments.

Every baseline, eval, training or ablation run writes a ``manifest.json`` next
to its outputs so a result can always be traced back to the exact code, data,
model and config that produced it (TODO stage 0).
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "run-manifest/v1"


def _git(*args: str, cwd: str | Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def git_state(cwd: str | Path | None = None) -> dict[str, Any]:
    """Current commit and whether the working tree has uncommitted changes."""
    commit = _git("rev-parse", "HEAD", cwd=cwd)
    status = _git("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    return {
        "commit": commit,
        "dirty": bool(status) if status is not None else None,
    }


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_run_manifest(
    run_id: str,
    *,
    kind: str,
    config: dict[str, Any] | None = None,
    data_files: list[str | Path] | None = None,
    model: dict[str, Any] | None = None,
    seed: int | None = None,
    metrics: dict[str, Any] | None = None,
    notes: str = "",
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Assemble a manifest dict. ``data_files`` are fingerprinted by sha256."""
    data_versions = {}
    for item in data_files or []:
        path = Path(item)
        data_versions[str(item)] = file_sha256(path) if path.is_file() else None
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": git_state(cwd),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "data": data_versions,
        "model": dict(model or {}),
        "seed": seed,
        "config": dict(config or {}),
        "metrics": dict(metrics or {}),
        "notes": notes,
    }


def write_run_manifest(run_dir: str | Path, manifest: dict[str, Any]) -> Path:
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    target = run_path / "manifest.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target

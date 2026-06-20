"""Minimal .env loader (no third-party dependency).

Reads KEY=VALUE lines from a `.env` file and sets them as environment variables
*without overriding* values that are already set in the real environment. This
lets the user keep API keys in a local, git-ignored `.env` file instead of
exporting them every shell session.

Supported syntax:
    KEY=value
    KEY="quoted value"      # surrounding single/double quotes are stripped
    # comment lines and blank lines are ignored
    export KEY=value         # a leading "export " is allowed and ignored
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load environment variables from a .env file.

    Args:
        path: path to the .env file. Defaults to ``.env`` in the current
            working directory.
        override: when True, values in the file replace existing environment
            variables. Defaults to False (real environment wins).

    Returns:
        The mapping of keys that were loaded from the file (regardless of
        whether they overrode the environment), for logging/inspection.
    """
    env_path = Path(path) if path is not None else Path.cwd() / ".env"
    loaded: dict[str, str] = {}
    if not env_path.is_file():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded

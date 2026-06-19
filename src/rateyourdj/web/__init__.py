"""Local web interface for rateyourDJ recommendations and feedback."""

from pathlib import Path
from typing import Any

from rateyourdj.l6 import LLMProvider
from rateyourdj.providers import ExternalMusicProvider


def create_app(
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    trajectory_dir: str | Path = "data/trajectories",
    session_dir: str | Path = "data/sessions",
    llm_provider: LLMProvider | None = None,
    music_provider: ExternalMusicProvider | None = None,
    auto_configure_music_provider: bool = True,
    agent_mode: str = "auto",
) -> Any:
    from .app import create_app as app_factory

    return app_factory(
        profile_dir=profile_dir,
        song_dir=song_dir,
        trajectory_dir=trajectory_dir,
        session_dir=session_dir,
        llm_provider=llm_provider,
        music_provider=music_provider,
        auto_configure_music_provider=auto_configure_music_provider,
        agent_mode=agent_mode,
    )


__all__ = ["create_app"]

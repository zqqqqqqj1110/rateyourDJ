from __future__ import annotations

from pathlib import Path
from typing import Any

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore
from rateyourdj.l4 import RecommendationRankingService
from rateyourdj.providers import ExternalMusicProvider

from .agent_tool_registry import AgentToolRegistryV1
from .models import AgentResponse
from .provider import LLMProvider
from .service import RecommendationAgentService
from .sessions import JsonSessionStore
from .store import JsonTrajectoryStore


def request_recommendations(
    user_id: str,
    query: str,
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    trajectory_dir: str | Path = "data/trajectories",
    session_dir: str | Path = "data/sessions",
    default_top_k: int = 10,
    session_id: str | None = None,
    max_steps: int = 5,
    agent_mode: str = "auto",
    llm_provider: LLMProvider | None = None,
    music_provider: ExternalMusicProvider | None = None,
    discovery_service: Any | None = None,
    auto_configure_discovery: bool = True,
) -> AgentResponse:
    # Mirror the web app: without a discovery service, tracks the model
    # nominates in the unified answer-first turn can never be grounded, so the
    # CLI path silently returned 0 songs.
    if (
        discovery_service is None
        and auto_configure_discovery
        and music_provider is not None
    ):
        from rateyourdj.domain import (
            DeepSeekTrackGenerator,
            DiscoveryService,
            TasteSeedTrackGenerator,
        )

        generator = DeepSeekTrackGenerator.from_env() or TasteSeedTrackGenerator()
        discovery_service = DiscoveryService(generator, music_provider)
    profile_store = JsonProfileStore(profile_dir)
    song_store = JsonSongStore(song_dir)
    return RecommendationAgentService(
        RecommendationRankingService(profile_store, song_store),
        song_store,
        JsonTrajectoryStore(trajectory_dir),
        JsonSessionStore(session_dir),
        model_tool_registry=(
            AgentToolRegistryV1.default(
                profile_store,
                song_store,
                music_provider=music_provider,
            )
            if music_provider is not None
            else None
        ),
        llm_provider=llm_provider,
        agent_mode=agent_mode,
        discovery_service=discovery_service,
    ).recommend(
        user_id,
        query,
        default_top_k=default_top_k,
        session_id=session_id,
        max_steps=max_steps,
        agent_mode=agent_mode,
    )

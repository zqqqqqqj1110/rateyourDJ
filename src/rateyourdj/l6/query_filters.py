from __future__ import annotations

from rateyourdj.l2 import JsonSongStore

from .models import AgentRequest
from .session_ranking import (
    SessionRankingContext,
    apply_session_ranking_filters,
    effective_preference_terms,
    ranked_song_matches,
    song_matches,
)


def apply_query_filters(
    ranked_songs: list[dict[str, object]],
    request: AgentRequest,
    *,
    song_store: JsonSongStore,
    context: SessionRankingContext,
) -> list[dict[str, object]]:
    return apply_session_ranking_filters(
        ranked_songs,
        request,
        song_store=song_store,
        context=context,
    )


__all__ = [
    "SessionRankingContext",
    "apply_query_filters",
    "effective_preference_terms",
    "ranked_song_matches",
    "song_matches",
]

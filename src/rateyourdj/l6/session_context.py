from __future__ import annotations

from .guards import canonical_preference_terms, query_has_explicit_count, unique
from .models import AgentRequest
from .sessions import AgentSession


def request_with_session_context(
    request: AgentRequest,
    session: AgentSession,
) -> AgentRequest:
    if request.intent != "more":
        return request
    top_k = (
        request.top_k
        if query_has_explicit_count(request.query)
        else session.last_top_k or request.top_k
    )
    max_per_artist = (
        request.max_per_artist
        if query_requests_artist_diversity(request.query)
        else session.last_max_per_artist or request.max_per_artist
    )
    return AgentRequest(
        query=request.query,
        top_k=top_k,
        max_per_artist=max_per_artist,
        min_retrieval_score=(
            request.min_retrieval_score
            if request.min_retrieval_score > 0
            else session.last_min_retrieval_score
            or request.min_retrieval_score
        ),
        preference_terms=(
            canonical_preference_terms(request.preference_terms)
            or canonical_preference_terms(session.preference_terms)
        ),
        exclude_terms=unique([*session.exclude_terms, *request.exclude_terms]),
        intent=request.intent,
        exclude_seen=True,
    )


def query_requests_artist_diversity(query: str) -> bool:
    lowered = query.casefold()
    return any(
        marker in lowered
        for marker in (
            "多样",
            "不同歌手",
            "不要重复歌手",
            "每位歌手",
            "每个歌手",
            "diverse",
            "different artists",
            "per artist",
        )
    )

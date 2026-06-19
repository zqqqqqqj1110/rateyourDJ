from __future__ import annotations

from typing import Any


def _case(
    case_id: str,
    category: str,
    query: str,
    *,
    profile_key: str = "rock",
    user_id: str = "user-1",
    session_setup: dict[str, Any] | None = None,
    prelude_queries: list[str] | None = None,
    provider_tracks: list[dict[str, Any]] | None = None,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "category": category,
        "user_id": user_id,
        "profile_key": profile_key,
        "query": query,
        "session_setup": session_setup or {},
        "prelude_queries": prelude_queries or [],
        "provider_tracks": provider_tracks or [],
        "expected": expected or {},
    }


def _provider_track(
    track_id: str,
    title: str,
    artist: str,
    *,
    album: str = "Provider Album",
    release_year: int | None = None,
    tags: dict[str, float] | None = None,
    genres: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "provider": "spotify",
        "title": title,
        "artist": artist,
        "album": album,
        "release_year": release_year,
        "tags": tags or {},
        "genres": genres or {},
        "external_urls": {
            "spotify": "https://open.spotify.com/track/" + track_id.split(":")[-1]
        },
    }


_GENRE_CASES = [
    ("basic-rock-1", "basic", "推荐一首摇滚", "rock", ["rock"], "goal_satisfied"),
    ("basic-rock-2", "basic", "推荐两首摇滚", "rock", ["rock"], "goal_satisfied"),
    ("basic-jazz-1", "basic", "推荐一首爵士", "mixed", ["jazz"], "goal_satisfied"),
    ("basic-soul-1", "basic", "推荐一首灵魂", "mixed", ["soul"], "insufficient_candidates"),
    ("basic-folk-1", "basic", "推荐一首民谣", "mixed", ["folk"], "insufficient_candidates"),
    ("basic-electronic-1", "basic", "推荐一首电子", "mixed", ["electronic"], "insufficient_candidates"),
    ("basic-ambient-1", "basic", "推荐一首氛围", "mixed", ["ambient"], "insufficient_candidates"),
    ("basic-punk-1", "basic", "推荐一首朋克", "mixed", ["punk"], "insufficient_candidates"),
    ("basic-metal-1", "basic", "推荐一首金属", "mixed", ["metal"], "insufficient_candidates"),
    ("basic-country-1", "basic", "推荐一首乡村", "mixed", ["country"], "insufficient_candidates"),
    ("basic-blues-1", "basic", "推荐一首蓝调", "mixed", ["blues"], "insufficient_candidates"),
    ("basic-british-1", "basic", "推荐一首英伦摇滚", "rock", ["british", "rock"], "goal_satisfied"),
]

_SESSION_CASES = [
    _case(
        "session-more-1",
        "session",
        "换一批，不要刚才推荐过的歌曲",
        prelude_queries=["推荐一首摇滚，每位歌手最多一首"],
        expected={
            "stop_reason": "goal_satisfied",
            "intent": "more",
            "session_current_intent": "more",
            "session_exclude_seen": True,
            "exclude_previous_results": True,
            "tool_names_exact": ["L1.inspect_user_profile", "L4.rank_candidates"],
        },
    ),
    _case(
        "session-more-2",
        "session",
        "再来一首",
        prelude_queries=["推荐一首摇滚"],
        expected={
            "stop_reason": "goal_satisfied",
            "intent": "more",
            "exclude_previous_results": True,
        },
    ),
    _case(
        "session-seen-1",
        "session",
        "推荐一首摇滚",
        session_setup={
            "seen_track_ids": ["rock-a"],
            "active_constraints": {"exclude_seen": True},
        },
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_track_ids": ["rock-a"],
        },
    ),
    _case(
        "session-seed-1",
        "session",
        "推荐一首摇滚",
        session_setup={"seed_track_ids": ["british-rock"]},
        expected={
            "stop_reason": "goal_satisfied",
            "evidence_seed_track_ids": ["british-rock"],
        },
    ),
    _case(
        "session-last-query-1",
        "session",
        "推荐一首摇滚",
        expected={
            "stop_reason": "goal_satisfied",
            "session_current_intent": "recommend",
            "session_last_user_query": "推荐一首摇滚",
        },
    ),
    _case(
        "session-limit-1",
        "session",
        "换一批",
        prelude_queries=["推荐一首摇滚"],
        expected={
            "stop_reason": "goal_satisfied",
            "intent": "more",
            "max_result_count": 1,
        },
    ),
    _case(
        "session-limit-2",
        "session",
        "再来两首摇滚",
        prelude_queries=["推荐一首摇滚"],
        expected={
            "stop_reason": "goal_satisfied",
            "intent": "more",
            "min_result_count": 2,
        },
    ),
    _case(
        "session-phase-1",
        "session",
        "推荐一首摇滚",
        expected={
            "phase_status": {
                "memory_read": "completed",
                "query_understanding": "completed",
                "candidate_ranking": "completed",
                "trajectory_write": "completed",
                "feedback_write": "pending",
            }
        },
    ),
    _case(
        "session-exclude-terms-1",
        "session",
        "推荐一首摇滚",
        session_setup={"exclude_terms": ["artist a"]},
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_artists": ["Artist A"],
        },
    ),
    _case(
        "session-metadata-1",
        "session",
        "推荐一首摇滚",
        session_setup={
            "seed_track_ids": ["seed-rock"],
            "active_constraints": {"exclude_seen": False, "limit": 1},
        },
        expected={
            "stop_reason": "goal_satisfied",
            "evidence_active_constraint_keys": ["exclude_seen", "limit"],
        },
    ),
]

_CONSTRAINT_CASES = [
    _case(
        "constraint-exclude-1",
        "constraints",
        "推荐一首摇滚，不要“Artist B”",
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_artists": ["Artist B"],
            "exclude_terms_include": ["artist b"],
        },
    ),
    _case(
        "constraint-exclude-2",
        "constraints",
        "不要artistb",
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_artists": ["Artist B"],
        },
    ),
    _case(
        "constraint-diversity-1",
        "constraints",
        "推荐两首摇滚，不要重复歌手",
        expected={
            "stop_reason": "goal_satisfied",
            "max_per_artist": 1,
        },
    ),
    _case(
        "constraint-diversity-2",
        "constraints",
        "推荐两首摇滚，每位歌手最多一首",
        expected={
            "stop_reason": "goal_satisfied",
            "max_per_artist": 1,
        },
    ),
    _case(
        "constraint-count-1",
        "constraints",
        "推荐五首摇滚",
        expected={
            "stop_reason": "goal_satisfied",
            "min_result_count": 5,
        },
    ),
    _case(
        "constraint-count-2",
        "constraints",
        "推荐一首摇滚",
        expected={
            "stop_reason": "goal_satisfied",
            "max_result_count": 1,
        },
    ),
    _case(
        "constraint-similar-1",
        "constraints",
        "有没有和pink floyd差不多的，但是不要这个乐队",
        expected={
            "stop_reason": "goal_satisfied",
            "intent": "recommend",
            "exclude_terms_include": ["pink floyd"],
        },
    ),
    _case(
        "constraint-similar-2",
        "constraints",
        "有没有英伦摇滚，但不是 Pink Floyd 的歌",
        expected={
            "stop_reason": "insufficient_candidates",
            "preference_terms_include": ["british", "rock"],
            "exclude_terms_include": ["pink floyd"],
        },
    ),
    _case(
        "constraint-query-1",
        "constraints",
        "推荐 1 首 2020 年之后的英伦独立摇滚",
        expected={
            "stop_reason": "goal_satisfied",
            "preference_terms_include": ["british indie rock", "indie rock", "rock"],
        },
    ),
    _case(
        "constraint-query-2",
        "constraints",
        "推荐一首英伦摇滚",
        expected={
            "stop_reason": "goal_satisfied",
            "preference_terms_include": ["british", "rock"],
        },
    ),
]

_FEEDBACK_CASES = [
    _case(
        "feedback-skip-1",
        "feedback",
        "推荐一首摇滚",
        session_setup={
            "temporary_feedback": [{"track_id": "rock-a", "event": "skipped"}]
        },
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_track_ids": ["rock-a"],
            "evidence_feedback_events": ["skipped"],
        },
    ),
    _case(
        "feedback-hide-track-1",
        "feedback",
        "推荐一首摇滚",
        session_setup={
            "temporary_feedback": [{"track_id": "rock-b", "event": "hide_track"}]
        },
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_track_ids": ["rock-b"],
            "evidence_feedback_events": ["hide_track"],
        },
    ),
    _case(
        "feedback-hide-artist-1",
        "feedback",
        "推荐一首摇滚",
        session_setup={
            "temporary_feedback": [{"value": "Artist A", "event": "hide_artist"}]
        },
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_artists": ["Artist A"],
            "evidence_feedback_events": ["hide_artist"],
        },
    ),
    _case(
        "feedback-liked-seed-1",
        "feedback",
        "推荐一首摇滚",
        session_setup={
            "temporary_feedback": [{"track_id": "british-rock", "event": "liked"}]
        },
        expected={
            "stop_reason": "goal_satisfied",
            "evidence_seed_track_ids": ["british-rock"],
            "evidence_feedback_events": ["liked"],
        },
    ),
    _case(
        "feedback-saved-seed-1",
        "feedback",
        "推荐一首摇滚",
        session_setup={
            "temporary_feedback": [{"track_id": "seed-rock", "event": "saved"}]
        },
        expected={
            "stop_reason": "goal_satisfied",
            "evidence_seed_track_ids": ["seed-rock"],
            "evidence_feedback_events": ["saved"],
        },
    ),
    _case(
        "feedback-request-similar-1",
        "feedback",
        "推荐一首摇滚",
        session_setup={
            "temporary_feedback": [
                {"track_id": "british-rock", "event": "request_similar"}
            ]
        },
        expected={
            "stop_reason": "goal_satisfied",
            "evidence_seed_track_ids": ["british-rock"],
            "evidence_feedback_events": ["request_similar"],
        },
    ),
]

_PROVIDER_TRACKS = {
    "britpop": [
        _provider_track(
            "spotify:track:provider_britpop_1",
            "Live Forever",
            "Oasis",
            album="Definitely Maybe",
            tags={"britpop": 1.0, "british": 1.0, "rock": 1.0},
        )
    ],
    "indie_year": [
        _provider_track(
            "spotify:track:provider_old",
            "Old Indie",
            "Band A",
            release_year=2019,
            tags={"indie": 1.0, "rock": 1.0},
        ),
        _provider_track(
            "spotify:track:provider_new",
            "New Indie",
            "Band B",
            release_year=2021,
            tags={"indie": 1.0, "rock": 1.0},
        ),
    ],
    "classic_british": [
        _provider_track(
            "spotify:track:provider_wonderwall",
            "Wonderwall",
            "Oasis",
            album="Morning Glory",
            release_year=1995,
            tags={"british": 1.0, "rock": 1.0},
        )
    ],
    "empty": [],
    "jazz": [
        _provider_track(
            "spotify:track:provider_jazz",
            "Blue in Green",
            "Miles Davis",
            release_year=1959,
            tags={"jazz": 1.0},
        )
    ],
    "soul": [
        _provider_track(
            "spotify:track:provider_soul",
            "A Change Is Gonna Come",
            "Sam Cooke",
            release_year=1964,
            tags={"soul": 1.0},
        )
    ],
}

_PROVIDER_CASES = [
    _case(
        "provider-britpop-1",
        "provider",
        "推荐一首 britpop rock",
        provider_tracks=_PROVIDER_TRACKS["britpop"],
        expected={
            "stop_reason": "goal_satisfied",
            "tool_names_exact": ["get_user_memory", "search_tracks"],
            "included_track_ids": ["spotify:track:provider_britpop_1"],
            "phase_status": {
                "external_search": "completed",
                "candidate_ranking": "skipped",
            },
        },
    ),
    _case(
        "provider-british-year-1",
        "provider",
        "推荐 1 首 2020 年之后的英伦独立摇滚",
        provider_tracks=_PROVIDER_TRACKS["indie_year"],
        expected={
            "stop_reason": "goal_satisfied",
            "included_track_ids": ["spotify:track:provider_new"],
            "excluded_track_ids": ["spotify:track:provider_old"],
        },
    ),
    _case(
        "provider-classic-1",
        "provider",
        "给我1首经典的英伦摇滚",
        provider_tracks=_PROVIDER_TRACKS["classic_british"],
        expected={
            "stop_reason": "goal_satisfied",
            "included_track_ids": ["spotify:track:provider_wonderwall"],
            "tool_names_include": ["search_tracks"],
        },
    ),
    _case(
        "provider-empty-1",
        "provider",
        "推荐一首 rock",
        provider_tracks=_PROVIDER_TRACKS["empty"],
        expected={
            "stop_reason": "insufficient_candidates",
            "max_result_count": 0,
            "tool_names_exact": ["get_user_memory", "search_tracks"],
        },
    ),
    _case(
        "provider-jazz-1",
        "provider",
        "推荐一首 jazz",
        provider_tracks=_PROVIDER_TRACKS["jazz"],
        expected={
            "stop_reason": "goal_satisfied",
            "included_track_ids": ["spotify:track:provider_jazz"],
            "tool_names_exact": ["get_user_memory", "search_tracks"],
        },
    ),
    _case(
        "provider-soul-1",
        "provider",
        "推荐一首 soul",
        provider_tracks=_PROVIDER_TRACKS["soul"],
        expected={
            "stop_reason": "goal_satisfied",
            "included_track_ids": ["spotify:track:provider_soul"],
            "tool_names_exact": ["get_user_memory", "search_tracks"],
        },
    ),
]

_EDGE_CASES = [
    _case(
        "edge-empty-profile-1",
        "edge",
        "推荐两首摇滚",
        profile_key="empty",
        expected={
            "stop_reason": "empty_profile",
            "max_result_count": 0,
            "tool_names_exact": ["L1.inspect_user_profile"],
        },
    ),
    _case(
        "edge-empty-profile-2",
        "edge",
        "推荐一首摇滚",
        profile_key="empty",
        expected={
            "stop_reason": "empty_profile",
            "max_result_count": 0,
        },
    ),
    _case(
        "edge-legacy-seen-1",
        "edge",
        "推荐一首摇滚",
        session_setup={
            "exclude_terms": ["刚才推荐过的歌曲"],
            "seen_track_ids": ["rock-a"],
            "active_constraints": {"exclude_seen": True},
        },
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_track_ids": ["rock-a"],
        },
    ),
    _case(
        "edge-insufficient-1",
        "edge",
        "推荐十首摇滚",
        expected={
            "stop_reason": "insufficient_candidates",
            "tool_names_include": ["L3.retrieve_candidates"],
        },
    ),
    _case(
        "edge-session-only-exclusion-1",
        "edge",
        "推荐一首摇滚",
        session_setup={
            "temporary_feedback": [{"value": "Artist B", "event": "hide_artist"}]
        },
        expected={
            "stop_reason": "goal_satisfied",
            "excluded_artists": ["Artist B"],
        },
    ),
    _case(
        "edge-request-blank-like-1",
        "edge",
        "推荐一首摇滚",
        session_setup={"last_user_query": "上次我想听摇滚"},
        expected={
            "stop_reason": "goal_satisfied",
            "session_last_user_query": "推荐一首摇滚",
        },
    ),
]


EVAL_CASES_V1: list[dict[str, Any]] = [
        *[
        _case(
            case_id,
            category,
            query,
            profile_key=profile_key,
            expected={
                "stop_reason": expected_stop,
                **({"min_result_count": 1} if expected_stop == "goal_satisfied" else {}),
                "intent": "recommend",
                "preference_terms_include": preference_terms,
                **(
                    {"tool_names_exact": ["L1.inspect_user_profile", "L4.rank_candidates"]}
                    if expected_stop == "goal_satisfied"
                    else {"tool_names_include": ["L3.retrieve_candidates"]}
                ),
            },
        )
        for case_id, category, query, profile_key, preference_terms, expected_stop in _GENRE_CASES
    ],
    *_SESSION_CASES,
    *_CONSTRAINT_CASES,
    *_FEEDBACK_CASES,
    *_PROVIDER_CASES,
    *_EDGE_CASES,
]


if len(EVAL_CASES_V1) != 50:
    raise AssertionError(f"expected 50 eval cases, found {len(EVAL_CASES_V1)}")

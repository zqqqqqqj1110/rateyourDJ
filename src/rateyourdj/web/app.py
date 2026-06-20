from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from rateyourdj.l1 import JsonProfileStore, ProfileNotFoundError
from rateyourdj.l2 import JsonSongStore, SongNotFoundError
from rateyourdj.l4 import RecommendationRankingService
from rateyourdj.l5 import FeedbackService
from rateyourdj.l6 import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    AgentToolRegistryV1,
    JsonSessionStore,
    JsonTrajectoryStore,
    LLMProvider,
    RecommendationAgentService,
    configured_llm_provider,
)
from rateyourdj.providers import (
    ExternalMusicProvider,
    configured_music_provider_from_env,
)
from rateyourdj.domain import (
    DeepSeekTrackGenerator,
    DiscoveryService,
    ExplanationGenerator,
    TasteSeedTrackGenerator,
)


def _default_track_generator() -> Any:
    """Use DeepSeek generation when a key is set, else a local taste seed."""
    generator = DeepSeekTrackGenerator.from_env()
    if generator is not None:
        return generator
    return TasteSeedTrackGenerator()


def create_app(
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
    trajectory_dir: str | Path = "data/trajectories",
    session_dir: str | Path = "data/sessions",
    llm_provider: LLMProvider | None = None,
    music_provider: ExternalMusicProvider | None = None,
    track_generator: Any | None = None,
    auto_configure_track_generator: bool = True,
    auto_configure_music_provider: bool = True,
    agent_mode: str = "auto",
) -> Flask:
    app = Flask(__name__)
    profile_store = JsonProfileStore(profile_dir)
    song_store = JsonSongStore(song_dir)
    trajectory_store = JsonTrajectoryStore(trajectory_dir)
    session_store = JsonSessionStore(session_dir)
    ranking_service = RecommendationRankingService(profile_store, song_store)
    feedback_service = FeedbackService(
        profile_store,
        song_store,
        trajectory_store,
    )
    resolved_music_provider = (
        music_provider
        if music_provider is not None
        else configured_music_provider_from_env()
        if auto_configure_music_provider
        else None
    )
    resolved_track_generator = (
        track_generator
        if track_generator is not None
        else _default_track_generator()
        if auto_configure_track_generator
        else None
    )
    agent_service = RecommendationAgentService(
        ranking_service,
        song_store,
        trajectory_store,
        session_store,
        model_tool_registry=(
            AgentToolRegistryV1.default(
                profile_store,
                song_store,
                music_provider=resolved_music_provider,
                track_generator=resolved_track_generator,
            )
            if resolved_music_provider is not None
            else None
        ),
        llm_provider=llm_provider,
        agent_mode=agent_mode,
        discovery_service=(
            DiscoveryService(resolved_track_generator, resolved_music_provider)
            if resolved_music_provider is not None
            and resolved_track_generator is not None
            else None
        ),
    )

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/agent-status")
    def agent_status() -> Any:
        return jsonify(
            {
                "configured_agent_mode": agent_mode,
                "provider": (
                    llm_provider.name if llm_provider is not None else None
                ),
                "model_enabled": llm_provider is not None,
                "music_provider_enabled": resolved_music_provider is not None,
            }
        )

    @app.get("/api/profile/<user_id>")
    def profile(user_id: str) -> Any:
        stored = profile_store.load(user_id)
        return jsonify(
            {
                "user_id": stored.user_id,
                "collection_count": len(stored.collection_song_ids),
                "feedback_count": len(stored.feedback_memory),
                "top_artists": _top_preferences(stored.artist_preferences),
                "top_genres": _top_preferences(stored.genre_preferences),
                "version": stored.version,
            }
        )

    @app.get("/api/recommendations/<user_id>")
    def recommendations(user_id: str) -> Any:
        top_k = _query_int("top_k", default=10, minimum=1, maximum=50)
        max_per_artist = _query_int(
            "max_per_artist",
            default=2,
            minimum=1,
            maximum=10,
        )
        result = ranking_service.rank(
            user_id,
            top_k=top_k,
            max_per_artist=max_per_artist,
        )
        return jsonify(_attach_spotify_playback(result.to_dict(), song_store))

    @app.post("/api/chat/<user_id>")
    def chat(user_id: str) -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        result = agent_service.recommend(
            user_id,
            _required_string(payload, "query"),
            default_top_k=_optional_int(
                payload,
                "default_top_k",
                default=10,
                minimum=1,
                maximum=50,
            ),
            session_id=_optional_string(payload, "session_id"),
            max_steps=_optional_int(
                payload,
                "max_steps",
                default=5,
                minimum=2,
                maximum=10,
            ),
            agent_mode=_optional_choice(
                payload,
                "agent_mode",
                default=agent_mode,
                choices={"auto", "model", "rules"},
            ),
        )
        return jsonify(
            _attach_spotify_playback(result.to_dict(), song_store)
        ), 201

    @app.post("/api/v1/agent/recommend")
    def v1_agent_recommend() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        constraints = _optional_mapping(payload, "constraints")
        result = agent_service.recommend(
            _required_string(payload, "user_id"),
            _required_string(payload, "message"),
            default_top_k=_optional_int(
                constraints,
                "limit",
                default=10,
                minimum=1,
                maximum=50,
            ),
            session_id=_optional_string(payload, "session_id"),
            max_steps=_optional_int(
                payload,
                "max_steps",
                default=5,
                minimum=2,
                maximum=10,
            ),
            agent_mode=_optional_choice(
                payload,
                "mode",
                default=agent_mode,
                choices={"auto", "model", "rules"},
            ),
        )
        return jsonify(
            _agent_recommend_response_v1(
                result.to_dict(),
                song_store,
                include_trace=_optional_bool(
                    payload,
                    "include_trace",
                    default=False,
                ),
                user_memory=_safe_user_memory(profile_store, result.user_id),
            )
        ), 201

    @app.post("/api/v1/agent/feedback")
    def v1_agent_feedback() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        context = _optional_mapping(payload, "context")
        recommendation_context = dict(context)
        run_id = _optional_string(payload, "run_id")
        if run_id is not None:
            recommendation_context.setdefault("trajectory_id", run_id)
        record = feedback_service.record(
            _required_string(payload, "user_id"),
            _required_string(payload, "track_id"),
            _required_string(payload, "event"),
            reward_score=payload.get("reward_score"),
            recommendation_context=recommendation_context or None,
        )
        return jsonify(
            {
                "run_id": run_id,
                "track_id": record.song_id,
                "event": record.feedback_type,
                "reward_score": record.reward_score,
            }
        ), 201

    @app.get("/api/v1/agent/session/<session_id>")
    def v1_agent_session(session_id: str) -> Any:
        user_id = request.args.get("user_id")
        if not user_id or not user_id.strip():
            raise ValueError("user_id query parameter is required")
        session = session_store.load_or_create(user_id.strip(), session_id)
        return jsonify(
            {
                "session_id": session.session_id,
                "user_id": session.user_id,
                "turn_count": session.turn_count,
                "current_intent": session.current_intent,
                "last_user_query": session.last_user_query,
                "preference_terms": list(session.preference_terms),
                "exclude_terms": list(session.exclude_terms),
                "seen_track_ids": list(session.seen_track_ids),
                "last_recommendation_ids": list(
                    session.last_recommendation_ids
                ),
                "last_run_id": session.last_run_id,
                "messages": [dict(item) for item in session.messages],
            }
        )

    @app.get("/api/collection/<user_id>")
    def collection(user_id: str) -> Any:
        stored = profile_store.load(user_id)
        feedback_favorites = {
            str(record["song_id"])
            for record in stored.feedback_memory
            if record.get("feedback_type") in {"favorite", "playlist_add"}
            and record.get("song_id")
        }
        feedback_tracks = _feedback_collection_tracks(stored.feedback_memory)
        songs = []
        missing_song_ids = []
        for song_id in stored.collection_song_ids:
            if not _song_exists(song_store, song_id):
                missing_song_ids.append(song_id)
                songs.append(
                    _missing_collection_song(
                        song_id,
                        feedback_favorites,
                        feedback_tracks.get(song_id, {}),
                    )
                )
            else:
                song = song_store.load(song_id)
                songs.append(
                    {
                        "song_id": song.song_id,
                        "title": song.metadata.get("title"),
                        "artist": song.metadata.get("artist"),
                        "album": song.metadata.get("album"),
                        "genres": [
                            name
                            for name, _score in sorted(
                                song.genres.items(),
                                key=lambda item: (-item[1], item[0].casefold()),
                            )[:3]
                        ],
                        "added_via_feedback": song.song_id in feedback_favorites,
                        "profile_missing": False,
                    }
                )
        return jsonify(
            {
                "user_id": user_id,
                "total": len(songs),
                "collection_count": len(stored.collection_song_ids),
                "missing_song_ids": missing_song_ids,
                "songs": songs,
            }
        )

    @app.get("/api/feedback/<user_id>")
    def feedback_summary(user_id: str) -> Any:
        return jsonify(feedback_service.summary(user_id).to_dict())

    @app.post("/api/feedback/<user_id>")
    def record_feedback(user_id: str) -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        record = feedback_service.record(
            user_id,
            _required_string(payload, "song_id"),
            _required_string(payload, "feedback_type"),
            reward_score=payload.get("reward_score"),
            recommendation_context=payload.get("recommendation_context"),
        )
        return jsonify(record.to_dict()), 201

    @app.delete("/api/collection/<user_id>/<path:track_id>")
    def remove_collection_track(user_id: str, track_id: str) -> Any:
        track_id = track_id.strip()
        if not track_id:
            raise ValueError("track_id must be a non-empty string")
        removed = {"value": False}

        def _drop(profile: Any) -> Any:
            if track_id in profile.collection_song_ids:
                profile.collection_song_ids = [
                    song_id
                    for song_id in profile.collection_song_ids
                    if song_id != track_id
                ]
                removed["value"] = True
            return profile

        if not profile_store.exists(user_id):
            raise ProfileNotFoundError(user_id)
        updated = profile_store.update(user_id, _drop)
        return jsonify(
            {
                "user_id": user_id,
                "track_id": track_id,
                "removed": removed["value"],
                "collection_count": len(updated.collection_song_ids),
            }
        )

    @app.errorhandler(ValueError)
    @app.errorhandler(ProfileNotFoundError)
    @app.errorhandler(SongNotFoundError)
    def handle_known_error(error: Exception) -> Any:
        status = (
            404
            if isinstance(error, (ProfileNotFoundError, SongNotFoundError))
            else 400
        )
        return jsonify({"error": str(error)}), status

    return app


def _top_preferences(
    values: dict[str, float],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return [
        {"name": name, "weight": weight}
        for name, weight in sorted(
            values.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )[:limit]
    ]


def _safe_user_memory(
    profile_store: JsonProfileStore,
    user_id: str,
) -> dict[str, Any]:
    """Load preference maps for explanation; never raise on a missing profile."""
    try:
        profile = profile_store.load(user_id)
    except (ProfileNotFoundError, ValueError):
        return {}
    return {
        "artist_preferences": dict(profile.artist_preferences),
        "genre_preferences": dict(profile.genre_preferences),
        "tag_preferences": dict(profile.tag_preferences),
    }


def _attach_spotify_playback(
    payload: dict[str, Any],
    song_store: JsonSongStore,
) -> dict[str, Any]:
    enriched = dict(payload)
    ranked_songs = []
    for ranked_song in payload.get("ranked_songs", []):
        song = dict(ranked_song)
        spotify_track_id = song.get("spotify_track_id")
        song_id = song.get("song_id")
        if isinstance(song_id, str) and _song_exists(song_store, song_id):
            spotify_track_id = song_store.load(song_id).external_ids.get(
                "spotify_track_id"
            )
        if not _valid_spotify_track_id(spotify_track_id):
            spotify_track_id = None
        song.update(
            {
                "spotify_track_id": spotify_track_id,
                "spotify_embed_url": (
                    f"https://open.spotify.com/embed/track/{spotify_track_id}"
                    if spotify_track_id
                    else None
                ),
                "spotify_url": (
                    f"https://open.spotify.com/track/{spotify_track_id}"
                    if spotify_track_id
                    else song.get("spotify_url")
                ),
                "preview_available": spotify_track_id is not None,
            }
        )
        ranked_songs.append(song)
    enriched["ranked_songs"] = ranked_songs
    return enriched


def _agent_recommend_response_v1(
    payload: dict[str, Any],
    song_store: JsonSongStore,
    *,
    include_trace: bool,
    user_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = _attach_spotify_playback(payload, song_store)
    explanation_generator = ExplanationGenerator()
    recommendations = [
        _agent_recommendation_v1(
            song,
            song_store,
            parsed_request=enriched.get("parsed_request", {}),
            seed_song_ids=enriched.get("seed_song_ids", []),
            explanation_generator=explanation_generator,
            user_memory=user_memory or {},
        )
        for song in enriched.get("ranked_songs", [])
    ]
    response = {
        "run_id": enriched.get("trajectory_id"),
        "session_id": enriched.get("session_id"),
        "user_id": enriched.get("user_id"),
        "message": enriched.get("message"),
        "intent": (
            enriched.get("parsed_request", {}).get("intent")
            if isinstance(enriched.get("parsed_request"), dict)
            else None
        ),
        "recommendations": recommendations,
        "memory_updates": {
            "session_seen_track_ids": [
                item["track"]["track_id"]
                for item in recommendations
                if item["track"].get("track_id")
            ],
        },
        "trace": None,
    }
    if include_trace:
        response["trace"] = {
            "agent_mode": enriched.get("agent_mode"),
            "provider": enriched.get("provider"),
            "fallback_reason": enriched.get("fallback_reason"),
            "stop_reason": enriched.get("stop_reason"),
            "attempts": enriched.get("attempts"),
            "parsed_request": enriched.get("parsed_request"),
            "tool_calls": enriched.get("tool_calls", []),
            "agent_decisions": enriched.get("agent_decisions", []),
            "seed_song_ids": enriched.get("seed_song_ids", []),
            "missing_seed_song_ids": enriched.get("missing_seed_song_ids", []),
        }
    return response


def _agent_recommendation_v1(
    ranked_song: dict[str, Any],
    song_store: JsonSongStore,
    *,
    parsed_request: Any,
    seed_song_ids: Any,
    explanation_generator: "ExplanationGenerator | None" = None,
    user_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if explanation_generator is not None:
        explanation = explanation_generator.explain_track(
            ranked_song,
            user_memory=user_memory or {},
        )
        reasons = [reason.to_dict() for reason in explanation.reasons]
    else:
        reasons = _recommendation_reasons_v1(ranked_song, parsed_request)
    return {
        "rank": ranked_song.get("rank"),
        "track": _track_payload_v1(ranked_song, song_store),
        "score": ranked_song.get("final_score"),
        "evidence": {
            "score_breakdown": ranked_song.get("score_breakdown", {}),
            "ranking_reasons": ranked_song.get("ranking_reasons", []),
            "best_seed_song_id": ranked_song.get("best_seed_song_id"),
            "retrieval_sources": ranked_song.get("retrieval_sources", []),
            "preference_terms": (
                parsed_request.get("preference_terms", [])
                if isinstance(parsed_request, dict)
                else []
            ),
            "seed_song_ids": (
                list(seed_song_ids) if isinstance(seed_song_ids, list) else []
            ),
        },
        "reasons": reasons,
        "actions": {
            "play": True,
            "like": True,
            "dislike": True,
            "save": True,
            "skip": True,
        },
    }


def _track_payload_v1(
    ranked_song: dict[str, Any],
    song_store: JsonSongStore,
) -> dict[str, Any]:
    song_id = ranked_song.get("song_id")
    metadata: dict[str, Any] = {}
    genres: list[str] = list(ranked_song.get("genres") or [])
    external_ids: dict[str, Any] = {}
    if isinstance(song_id, str) and _song_exists(song_store, song_id):
        song = song_store.load(song_id)
        metadata = dict(song.metadata)
        external_ids = dict(song.external_ids)
        genres = [
            name
            for name, _score in sorted(
                song.genres.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ]
    spotify_track_id = ranked_song.get("spotify_track_id") or external_ids.get(
        "spotify_track_id"
    )
    return {
        "track_id": song_id,
        "title": metadata.get("title") or ranked_song.get("title"),
        "artist": metadata.get("artist") or ranked_song.get("artist"),
        "album": metadata.get("album") or ranked_song.get("album"),
        "release_year": metadata.get("release_year")
        or ranked_song.get("release_year"),
        "duration_ms": metadata.get("duration_ms")
        or ranked_song.get("duration_ms"),
        "genres": genres,
        "external_ids": {"spotify_track_id": spotify_track_id},
        "external_urls": {"spotify": ranked_song.get("spotify_url")},
        "embed_urls": {"spotify": ranked_song.get("spotify_embed_url")},
        "preview_available": ranked_song.get("preview_available", False),
    }


def _recommendation_reasons_v1(
    ranked_song: dict[str, Any],
    parsed_request: Any,
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    ranking_reasons = ranked_song.get("ranking_reasons")
    if isinstance(ranking_reasons, list):
        for reason in ranking_reasons[:2]:
            if isinstance(reason, str) and reason:
                reasons.append({"type": "profile_match", "text": reason})
    if isinstance(parsed_request, dict):
        preference_terms = parsed_request.get("preference_terms")
        if isinstance(preference_terms, list) and preference_terms:
            reasons.append(
                {
                    "type": "query_match",
                    "text": (
                        "Matches the current request preferences: "
                        + ", ".join(str(term) for term in preference_terms[:4])
                    ),
                }
            )
    if not reasons:
        reasons.append(
            {
                "type": "ranking",
                "text": "Selected from the current ranking and session context.",
            }
        )
    return reasons[:3]


def _song_exists(song_store: JsonSongStore, song_id: str) -> bool:
    try:
        return song_store.exists(song_id)
    except ValueError:
        return False


def _missing_collection_song(
    song_id: str,
    feedback_favorites: set[str],
    feedback_track: dict[str, Any] | None = None,
) -> dict[str, Any]:
    track = feedback_track or {}
    return {
        "song_id": song_id,
        "title": track.get("title") or _readable_track_title(song_id),
        "artist": track.get("artist") or "画像待恢复",
        "album": track.get("album") or "收藏记录",
        "genres": [],
        "added_via_feedback": song_id in feedback_favorites,
        "profile_missing": True,
    }


def _feedback_collection_tracks(
    feedback_memory: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    tracks: dict[str, dict[str, Any]] = {}
    for record in feedback_memory:
        if record.get("feedback_type") not in {"favorite", "playlist_add"}:
            continue
        song_id = record.get("song_id")
        context = record.get("recommendation_context")
        if not isinstance(song_id, str) or not isinstance(context, dict):
            continue
        track = context.get("track")
        if isinstance(track, dict) and song_id not in tracks:
            tracks[song_id] = track
    return tracks


def _readable_track_title(song_id: str) -> str:
    if song_id.startswith("spotify:track:"):
        return song_id
    parts = [part for part in song_id.split("-") if part]
    if len(parts) > 1 and parts[-1].isdigit():
        parts = parts[:-1]
    if len(parts) > 4:
        parts = parts[-4:]
    return " ".join(part.capitalize() for part in parts) or song_id


def _valid_spotify_track_id(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"[A-Za-z0-9]{22}", value)
    )


def _query_int(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string or null")
    return value.strip()


def _optional_mapping(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object or null")
    return value


def _optional_bool(
    payload: dict[str, Any],
    name: str,
    *,
    default: bool,
) -> bool:
    value = payload.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _optional_int(
    payload: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _optional_choice(
    payload: dict[str, Any],
    name: str,
    *,
    default: str,
    choices: set[str],
) -> str:
    value = payload.get(name, default)
    if not isinstance(value, str) or value not in choices:
        raise ValueError(
            f"{name} must be one of: {', '.join(sorted(choices))}"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="rateyourDJ local web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--profile-dir", default="data/user_profiles")
    parser.add_argument("--song-dir", default="data/song_profiles")
    parser.add_argument("--trajectory-dir", default="data/trajectories")
    parser.add_argument("--session-dir", default="data/sessions")
    parser.add_argument(
        "--agent-mode",
        choices=("auto", "model", "rules"),
        default="auto",
    )
    parser.add_argument(
        "--llm-provider",
        choices=("auto", "deepseek", "none"),
        default="auto",
    )
    parser.add_argument(
        "--deepseek-model",
        default=os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
    )
    parser.add_argument(
        "--deepseek-base-url",
        default=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
    )
    parser.add_argument("--debug", action="store_true")
    return parser


def main() -> None:
    from rateyourdj.config import load_dotenv

    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    try:
        llm_provider = configured_llm_provider(
            args.llm_provider,
            model=args.deepseek_model,
            base_url=args.deepseek_base_url,
        )
    except ValueError as error:
        parser.error(str(error))
    app = create_app(
        profile_dir=args.profile_dir,
        song_dir=args.song_dir,
        trajectory_dir=args.trajectory_dir,
        session_dir=args.session_dir,
        llm_provider=llm_provider,
        agent_mode=args.agent_mode,
    )
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()

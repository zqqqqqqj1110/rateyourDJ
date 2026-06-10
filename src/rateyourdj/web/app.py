from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from rateyourdj.l1 import JsonProfileStore, ProfileNotFoundError
from rateyourdj.l2 import JsonSongStore, SongNotFoundError
from rateyourdj.l4 import RecommendationRankingService
from rateyourdj.l5 import FeedbackService


def create_app(
    *,
    profile_dir: str | Path = "data/user_profiles",
    song_dir: str | Path = "data/song_profiles",
) -> Flask:
    app = Flask(__name__)
    profile_store = JsonProfileStore(profile_dir)
    song_store = JsonSongStore(song_dir)
    ranking_service = RecommendationRankingService(profile_store, song_store)
    feedback_service = FeedbackService(profile_store, song_store)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

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
        return jsonify(result.to_dict())

    @app.get("/api/collection/<user_id>")
    def collection(user_id: str) -> Any:
        stored = profile_store.load(user_id)
        feedback_favorites = {
            str(record["song_id"])
            for record in stored.feedback_memory
            if record.get("feedback_type") in {"favorite", "playlist_add"}
            and record.get("song_id")
        }
        songs = []
        for song_id in stored.collection_song_ids:
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
                }
            )
        return jsonify(
            {
                "user_id": user_id,
                "total": len(songs),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="rateyourDJ local web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--profile-dir", default="data/user_profiles")
    parser.add_argument("--song-dir", default="data/song_profiles")
    parser.add_argument("--debug", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = create_app(
        profile_dir=args.profile_dir,
        song_dir=args.song_dir,
    )
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()

import tempfile
import unittest
from pathlib import Path

from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l6 import JsonTrajectoryStore
from rateyourdj.web import create_app


def make_song(
    song_id: str,
    *,
    artist: str,
    tag: str,
    spotify_track_id: str | None = None,
) -> SongProfile:
    song = SongProfile.empty(song_id)
    song.metadata.update(
        {
            "title": song_id,
            "artist": artist,
            "album": "Album",
            "release_year": 2000,
            "duration_ms": 200_000,
            "version_type": "original",
        }
    )
    song.genres = {"rock": 1.0}
    song.source_tags["lastfm_track_tags"] = {tag: 1.0}
    song.external_ids["spotify_track_id"] = spotify_track_id
    song.confidence_score = 1.0
    return song


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_dir = root / "profiles"
        self.song_dir = root / "songs"
        self.trajectory_dir = root / "trajectories"
        self.session_dir = root / "sessions"
        profile_store = JsonProfileStore(self.profile_dir)
        song_store = JsonSongStore(self.song_dir)
        profile_store.save(
            UserProfile(
                user_id="user-1",
                collection_song_ids=["seed"],
                artist_preferences={"Seed Artist": 1.0},
                genre_preferences={"rock": 1.0},
                tag_preferences={"rock": 1.0},
            )
        )
        song_store.save(make_song("seed", artist="Seed Artist", tag="rock"))
        song_store.save(
            make_song(
                "candidate",
                artist="Other Artist",
                tag="rock",
                spotify_track_id="4uLU6hMCjMI75M1A2tKUQC",
            )
        )
        song_store.save(make_song("candidate-2", artist="Third Artist", tag="rock"))
        app = create_app(
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
            trajectory_dir=self.trajectory_dir,
            session_dir=self.session_dir,
            auto_configure_music_provider=False,
        )
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_index_renders_frontend(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"rateyourDJ", response.data)
        self.assertIn(b'id="recommendations"', response.data)
        self.assertIn(b'id="agent-debug-panel"', response.data)

    def test_profile_and_recommendation_endpoints(self) -> None:
        profile = self.client.get("/api/profile/user-1")
        ranking = self.client.get("/api/recommendations/user-1?top_k=5")

        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.get_json()["collection_count"], 1)
        self.assertEqual(ranking.status_code, 200)
        self.assertEqual(
            ranking.get_json()["ranked_songs"][0]["song_id"],
            "candidate",
        )
        self.assertEqual(
            ranking.get_json()["ranked_songs"][0]["spotify_embed_url"],
            (
                "https://open.spotify.com/embed/track/"
                "4uLU6hMCjMI75M1A2tKUQC"
            ),
        )
        self.assertTrue(
            ranking.get_json()["ranked_songs"][0]["preview_available"]
        )

    def test_agent_status_reports_provider_configuration(self) -> None:
        response = self.client.get("/api/agent-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "configured_agent_mode": "auto",
                "provider": None,
                "model_enabled": False,
                "music_provider_enabled": False,
            },
        )

    def test_feedback_endpoint_updates_l5_summary(self) -> None:
        response = self.client.post(
            "/api/feedback/user-1",
            json={
                "song_id": "candidate",
                "feedback_type": "like",
                "recommendation_context": {"rank": 1, "source": "web"},
            },
        )
        summary = self.client.get("/api/feedback/user-1")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["reward_score"], 0.6)
        self.assertEqual(summary.get_json()["positive_events"], 1)

    def test_spotify_playback_events_are_written_to_trajectory(self) -> None:
        recommendation = self.client.post(
            "/api/chat/user-1",
            json={"query": "推荐一首摇滚"},
        ).get_json()
        trajectory_id = recommendation["trajectory_id"]
        song = recommendation["ranked_songs"][0]

        for feedback_type, position in (
            ("play", 0),
            ("play_complete", 190_000),
            ("quick_skip", 5_000),
        ):
            response = self.client.post(
                "/api/feedback/user-1",
                json={
                    "song_id": song["song_id"],
                    "feedback_type": feedback_type,
                    "recommendation_context": {
                        "trajectory_id": trajectory_id,
                        "rank": song["rank"],
                        "final_score": song["final_score"],
                        "source": "spotify_embed",
                        "playback_position_ms": position,
                        "playback_duration_ms": 200_000,
                    },
                },
            )
            self.assertEqual(response.status_code, 201)

        trajectory = JsonTrajectoryStore(self.trajectory_dir).load(
            "user-1",
            trajectory_id,
        )
        self.assertEqual(
            [
                event["feedback_type"]
                for event in trajectory.feedback_events
            ],
            ["play", "play_complete", "quick_skip"],
        )
        self.assertEqual(
            trajectory.feedback_events[1]["recommendation_context"]["source"],
            "spotify_embed",
        )

    def test_chat_endpoint_runs_l6_and_saves_trajectory(self) -> None:
        response = self.client.post(
            "/api/chat/user-1",
            json={"query": "推荐一首摇滚"},
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["parsed_request"]["top_k"], 1)
        self.assertEqual(payload["ranked_songs"][0]["song_id"], "candidate")
        self.assertEqual(
            payload["ranked_songs"][0]["spotify_track_id"],
            "4uLU6hMCjMI75M1A2tKUQC",
        )
        self.assertEqual(payload["seed_song_ids"], ["seed"])
        self.assertEqual(payload["stop_reason"], "goal_satisfied")
        self.assertTrue(payload["session_id"])
        self.assertTrue(
            (
                self.trajectory_dir
                / "user-1"
                / f"{payload['trajectory_id']}.json"
            ).exists()
        )
        feedback = self.client.post(
            "/api/feedback/user-1",
            json={
                "song_id": "candidate",
                "feedback_type": "like",
                "recommendation_context": {
                    "trajectory_id": payload["trajectory_id"],
                    "rank": 1,
                },
            },
        )
        self.assertEqual(feedback.status_code, 201)
        stored = JsonProfileStore(self.profile_dir).load("user-1")
        self.assertEqual(
            stored.feedback_memory[-1]["recommendation_context"][
                "trajectory_id"
            ],
            payload["trajectory_id"],
        )
        trajectory = JsonTrajectoryStore(self.trajectory_dir).load(
            "user-1",
            payload["trajectory_id"],
        )
        self.assertEqual(len(trajectory.feedback_events), 1)
        self.assertEqual(
            trajectory.feedback_events[0]["reward_score"],
            0.6,
        )

    def test_v1_agent_recommend_returns_contract_shape(self) -> None:
        response = self.client.post(
            "/api/v1/agent/recommend",
            json={
                "user_id": "user-1",
                "message": "推荐一首摇滚",
                "constraints": {"limit": 1},
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload["run_id"])
        self.assertTrue(payload["session_id"])
        self.assertEqual(payload["user_id"], "user-1")
        self.assertEqual(payload["trace"], None)
        self.assertEqual(len(payload["recommendations"]), 1)
        recommendation = payload["recommendations"][0]
        self.assertEqual(recommendation["rank"], 1)
        self.assertEqual(recommendation["track"]["track_id"], "candidate")
        self.assertEqual(
            recommendation["track"]["external_urls"]["spotify"],
            "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        )
        self.assertTrue(recommendation["actions"]["like"])
        self.assertTrue(recommendation["reasons"])
        self.assertEqual(
            payload["memory_updates"]["session_seen_track_ids"],
            ["candidate"],
        )

    def test_v1_agent_recommend_can_include_trace(self) -> None:
        response = self.client.post(
            "/api/v1/agent/recommend",
            json={
                "user_id": "user-1",
                "message": "推荐一首摇滚",
                "constraints": {"limit": 1},
                "include_trace": True,
            },
        )

        self.assertEqual(response.status_code, 201)
        trace = response.get_json()["trace"]
        self.assertEqual(trace["agent_mode"], "rules")
        self.assertEqual(trace["stop_reason"], "goal_satisfied")
        self.assertEqual(trace["parsed_request"]["top_k"], 1)
        self.assertTrue(trace["tool_calls"])

    def test_v1_agent_recommend_validates_payload(self) -> None:
        response = self.client.post(
            "/api/v1/agent/recommend",
            json={"message": "推荐一首摇滚"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("user_id", response.get_json()["error"])

    def test_chat_session_supports_more_without_repeating_songs(self) -> None:
        first = self.client.post(
            "/api/chat/user-1",
            json={"query": "推荐一首摇滚"},
        ).get_json()
        second = self.client.post(
            "/api/chat/user-1",
            json={
                "query": "换一批",
                "default_top_k": 1,
                "session_id": first["session_id"],
            },
        ).get_json()

        self.assertEqual(second["session_id"], first["session_id"])
        self.assertNotEqual(
            second["ranked_songs"][0]["song_id"],
            first["ranked_songs"][0]["song_id"],
        )

    def test_chat_model_mode_without_provider_degrades_to_rules(self) -> None:
        response = self.client.post(
            "/api/chat/user-1",
            json={
                "query": "推荐一首摇滚",
                "agent_mode": "model",
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["agent_mode"], "rules")
        self.assertIn("no LLM provider", payload["fallback_reason"])
        self.assertEqual(payload["ranked_songs"][0]["song_id"], "candidate")

    def test_collection_endpoint_marks_feedback_favorites(self) -> None:
        before = self.client.get("/api/collection/user-1")
        response = self.client.post(
            "/api/feedback/user-1",
            json={
                "song_id": "candidate",
                "feedback_type": "favorite",
            },
        )
        after = self.client.get("/api/collection/user-1")

        self.assertEqual(before.get_json()["total"], 1)
        self.assertEqual(before.get_json()["collection_count"], 1)
        self.assertEqual(before.get_json()["missing_song_ids"], [])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.get_json()["total"], 2)
        self.assertEqual(
            after.get_json()["songs"][1],
            {
                "song_id": "candidate",
                "title": "candidate",
                "artist": "Other Artist",
                "album": "Album",
                "genres": ["rock"],
                "added_via_feedback": True,
                "profile_missing": False,
            },
        )

    def test_external_favorite_is_visible_in_collection(self) -> None:
        response = self.client.post(
            "/api/feedback/user-1",
            json={
                "song_id": "spotify:track:abc123",
                "feedback_type": "favorite",
                "recommendation_context": {
                    "source": "web",
                    "track": {
                        "title": "External Song",
                        "artist": "External Artist",
                        "album": "External Album",
                        "spotify_track_id": "abc123",
                    },
                },
            },
        )
        collection = self.client.get("/api/collection/user-1")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(collection.status_code, 200)
        self.assertEqual(collection.get_json()["collection_count"], 2)
        self.assertEqual(collection.get_json()["missing_song_ids"], [
            "spotify:track:abc123"
        ])
        self.assertEqual(
            collection.get_json()["songs"][1],
            {
                "song_id": "spotify:track:abc123",
                "title": "External Song",
                "artist": "External Artist",
                "album": "External Album",
                "genres": [],
                "added_via_feedback": True,
                "profile_missing": True,
            },
        )

    def test_feedback_rejects_unknown_trajectory_before_writing_l1(self) -> None:
        response = self.client.post(
            "/api/feedback/user-1",
            json={
                "song_id": "candidate",
                "feedback_type": "like",
                "recommendation_context": {
                    "trajectory_id": "missing-trajectory",
                },
            },
        )

        self.assertEqual(response.status_code, 400)
        profile = JsonProfileStore(self.profile_dir).load("user-1")
        self.assertEqual(profile.feedback_memory, [])

    def test_collection_endpoint_reports_missing_song_profiles(self) -> None:
        profile_store = JsonProfileStore(self.profile_dir)
        profile = profile_store.load("user-1")
        profile.collection_song_ids.append("missing-song")
        profile_store.save(profile)

        response = self.client.get("/api/collection/user-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["collection_count"], 2)
        self.assertEqual(response.get_json()["total"], 2)
        self.assertEqual(
            response.get_json()["missing_song_ids"],
            ["missing-song"],
        )
        self.assertEqual(
            response.get_json()["songs"][1],
            {
                "song_id": "missing-song",
                "title": "Missing Song",
                "artist": "画像待恢复",
                "album": "收藏记录",
                "genres": [],
                "added_via_feedback": False,
                "profile_missing": True,
            },
        )

    def test_api_returns_json_errors(self) -> None:
        response = self.client.get("/api/recommendations/missing")

        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main()

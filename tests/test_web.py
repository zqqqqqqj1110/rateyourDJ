import tempfile
import unittest
from pathlib import Path

from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.web import create_app


def make_song(
    song_id: str,
    *,
    artist: str,
    tag: str,
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
    song.confidence_score = 1.0
    return song


class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_dir = root / "profiles"
        self.song_dir = root / "songs"
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
        song_store.save(make_song("candidate", artist="Other Artist", tag="rock"))
        app = create_app(
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
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
            },
        )

    def test_api_returns_json_errors(self) -> None:
        response = self.client.get("/api/recommendations/missing")

        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main()

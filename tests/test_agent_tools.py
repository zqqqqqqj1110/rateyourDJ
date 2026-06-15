import tempfile
import unittest
from pathlib import Path

from rateyourdj.l1 import (
    JsonProfileStore,
    UserProfile,
    inspect_user_profile,
)
from rateyourdj.l2 import (
    JsonSongStore,
    SongProfile,
    inspect_song_profile,
)
from rateyourdj.l3 import retrieve_candidates_tool
from rateyourdj.l4 import rank_candidates_tool
from rateyourdj.l5 import inspect_feedback_state


def make_song(song_id: str, *, tag: str = "rock") -> SongProfile:
    song = SongProfile.empty(song_id)
    song.metadata.update(
        {
            "title": song_id,
            "artist": "Artist",
            "album": "Album",
            "release_year": 2000,
            "duration_ms": 200_000,
            "version_type": "original",
        }
    )
    song.source_tags["lastfm_track_tags"] = {tag: 1.0}
    song.genres = {tag: 1.0}
    song.confidence_score = 1.0
    return song


class AgentToolObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_dir = root / "profiles"
        self.song_dir = root / "songs"
        self.profile_store = JsonProfileStore(self.profile_dir)
        self.song_store = JsonSongStore(self.song_dir)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_l1_inspection_reports_empty_collection(self) -> None:
        self.profile_store.save(UserProfile(user_id="user-1"))

        observation = inspect_user_profile("user-1", self.profile_dir)

        self.assertEqual(observation.status, "empty")
        self.assertTrue(observation.retryable)
        self.assertEqual(observation.data["collection_count"], 0)

    def test_l2_inspection_reports_incomplete_profile(self) -> None:
        self.song_store.save(SongProfile.empty("song-1"))

        observation = inspect_song_profile("song-1", self.song_dir)

        self.assertEqual(observation.status, "partial")
        self.assertIn("normalized genres are empty", observation.diagnostics)

    def test_l3_and_l4_observations_suggest_retry_when_pool_is_short(self) -> None:
        self.profile_store.save(
            UserProfile(user_id="user-1", collection_song_ids=["seed"])
        )
        self.song_store.save(make_song("seed"))
        self.song_store.save(make_song("candidate"))

        retrieval = retrieve_candidates_tool(
            "user-1",
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
            top_k=5,
            min_score=0.2,
        )
        ranking = rank_candidates_tool(
            "user-1",
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
            top_k=5,
            min_retrieval_score=0.2,
        )

        self.assertEqual(retrieval.status, "partial")
        self.assertTrue(retrieval.retryable)
        self.assertTrue(retrieval.suggested_actions)
        self.assertEqual(ranking.status, "partial")
        self.assertTrue(ranking.retryable)
        self.assertTrue(ranking.suggested_actions)

    def test_l5_inspection_returns_structured_summary(self) -> None:
        self.profile_store.save(UserProfile(user_id="user-1"))

        observation = inspect_feedback_state(
            "user-1",
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
        )

        self.assertEqual(observation.status, "ok")
        self.assertEqual(observation.data["total_events"], 0)


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongNotFoundError, SongProfile
from rateyourdj.l5 import (
    REWARD_BY_FEEDBACK_TYPE,
    FeedbackService,
    FeedbackSignalModel,
    collect_feedback,
    feedback_schema,
    get_feedback_score,
    get_feedback_summary,
)


def make_song(
    song_id: str,
    *,
    artist: str,
    genre: str,
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
    song.genres = {genre: 1.0}
    song.source_tags["lastfm_track_tags"] = {tag: 1.0}
    return song


class FeedbackServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_dir = root / "profiles"
        self.song_dir = root / "songs"
        self.profile_store = JsonProfileStore(self.profile_dir)
        self.song_store = JsonSongStore(self.song_dir)
        self.profile_store.save(UserProfile(user_id="user-1"))
        self.song_store.save(
            make_song(
                "rock-song",
                artist="Artist A",
                genre="rock",
                tag="energetic",
            )
        )
        self.song_store.save(
            make_song(
                "similar-rock-song",
                artist="Artist A",
                genre="rock",
                tag="energetic",
            )
        )
        self.song_store.save(
            make_song(
                "jazz-song",
                artist="Artist B",
                genre="jazz",
                tag="mellow",
            )
        )
        self.service = FeedbackService(self.profile_store, self.song_store)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_records_default_reward_and_persists_to_l1(self) -> None:
        record = self.service.record(
            "user-1",
            "rock-song",
            "like",
            timestamp="2026-06-11T00:00:00+00:00",
            recommendation_context={"rank": 2},
        )

        profile = self.profile_store.load("user-1")
        self.assertEqual(record.reward_score, 0.6)
        self.assertEqual(profile.feedback_memory, [record.to_dict()])
        self.assertEqual(profile.version, 2)

    def test_accepts_bounded_override_and_rejects_invalid_input(self) -> None:
        record = self.service.record(
            "user-1",
            "rock-song",
            "play",
            reward_score=0.25,
        )
        self.assertEqual(record.reward_score, 0.25)

        with self.assertRaises(ValueError):
            self.service.record("user-1", "rock-song", "unknown")
        with self.assertRaises(ValueError):
            self.service.record(
                "user-1",
                "rock-song",
                "like",
                reward_score=1.1,
            )
        with self.assertRaises(ValueError):
            self.service.record(
                "user-1",
                "rock-song",
                "like",
                timestamp="not-a-timestamp",
            )
        with self.assertRaises(SongNotFoundError):
            self.service.record("user-1", "missing", "like")

    def test_summary_counts_rewards_and_missing_songs(self) -> None:
        self.service.record("user-1", "rock-song", "like")
        self.service.record("user-1", "jazz-song", "skip")
        profile = self.profile_store.load("user-1")
        profile.feedback_memory.append(
            {
                "feedback_type": "play",
                "song_id": "deleted-song",
                "timestamp": "2026-06-11T00:00:00+00:00",
                "reward_score": 0.0,
                "recommendation_context": {},
            }
        )
        self.profile_store.save(profile)

        summary = self.service.summary("user-1")

        self.assertEqual(summary.total_events, 3)
        self.assertEqual(summary.positive_events, 1)
        self.assertEqual(summary.negative_events, 1)
        self.assertEqual(summary.neutral_events, 1)
        self.assertEqual(summary.feedback_type_counts["like"], 1)
        self.assertEqual(summary.missing_song_ids, ["deleted-song"])

    def test_direct_feedback_overrides_transferred_feedback(self) -> None:
        self.service.record("user-1", "rock-song", "like")
        profile = self.profile_store.load("user-1")
        model = FeedbackSignalModel(profile, self.song_store)

        self.assertEqual(model.score(self.song_store.load("rock-song")), 0.6)
        self.assertEqual(
            model.score(self.song_store.load("similar-rock-song")),
            0.6,
        )
        self.assertEqual(model.score(self.song_store.load("jazz-song")), 0.0)

        self.service.record("user-1", "similar-rock-song", "dislike")
        updated_model = FeedbackSignalModel(
            self.profile_store.load("user-1"),
            self.song_store,
        )
        self.assertEqual(
            updated_model.score(self.song_store.load("similar-rock-song")),
            -1.0,
        )

    def test_transferred_feedback_decays_with_similarity(self) -> None:
        partial = make_song(
            "partial-rock-song",
            artist="Different Artist",
            genre="rock",
            tag="different",
        )
        weak = make_song(
            "weak-match",
            artist="Different Artist",
            genre="different",
            tag="energetic",
        )
        self.song_store.save(partial)
        self.song_store.save(weak)
        self.service.record("user-1", "rock-song", "like")
        model = FeedbackSignalModel(
            self.profile_store.load("user-1"),
            self.song_store,
        )

        self.assertEqual(model.score(partial), 0.21)
        self.assertEqual(model.score(weak), 0.24)
        self.assertLess(model.score(partial), 0.6)

    def test_feedback_below_similarity_threshold_does_not_propagate(self) -> None:
        weak = make_song(
            "artist-only-match",
            artist="Artist A",
            genre="different",
            tag="different",
        )
        self.song_store.save(weak)
        self.service.record(
            "user-1",
            "rock-song",
            "play",
            reward_score=0.1,
        )
        model = FeedbackSignalModel(
            self.profile_store.load("user-1"),
            self.song_store,
        )

        self.assertEqual(model.score(weak), 0.0)

    def test_functional_apis(self) -> None:
        record = collect_feedback(
            "user-1",
            "rock-song",
            "favorite",
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
        )
        summary = get_feedback_summary(
            "user-1",
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
        )
        score = get_feedback_score(
            "user-1",
            "rock-song",
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
        )

        self.assertEqual(record.reward_score, 0.8)
        self.assertEqual(summary.total_events, 1)
        self.assertEqual(score, 0.8)
        profile = self.profile_store.load("user-1")
        self.assertEqual(profile.collection_song_ids, ["rock-song"])
        self.assertEqual(profile.artist_preferences, {"Artist A": 1.0})
        self.assertEqual(profile.genre_preferences, {"rock": 1.0})

    def test_playlist_add_also_adds_song_to_collection(self) -> None:
        self.service.record("user-1", "jazz-song", "playlist_add")

        profile = self.profile_store.load("user-1")
        self.assertEqual(profile.collection_song_ids, ["jazz-song"])


class L5SchemaAndCliTests(unittest.TestCase):
    def test_schema_covers_all_rewards(self) -> None:
        self.assertEqual(feedback_schema()["feedback_type"], REWARD_BY_FEEDBACK_TYPE)

    def test_schema_command_prints_feedback_contract(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "rateyourdj.l5.cli", "schema"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(json.loads(result.stdout), feedback_schema())


if __name__ == "__main__":
    unittest.main()

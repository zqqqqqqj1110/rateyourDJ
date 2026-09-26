import unittest

from rateyourdj.contracts import (
    BUCKET_VERSION,
    ContractError,
    assign_bucket,
    is_valid_internal_id,
    make_song_id,
    percentile_ranks,
    song_profile_from_v1,
    strip_eval_only,
    user_context_from_v1,
    validate_record,
)
from rateyourdj.l2.models import SongProfile


def _song(**overrides):
    record = {
        "schema_version": "song-profile/v2",
        "song_id": "s_0123456789abcdef",
        "id_basis": "mbid",
        "title": "Time",
        "artists": [{"name": "Pink Floyd", "mbid": None}],
        "external_ids": {},
        "popularity": {"global_percentile": 0.3, "bucket": "tail",
                       "bucket_version": BUCKET_VERSION},
        "playback": {"source": "youtube", "verified": True},
        "provenance": [],
    }
    record.update(overrides)
    return record


class IdRulesTest(unittest.TestCase):
    def test_song_id_is_deterministic_and_filename_safe(self):
        a, basis = make_song_id(recording_mbid="ABC-def")
        b, _ = make_song_id(recording_mbid="abc-def")
        self.assertEqual(a, b)
        self.assertEqual(basis, "mbid")
        self.assertTrue(is_valid_internal_id(a))
        self.assertRegex(a, r"^s_[0-9a-f]{16}$")

    def test_name_based_id_normalizes_case_and_spaces(self):
        a, basis = make_song_id(artist="Oasis", title="Live  Forever")
        b, _ = make_song_id(artist="oasis", title="live forever")
        self.assertEqual((a, basis), (b, "name"))

    def test_external_uri_is_not_a_valid_internal_id(self):
        self.assertFalse(is_valid_internal_id("spotify:track:2mHchPRtQWet3iIS3jANr1"))


class BucketTest(unittest.TestCase):
    def test_thresholds_10_50(self):
        self.assertEqual(assign_bucket(0.95), "head")
        self.assertEqual(assign_bucket(0.90), "head")
        self.assertEqual(assign_bucket(0.89), "mid")
        self.assertEqual(assign_bucket(0.50), "mid")
        self.assertEqual(assign_bucket(0.49), "tail")
        self.assertEqual(assign_bucket(None), "unknown")

    def test_percentile_ranks_handles_ties_and_missing(self):
        ranks = percentile_ranks({"a": 10, "b": 10, "c": 1000, "d": None, "e": 1})
        self.assertEqual(ranks["c"], 1.0)
        self.assertEqual(ranks["e"], 0.0)
        self.assertEqual(ranks["a"], ranks["b"])
        self.assertIsNone(ranks["d"])


class ValidatorTest(unittest.TestCase):
    def test_valid_song_passes(self):
        validate_record("song_profile", _song())

    def test_bucket_must_match_percentile(self):
        with self.assertRaises(ContractError):
            validate_record("song_profile", _song(popularity={
                "global_percentile": 0.95, "bucket": "tail", "bucket_version": BUCKET_VERSION}))

    def test_unverified_playback_is_rejected(self):
        with self.assertRaises(ContractError):
            validate_record("song_profile", _song(playback={"source": "youtube", "verified": False}))

    def test_feedback_requires_impression(self):
        with self.assertRaises(ContractError):
            validate_record("feedback", {
                "schema_version": "feedback/v2", "feedback_id": "fb_1", "impression_id": "",
                "user_id": "participant_001", "song_id": "s_0123456789abcdef",
                "events": [], "phase": "dev"})

    def test_reject_reason_distinguishes_song_from_mood(self):
        base = {"schema_version": "feedback/v2", "feedback_id": "fb_1", "impression_id": "imp_1",
                "user_id": "participant_001", "song_id": "s_0123456789abcdef",
                "events": [{"type": "quick_skip"}], "phase": "dev"}
        validate_record("feedback", {**base, "survey": {"reject_reason": "not_in_mood_to_explore"}})
        with self.assertRaises(ContractError):
            validate_record("feedback", {**base, "survey": {"reject_reason": "meh"}})

    def test_sft_rejects_hidden_thoughts(self):
        with self.assertRaises(ContractError):
            validate_record("sft_sample", {
                "schema_version": "sft-sample/v2", "sample_id": "x",
                "messages": [{"role": "assistant", "content": "{}", "thought": "secret"}],
                "meta": {"split": "train"}})

    def test_interleaving_arm_must_exist(self):
        imp = {"schema_version": "impression/v2", "impression_id": "imp_1",
               "user_id": "participant_001", "run_id": "run_1", "song_id": "s_0123456789abcdef",
               "rank": 1, "bucket": "tail", "channel": "tail", "strategy_version": "rag-rel-v1",
               "shown_at": "2026-10-01T00:00:00Z",
               "interleaving": {"pair_id": "p1", "arm": "C", "arms": {"A": "x", "B": "y"}}}
        with self.assertRaises(ContractError):
            validate_record("impression", imp)

    def test_eval_only_never_reaches_training(self):
        sample = {"sample_id": "g1", "prompt": [], "eval_only": {"hidden_positives": ["s_x"]}}
        self.assertNotIn("eval_only", strip_eval_only(sample))
        self.assertIn("eval_only", sample)  # original untouched


class MigrationTest(unittest.TestCase):
    V1_PROFILE = {
        "user_id": "demo-user", "version": 78, "updated_at": "2026-06-20T16:12:27+00:00",
        "collection_song_ids": ["spotify:track:2mHchPRtQWet3iIS3jANr1"],
        "artist_preferences": {"Pink Floyd": 0.9}, "genre_preferences": {},
        "tag_preferences": {}, "feedback_memory": [], "conversation_affinity": {},
    }

    def test_v1_profile_reads_as_user_context(self):
        context, candidates = user_context_from_v1(self.V1_PROFILE)
        validate_record("user_context", context)
        self.assertEqual(context["seed_branches"], [])  # seeds need manual confirmation
        self.assertEqual(candidates, ["spotify:track:2mHchPRtQWet3iIS3jANr1"])

    def test_v2_context_passes_through(self):
        context, _ = user_context_from_v1(self.V1_PROFILE)
        again, candidates = user_context_from_v1(context)
        self.assertEqual(again, context)
        self.assertEqual(candidates, [])

    def test_v1_song_profile_reads_as_song_profile_v2(self):
        v1 = SongProfile.from_dict({
            "song_id": "spotify:track:2mHchPRtQWet3iIS3jANr1",
            "external_ids": {"spotify_track_id": None, "musicbrainz_recording_id": None},
            "metadata": {"title": "Time", "artist": "Pink Floyd", "album": None,
                         "release_year": 1973, "duration_ms": None, "version_type": None},
            "source_tags": {"lastfm_track_tags": {"progressive rock": 1.0},
                            "lastfm_artist_tags": {}},
            "genres": {"progressive rock": 0.9},
            "data_source": {}, "confidence_score": None, "version": 1,
            "updated_at": "2026-06-20T00:00:00Z",
        }).to_dict() if hasattr(SongProfile, "from_dict") else None
        if v1 is None:
            self.skipTest("SongProfile.from_dict not available")
        v2 = song_profile_from_v1(v1)
        self.assertTrue(is_valid_internal_id(v2["song_id"]))
        self.assertEqual(v2["external_ids"]["spotify_track"], "2mHchPRtQWet3iIS3jANr1")
        self.assertEqual(v2["external_ids"]["legacy_song_id"], "spotify:track:2mHchPRtQWet3iIS3jANr1")
        self.assertEqual(v2["popularity"]["bucket"], "unknown")
        self.assertEqual(v2["playback"]["source"], "spotify")


if __name__ == "__main__":
    unittest.main()

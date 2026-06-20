"""Tests for evidence-based recommendation explanations."""

import unittest

from rateyourdj.domain import ExplanationGenerator, TrackExplanation


class ExplanationGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = ExplanationGenerator()
        self.memory = {
            "artist_preferences": {"Oasis": 1.0},
            "genre_preferences": {"britpop": 0.9},
            "tag_preferences": {"melodic": 0.7},
        }

    def test_preference_match_produces_reason(self):
        track = {
            "song_id": "s1",
            "title": "Live Forever",
            "artist": "Verve",
            "genres": ["britpop"],
            "tags": ["melodic"],
        }

        explanation = self.generator.explain_track(track, user_memory=self.memory)

        self.assertIsInstance(explanation, TrackExplanation)
        types = {reason.type for reason in explanation.reasons}
        self.assertIn("preference_match", types)
        text = " ".join(reason.text for reason in explanation.reasons)
        self.assertIn("britpop", text)

    def test_preferred_artist_is_high_weight(self):
        track = {
            "song_id": "s1",
            "title": "Wonderwall",
            "artist": "Oasis",
            "genres": [],
            "tags": [],
        }

        explanation = self.generator.explain_track(track, user_memory=self.memory)

        self.assertTrue(explanation.evidence)
        self.assertEqual(explanation.evidence[0].type, "preference_match")
        self.assertIn(
            "Oasis",
            " ".join(reason.text for reason in explanation.reasons),
        )

    def test_discovery_reason_becomes_evidence(self):
        track = {
            "song_id": "s1",
            "title": "Some Song",
            "artist": "Some Artist",
            "discovery_reason": "深夜氛围与长篇编曲契合",
        }

        explanation = self.generator.explain_track(track, user_memory={})

        types = {reason.type for reason in explanation.reasons}
        self.assertIn("discovery", types)
        self.assertIn(
            "深夜氛围与长篇编曲契合",
            " ".join(reason.text for reason in explanation.reasons),
        )

    def test_falls_back_to_session_intent_when_no_evidence(self):
        track = {"song_id": "s1", "title": "Bare Track", "artist": "X"}

        explanation = self.generator.explain_track(track, user_memory={})

        self.assertEqual(len(explanation.reasons), 1)
        self.assertEqual(explanation.reasons[0].type, "session_intent")

    def test_short_style_returns_single_reason(self):
        track = {
            "song_id": "s1",
            "title": "Live Forever",
            "artist": "Oasis",
            "genres": ["britpop"],
            "tags": ["melodic"],
            "discovery_reason": "anthemic",
            "release_year": 1994,
        }

        explanation = self.generator.explain_track(
            track, user_memory=self.memory, style="short"
        )

        self.assertEqual(len(explanation.reasons), 1)

    def test_positive_feedback_adjustment_is_evidence(self):
        track = {
            "song_id": "s1",
            "title": "Song",
            "artist": "Artist",
            "score_breakdown": {"feedback_adjustment": 0.09},
        }

        explanation = self.generator.explain_track(track, user_memory={})

        types = {item.type for item in explanation.evidence}
        self.assertIn("feedback", types)

    def test_explain_all_handles_multiple_tracks(self):
        tracks = [
            {"song_id": "s1", "title": "A", "artist": "Oasis", "genres": ["britpop"]},
            {"song_id": "s2", "title": "B", "artist": "Blur", "tags": ["melodic"]},
        ]

        explanations = self.generator.explain_all(tracks, user_memory=self.memory)

        self.assertEqual(len(explanations), 2)
        self.assertEqual(explanations[0].song_id, "s1")


if __name__ == "__main__":
    unittest.main()

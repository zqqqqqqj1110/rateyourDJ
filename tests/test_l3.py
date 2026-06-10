import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l3 import (
    CandidateRetrievalService,
    retrieve_candidates,
    score_song_pair,
    weighted_jaccard,
)


def make_song(
    song_id: str,
    *,
    title: str,
    artist: str,
    year: int,
    track_tags: dict[str, float],
    artist_tags: dict[str, float] | None = None,
    genres: dict[str, float] | None = None,
    spotify_id: str | None = None,
    duration_ms: int = 200_000,
) -> SongProfile:
    song = SongProfile.empty(song_id)
    song.external_ids["spotify_track_id"] = spotify_id
    song.metadata.update(
        {
            "title": title,
            "artist": artist,
            "release_year": year,
            "duration_ms": duration_ms,
            "version_type": "original",
        }
    )
    song.source_tags["lastfm_track_tags"] = track_tags
    song.source_tags["lastfm_artist_tags"] = artist_tags or {}
    song.genres = genres or {}
    return song


class SimilarityTests(unittest.TestCase):
    def test_weighted_jaccard_uses_tag_confidence(self) -> None:
        score = weighted_jaccard(
            {"rock": 1.0, "britpop": 0.5},
            {"rock": 0.5, "pop": 0.5},
        )
        self.assertAlmostEqual(score, 0.5 / 2.0)

    def test_pair_score_matches_documented_weights(self) -> None:
        seed = make_song(
            "seed",
            title="Seed",
            artist="Artist A",
            year=1995,
            track_tags={"rock": 1.0},
            artist_tags={"british": 1.0},
            genres={"rock": 1.0},
        )
        candidate = make_song(
            "candidate",
            title="Candidate",
            artist="Artist B",
            year=1995,
            track_tags={"rock": 1.0},
            artist_tags={"british": 1.0},
            genres={"rock": 1.0},
        )

        score, breakdown = score_song_pair(seed, candidate)

        self.assertEqual(score, 1.0)
        self.assertEqual(
            breakdown,
            {
                "track_tags": 0.55,
                "genres": 0.25,
                "artist_tags": 0.15,
                "release_year": 0.05,
            },
        )


class RetrievalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_dir = root / "profiles"
        self.song_dir = root / "songs"
        self.profile_store = JsonProfileStore(self.profile_dir)
        self.song_store = JsonSongStore(self.song_dir)
        self.service = CandidateRetrievalService(
            self.profile_store, self.song_store
        )

        seed_one = make_song(
            "seed-1",
            title="Wonderwall",
            artist="Oasis",
            year=1995,
            track_tags={"britpop": 1.0, "rock": 0.8},
            artist_tags={"british": 1.0},
            genres={"rock": 1.0},
            spotify_id="seed-spotify",
        )
        seed_two = make_song(
            "seed-2",
            title="Song 2",
            artist="Artist Two",
            year=1996,
            track_tags={"britpop": 0.8},
            artist_tags={"british": 0.5},
            genres={"rock": 0.8},
        )
        for song in (seed_one, seed_two):
            self.song_store.save(song)

        profile = UserProfile(
            user_id="user-1",
            collection_song_ids=["seed-1", "seed-2", "missing-seed"],
        )
        self.profile_store.save(profile)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_retrieves_filters_and_merges_seed_matches(self) -> None:
        candidates = (
            make_song(
                "best",
                title="Best Match",
                artist="Blur",
                year=1995,
                track_tags={"britpop": 1.0, "rock": 0.7},
                artist_tags={"british": 1.0},
                genres={"rock": 1.0},
            ),
            make_song(
                "same-external-id",
                title="Other",
                artist="Other Artist",
                year=1995,
                track_tags={"britpop": 1.0},
                spotify_id="seed-spotify",
            ),
            make_song(
                "duplicate-version",
                title="Wonderwall (Remastered)",
                artist="Oasis",
                year=1995,
                track_tags={"britpop": 1.0},
                duration_ms=205_000,
            ),
            make_song(
                "other",
                title="Other Match",
                artist="Pulp",
                year=1994,
                track_tags={"britpop": 0.7},
            ),
        )
        for song in candidates:
            self.song_store.save(song)

        result = self.service.retrieve("user-1", top_k=10)
        result_ids = [
            candidate.candidate_song_id for candidate in result.candidates
        ]

        self.assertEqual(result.missing_seed_song_ids, ["missing-seed"])
        self.assertEqual(result_ids, ["best", "other"])
        self.assertEqual(
            result.candidates[0].matched_seed_song_ids,
            ["seed-1", "seed-2"],
        )
        self.assertEqual(result.candidates[0].best_seed_song_id, "seed-1")
        self.assertEqual(
            result.candidates[0].to_dict()["best_seed_song_id"], "seed-1"
        )
        seed_one = self.song_store.load("seed-1")
        seed_two = self.song_store.load("seed-2")
        best_candidate = self.song_store.load("best")
        seed_one_score, _ = score_song_pair(seed_one, best_candidate)
        seed_two_score, _ = score_song_pair(seed_two, best_candidate)
        expected_average = (seed_one_score + seed_two_score) / 2
        expected_collection_score = (
            0.7 * max(seed_one_score, seed_two_score)
            + 0.3 * expected_average
        )

        self.assertEqual(
            result.candidates[0].best_seed_score,
            max(seed_one_score, seed_two_score),
        )
        self.assertAlmostEqual(
            result.candidates[0].top_seed_average_score,
            expected_average,
            places=6,
        )
        self.assertAlmostEqual(
            result.candidates[0].similarity_score,
            expected_collection_score,
            places=5,
        )
        self.assertAlmostEqual(
            result.candidates[0].similarity_score,
            sum(result.candidates[0].score_breakdown.values()),
            places=6,
        )
        self.assertLess(
            result.candidates[0].similarity_score,
            result.candidates[0].best_seed_score,
        )
        self.assertNotIn("seed_song_ids", result.candidates[0].to_dict())
        self.assertNotIn("same-external-id", result_ids)
        self.assertNotIn("duplicate-version", result_ids)

    def test_applies_artist_limit_and_functional_api(self) -> None:
        for song_id, score in (("blur-1", 1.0), ("blur-2", 0.9)):
            self.song_store.save(
                make_song(
                    song_id,
                    title=song_id,
                    artist="Blur",
                    year=1995,
                    track_tags={"britpop": score},
                )
            )

        result = retrieve_candidates(
            "user-1",
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
            max_per_artist=1,
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertIn(
            result.candidates[0].candidate_song_id, {"blur-1", "blur-2"}
        )


class L3CliTests(unittest.TestCase):
    def test_schema_command_prints_score_breakdown(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "rateyourdj.l3.cli", "schema"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertIn("candidate_song_id", output)
        self.assertIn("track_tags", output["score_breakdown"])


if __name__ == "__main__":
    unittest.main()

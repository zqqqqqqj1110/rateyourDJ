import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l3 import (
    RetrievalCandidate,
    RetrievalResult,
)
from rateyourdj.l4 import (
    RecommendationRankingService,
    rank_candidates,
    ranking_schema,
    score_candidate,
)


def make_song(
    song_id: str,
    *,
    title: str,
    artist: str,
    tags: dict[str, float] | None = None,
    genres: dict[str, float] | None = None,
    confidence: float = 1.0,
) -> SongProfile:
    song = SongProfile.empty(song_id)
    song.metadata.update(
        {
            "title": title,
            "artist": artist,
            "release_year": 2000,
            "duration_ms": 200_000,
            "version_type": "original",
        }
    )
    song.source_tags["lastfm_track_tags"] = tags or {}
    song.genres = genres or {}
    song.confidence_score = confidence
    return song


def make_candidate(song_id: str, score: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        candidate_song_id=song_id,
        best_seed_song_id="seed",
        matched_seed_song_ids=["seed"],
        best_seed_score=score,
        top_seed_average_score=score,
        similarity_score=score,
        score_breakdown={
            "track_tags": score,
            "genres": 0.0,
            "artist_tags": 0.0,
            "release_year": 0.0,
        },
    )


class FakeRetrievalService:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self.candidates = candidates

    def retrieve(self, user_id: str, **_: object) -> RetrievalResult:
        return RetrievalResult(
            user_id=user_id,
            seed_song_ids=["seed"],
            missing_seed_song_ids=[],
            candidates=list(self.candidates),
        )


class RecordingRetrievalService(FakeRetrievalService):
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        super().__init__(candidates)
        self.max_per_artist: int | None = None

    def retrieve(self, user_id: str, **kwargs: object) -> RetrievalResult:
        self.max_per_artist = int(kwargs["max_per_artist"])
        return super().retrieve(user_id, **kwargs)


class L4ScoringTests(unittest.TestCase):
    def test_score_breakdown_uses_documented_weights(self) -> None:
        profile = UserProfile(
            user_id="user-1",
            artist_preferences={"Preferred Artist": 1.0},
            genre_preferences={"rock": 1.0},
            tag_preferences={"energetic": 1.0},
        )
        song = make_song(
            "candidate",
            title="Candidate",
            artist="Preferred Artist",
            tags={"energetic": 1.0},
            genres={"rock": 1.0},
        )

        score, breakdown, raw_scores = score_candidate(
            profile,
            song,
            make_candidate("candidate", 1.0),
        )

        self.assertEqual(score, 1.0)
        self.assertEqual(
            breakdown,
            {
                "retrieval": 0.5,
                "artist_preference": 0.08,
                "genre_preference": 0.14,
                "tag_preference": 0.18,
                "quality": 0.1,
                "feedback_adjustment": 0.0,
            },
        )
        self.assertEqual(raw_scores["feedback"], 0.0)
        self.assertTrue(
            all(
                raw_scores[name] == 1.0
                for name in (
                    "retrieval",
                    "artist_preference",
                    "genre_preference",
                    "tag_preference",
                    "quality",
                )
            )
        )


class L4RankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_dir = root / "profiles"
        self.song_dir = root / "songs"
        self.profile_store = JsonProfileStore(self.profile_dir)
        self.song_store = JsonSongStore(self.song_dir)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _service(
        self,
        candidates: list[RetrievalCandidate],
    ) -> RecommendationRankingService:
        return RecommendationRankingService(
            self.profile_store,
            self.song_store,
            FakeRetrievalService(candidates),
        )

    def test_preferences_can_rerank_l3_candidates(self) -> None:
        self.profile_store.save(
            UserProfile(
                user_id="user-1",
                tag_preferences={"rock": 1.0},
            )
        )
        self.song_store.save(
            make_song(
                "generic",
                title="Generic",
                artist="Artist A",
                tags={"pop": 1.0},
            )
        )
        self.song_store.save(
            make_song(
                "preferred",
                title="Preferred",
                artist="Artist B",
                tags={"rock": 1.0},
            )
        )

        result = self._service(
            [
                make_candidate("generic", 0.8),
                make_candidate("preferred", 0.5),
            ]
        ).rank("user-1", top_k=2, candidate_pool_size=2)

        self.assertEqual(result.ranked_songs[0].song_id, "preferred")
        self.assertIn(
            "matches the collection tag profile",
            result.ranked_songs[0].ranking_reasons,
        )

    def test_feedback_can_rerank_candidates(self) -> None:
        self.profile_store.save(
            UserProfile(
                user_id="user-1",
                feedback_memory=[
                    {
                        "feedback_type": "dislike",
                        "song_id": "higher-retrieval",
                        "timestamp": "2026-06-11T00:00:00+00:00",
                        "reward_score": -1.0,
                        "recommendation_context": {},
                    }
                ],
            )
        )
        for song_id in ("higher-retrieval", "lower-retrieval"):
            self.song_store.save(
                make_song(
                    song_id,
                    title=song_id,
                    artist=song_id,
                )
            )

        result = self._service(
            [
                make_candidate("higher-retrieval", 0.8),
                make_candidate("lower-retrieval", 0.6),
            ]
        ).rank("user-1", top_k=2, candidate_pool_size=2)

        self.assertEqual(
            [song.song_id for song in result.ranked_songs],
            ["lower-retrieval", "higher-retrieval"],
        )
        disliked = result.ranked_songs[1]
        self.assertEqual(disliked.score_breakdown["feedback_adjustment"], -0.15)
        self.assertIn(
            "penalized by negative feedback",
            disliked.ranking_reasons,
        )

    def test_positive_feedback_keeps_final_score_bounded(self) -> None:
        self.profile_store.save(
            UserProfile(
                user_id="user-1",
                artist_preferences={"Artist": 1.0},
                genre_preferences={"rock": 1.0},
                tag_preferences={"rock": 1.0},
                feedback_memory=[
                    {
                        "feedback_type": "playlist_add",
                        "song_id": "candidate",
                        "timestamp": "2026-06-11T00:00:00+00:00",
                        "reward_score": 1.0,
                        "recommendation_context": {},
                    }
                ],
            )
        )
        self.song_store.save(
            make_song(
                "candidate",
                title="Candidate",
                artist="Artist",
                tags={"rock": 1.0},
                genres={"rock": 1.0},
            )
        )

        result = self._service(
            [make_candidate("candidate", 1.0)]
        ).rank("user-1", top_k=1, candidate_pool_size=1)

        self.assertEqual(result.ranked_songs[0].base_score, 1.15)
        self.assertEqual(result.ranked_songs[0].final_score, 1.0)

    def test_diversity_penalty_promotes_a_distinct_second_song(self) -> None:
        self.profile_store.save(UserProfile(user_id="user-1"))
        for song in (
            make_song(
                "similar-a",
                title="Similar A",
                artist="Artist A",
                tags={"rock": 1.0},
                genres={"rock": 1.0},
            ),
            make_song(
                "similar-b",
                title="Similar B",
                artist="Artist B",
                tags={"rock": 1.0},
                genres={"rock": 1.0},
            ),
            make_song(
                "different",
                title="Different",
                artist="Artist C",
                tags={"jazz": 1.0},
                genres={"jazz": 1.0},
            ),
        ):
            self.song_store.save(song)

        result = self._service(
            [
                make_candidate("similar-a", 0.9),
                make_candidate("similar-b", 0.89),
                make_candidate("different", 0.75),
            ]
        ).rank("user-1", top_k=3, candidate_pool_size=3)

        self.assertEqual(
            [song.song_id for song in result.ranked_songs],
            ["similar-a", "different", "similar-b"],
        )
        self.assertGreater(result.ranked_songs[2].diversity_penalty, 0)

    def test_missing_candidate_is_reported_and_functional_api_works(self) -> None:
        self.profile_store.save(UserProfile(user_id="user-1"))
        self.song_store.save(
            make_song(
                "available",
                title="Available",
                artist="Artist",
            )
        )
        result = self._service(
            [
                make_candidate("missing", 0.9),
                make_candidate("available", 0.8),
            ]
        ).rank("user-1", top_k=1, candidate_pool_size=2)

        self.assertEqual(result.missing_candidate_song_ids, ["missing"])
        self.assertEqual(result.ranked_songs[0].song_id, "available")

        functional_result = rank_candidates(
            "user-1",
            profile_dir=self.profile_dir,
            song_dir=self.song_dir,
            top_k=1,
        )
        self.assertEqual(functional_result.ranked_songs, [])

    def test_applies_artist_limit(self) -> None:
        self.profile_store.save(UserProfile(user_id="user-1"))
        for song_id in ("artist-a-1", "artist-a-2", "artist-b-1"):
            artist = "Artist A" if song_id.startswith("artist-a") else "Artist B"
            self.song_store.save(
                make_song(
                    song_id,
                    title=song_id,
                    artist=artist,
                )
            )

        result = self._service(
            [
                make_candidate("artist-a-1", 0.9),
                make_candidate("artist-a-2", 0.8),
                make_candidate("artist-b-1", 0.7),
            ]
        ).rank(
            "user-1",
            top_k=3,
            candidate_pool_size=3,
            max_per_artist=1,
        )

        self.assertEqual(
            [song.song_id for song in result.ranked_songs],
            ["artist-a-1", "artist-b-1"],
        )

    def test_validates_ranking_limits(self) -> None:
        self.profile_store.save(UserProfile(user_id="user-1"))
        service = self._service([])

        with self.assertRaises(ValueError):
            service.rank("user-1", top_k=0)
        with self.assertRaises(ValueError):
            service.rank("user-1", top_k=2, candidate_pool_size=1)

    def test_passes_artist_limit_to_candidate_pool_retrieval(self) -> None:
        self.profile_store.save(UserProfile(user_id="user-1"))
        retrieval = RecordingRetrievalService([])
        service = RecommendationRankingService(
            self.profile_store,
            self.song_store,
            retrieval,
        )

        service.rank(
            "user-1",
            top_k=2,
            candidate_pool_size=10,
            max_per_artist=2,
        )

        self.assertEqual(retrieval.max_per_artist, 2)


class L4CliTests(unittest.TestCase):
    def test_schema_command_prints_ranking_framework(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "rateyourdj.l4.cli", "schema"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertEqual(output, ranking_schema())
        self.assertIn("retrieval", output["score_breakdown"])
        self.assertIn("diversity_penalty", output)


if __name__ == "__main__":
    unittest.main()

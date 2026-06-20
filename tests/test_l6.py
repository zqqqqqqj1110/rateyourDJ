import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from rateyourdj.agent_tools import ToolObservation
from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l4 import RecommendationRankingService
from rateyourdj.l6 import (
    AGENT_TOOL_SCHEMA_VERSION,
    AgentDecision,
    AgentSession,
    AgentTrajectory,
    AgentToolRegistryV1,
    AgentToolRegistry,
    JsonSessionStore,
    JsonTrajectoryStore,
    LLMProviderError,
    LLMResponseError,
    LOOP_CONTRACT_VERSION,
    MockLLMProvider,
    RecommendationAgentService,
    TRAJECTORY_SCHEMA_VERSION,
    agent_schema,
    parse_agent_request,
    recommendation_loop_plan,
)
from rateyourdj.l6.session_ranking import (
    SESSION_MEMORY_RANKING_FIELDS,
    build_session_ranking_context,
)
from rateyourdj.providers import (
    ExternalMusicProvider,
    ProviderSearchResult,
    ProviderTrack,
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
    song.confidence_score = 1.0
    return song


class AgentParserTests(unittest.TestCase):
    def test_parses_count_diversity_preferences_and_exclusions(self) -> None:
        request = parse_agent_request(
            "推荐五首多样一点的摇滚，不要“Artist B”"
        )

        self.assertEqual(request.top_k, 5)
        self.assertEqual(request.max_per_artist, 1)
        self.assertEqual(request.preference_terms, ["rock"])
        self.assertEqual(request.exclude_terms, ["artist b"])

    def test_parses_unquoted_compact_exclusion(self) -> None:
        request = parse_agent_request("不要pinkfloyd")

        self.assertEqual(request.exclude_terms, ["pinkfloyd"])

    def test_diversity_phrase_is_not_an_artist_exclusion(self) -> None:
        request = parse_agent_request("推荐五首摇滚，不要重复歌手")

        self.assertEqual(request.max_per_artist, 1)
        self.assertEqual(request.exclude_terms, [])

    def test_resolves_referenced_artist_exclusion(self) -> None:
        request = parse_agent_request(
            "有没有和pink floyd差不多的，但是不要这个乐队"
        )

        self.assertEqual(request.exclude_terms, ["pink floyd"])
        self.assertEqual(request.min_retrieval_score, 0.1)

    def test_parses_british_rock_with_negative_artist_phrase(self) -> None:
        request = parse_agent_request(
            "有没有英伦摇滚，但不是 Pink Floyd 的歌"
        )

        self.assertEqual(request.preference_terms, ["british", "rock"])
        self.assertEqual(request.exclude_terms, ["pink floyd"])

    def test_parses_reference_and_avoid_artists_from_similarity_query(self) -> None:
        request = parse_agent_request(
            "不要 Sex Pistols 这种，给我更像 Oasis / Blur 的"
        )

        self.assertEqual(request.reference_artists, ["oasis", "blur"])
        self.assertEqual(request.avoid_artists, ["sex pistols"])

    def test_parses_british_indie_rock(self) -> None:
        request = parse_agent_request(
            "推荐 5 首 2020 年之后的英伦独立摇滚"
        )

        self.assertEqual(request.top_k, 5)
        self.assertIn("british indie rock", request.preference_terms)
        self.assertIn("indie rock", request.preference_terms)
        self.assertIn("rock", request.preference_terms)

    def test_seen_song_reference_is_not_a_text_exclusion(self) -> None:
        request = parse_agent_request("换一批，不要刚才推荐过的歌曲")

        self.assertEqual(request.intent, "more")
        self.assertTrue(request.exclude_seen)

    def test_similarity_refinement_query_is_treated_as_more(self) -> None:
        request = parse_agent_request("还是不够像 Oasis，我想要更旋律一点的英伦摇滚")

        self.assertEqual(request.intent, "more")
        self.assertEqual(request.exclude_terms, [])

    def test_rejects_empty_query(self) -> None:
        with self.assertRaises(ValueError):
            parse_agent_request(" ")


class RecommendationLoopContractTests(unittest.TestCase):
    def test_plan_exposes_stable_v1_phases(self) -> None:
        plan = recommendation_loop_plan()

        self.assertEqual(
            [item["phase"] for item in plan],
            [
                "memory_read",
                "query_understanding",
                "external_search",
                "candidate_enrichment",
                "candidate_ranking",
                "retrieval_diagnostics",
                "explanation",
                "trajectory_write",
                "feedback_write",
            ],
        )
        self.assertIn("get_user_memory", plan[0]["allowed_tools"])
        self.assertIn("search_tracks", plan[2]["allowed_tools"])
        self.assertIn("rank_candidates", plan[4]["allowed_tools"])

    def test_session_ranking_context_exposes_only_explicit_session_fields(self) -> None:
        session = AgentSession(
            schema_version=1,
            session_id="session-ranking-test",
            user_id="user-1",
            preference_terms=["rock"],
            exclude_terms=["artist b"],
            seen_track_ids=["rock-a"],
            seed_track_ids=["seed"],
            active_constraints={
                "exclude_seen": True,
                "limit": 1,
                "max_per_artist": 1,
            },
            temporary_feedback=[
                {"track_id": "rock-b", "event": "skipped"},
                {"track_id": "seed", "event": "liked"},
            ],
        )
        request = parse_agent_request("推荐一首摇滚")

        context = build_session_ranking_context(
            session,
            request,
        )

        self.assertEqual(
            context.ranking_fields,
            SESSION_MEMORY_RANKING_FIELDS,
        )
        self.assertEqual(context.preference_terms, ["rock"])
        self.assertEqual(context.exclude_terms, ["artist b"])
        self.assertEqual(context.seed_track_ids, ["seed"])
        self.assertEqual(context.excluded_song_ids, {"rock-a", "rock-b"})
        self.assertTrue(context.active_constraints["exclude_seen"])


class RecommendationAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_store = JsonProfileStore(root / "profiles")
        self.song_store = JsonSongStore(root / "songs")
        self.trajectory_store = JsonTrajectoryStore(root / "trajectories")
        self.profile_store.save(
            UserProfile(
                user_id="user-1",
                collection_song_ids=["seed"],
                genre_preferences={"rock": 1.0},
                tag_preferences={"rock": 1.0, "britpop": 0.8},
            )
        )
        self.song_store.save(
            make_song("seed", artist="Seed Artist", genre="rock", tag="rock")
        )
        self.song_store.save(
            make_song("rock-a", artist="Artist A", genre="rock", tag="rock")
        )
        self.song_store.save(
            make_song("rock-b", artist="Artist B", genre="rock", tag="rock")
        )
        self.song_store.save(
            make_song("jazz", artist="Artist C", genre="jazz", tag="jazz")
        )
        ranking_service = RecommendationRankingService(
            self.profile_store,
            self.song_store,
        )
        self.service = RecommendationAgentService(
            ranking_service,
            self.song_store,
            self.trajectory_store,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_runs_l4_filters_results_and_saves_trajectory(self) -> None:
        response = self.service.recommend(
            "user-1",
            "推荐两首摇滚，不要“Artist B”",
        )

        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["rock-a"],
        )
        trajectory = self.trajectory_store.load(
            "user-1",
            response.trajectory_id,
        )
        self.assertEqual(trajectory.query, response.query)
        self.assertEqual(
            [item["phase"] for item in trajectory.plan[:2]],
            ["memory_read", "query_understanding"],
        )
        plan_by_phase = {
            item["phase"]: item for item in trajectory.plan
        }
        self.assertEqual(plan_by_phase["memory_read"]["status"], "completed")
        self.assertEqual(
            plan_by_phase["query_understanding"]["status"],
            "completed",
        )
        self.assertEqual(
            plan_by_phase["candidate_ranking"]["status"],
            "completed",
        )
        self.assertEqual(plan_by_phase["explanation"]["status"], "completed")
        self.assertEqual(
            plan_by_phase["trajectory_write"]["status"],
            "completed",
        )
        self.assertEqual(plan_by_phase["feedback_write"]["status"], "pending")
        self.assertEqual(
            plan_by_phase["candidate_ranking"]["session_memory_reads"],
            list(SESSION_MEMORY_RANKING_FIELDS),
        )
        self.assertIn(
            "seen_track_ids",
            plan_by_phase["trajectory_write"]["session_memory_writes"],
        )
        self.assertEqual(
            [call["tool"] for call in trajectory.tool_calls[:2]],
            ["L1.inspect_user_profile", "L4.rank_candidates"],
        )
        self.assertEqual(
            [call["loop_phase"] for call in trajectory.tool_calls[:2]],
            ["memory_read", "candidate_ranking"],
        )
        self.assertTrue(
            all(
                call["loop_contract"] == LOOP_CONTRACT_VERSION
                for call in trajectory.tool_calls
            )
        )
        self.assertEqual(
            trajectory.trajectory_schema_version,
            TRAJECTORY_SCHEMA_VERSION,
        )
        self.assertEqual(
            trajectory.loop_contract_version,
            LOOP_CONTRACT_VERSION,
        )
        self.assertEqual(
            trajectory.tool_schema_version,
            AGENT_TOOL_SCHEMA_VERSION,
        )
        self.assertEqual(
            trajectory.user_memory_snapshot["collection_song_ids"],
            ["seed"],
        )
        self.assertEqual(
            trajectory.session_memory_snapshot["last_user_query"],
            response.query,
        )
        self.assertEqual(
            trajectory.retrieval_snapshot["seed_song_ids"],
            ["seed"],
        )
        self.assertEqual(trajectory.stop_reason, "insufficient_candidates")
        self.assertTrue(trajectory.plan)
        self.assertEqual(
            trajectory.recommendations[0]["song_id"],
            "rock-a",
        )
        self.assertEqual(
            trajectory.ranked_candidates[0]["song_id"],
            "rock-a",
        )
        self.assertEqual(
            trajectory.final_recommendations[0]["song_id"],
            "rock-a",
        )

    def test_unquoted_compact_artist_name_is_excluded(self) -> None:
        response = self.service.recommend("user-1", "不要artistb")

        self.assertNotIn(
            "rock-b",
            [song["song_id"] for song in response.ranked_songs],
        )

    def test_trajectory_from_dict_migrates_old_payload_to_v1_contract(self) -> None:
        legacy_payload = {
            "trajectory_id": "trajectory-1",
            "session_id": "session-1",
            "turn_index": 1,
            "user_id": "user-1",
            "query": "推荐一首摇滚",
            "parsed_request": {"top_k": 1},
            "plan": [],
            "tool_calls": [],
            "recommendations": [{"song_id": "rock-a"}],
            "response_text": "完成推荐",
            "feedback_events": [],
            "stop_reason": "goal_satisfied",
            "agent_mode": "rules",
            "provider": None,
            "fallback_reason": None,
            "agent_decisions": [],
            "created_at": "2026-06-19T00:00:00+00:00",
        }

        trajectory = AgentTrajectory.from_dict(legacy_payload)

        self.assertEqual(
            trajectory.trajectory_schema_version,
            TRAJECTORY_SCHEMA_VERSION,
        )
        self.assertEqual(trajectory.user_memory_snapshot, {})
        self.assertEqual(trajectory.session_memory_snapshot, {})
        self.assertEqual(trajectory.retrieval_snapshot, {})
        self.assertEqual(
            trajectory.ranked_candidates,
            [{"song_id": "rock-a"}],
        )
        self.assertEqual(
            trajectory.final_recommendations,
            [{"song_id": "rock-a"}],
        )
        self.assertEqual(trajectory.feedback_contexts, [])
        self.assertEqual(trajectory.collection_writes, [])

    def test_mock_model_drives_tools_and_program_enforces_exclusion(self) -> None:
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="tool",
                    tool_name="L1.inspect_user_profile",
                    arguments={},
                    summary="inspect profile before ranking",
                    request_patch={
                        "top_k": 1,
                        "exclude_terms": ["artistb"],
                    },
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="rank a candidate pool",
                ),
                AgentDecision(
                    kind="finish",
                    summary="eligible recommendation is available",
                    response_text="模型路径完成推荐。",
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "来点合适的音乐")

        self.assertEqual(response.agent_mode, "model")
        self.assertEqual(response.provider, "mock")
        self.assertIsNone(response.fallback_reason)
        self.assertEqual(response.parsed_request.exclude_terms, ["artistb"])
        self.assertEqual(response.ranked_songs[0]["song_id"], "rock-a")
        self.assertEqual(
            [call["tool"] for call in response.tool_calls],
            ["L1.inspect_user_profile", "L4.rank_candidates"],
        )
        self.assertTrue(
            all(
                call["decision_source"] == "model"
                for call in response.tool_calls
            )
        )
        trajectory = self.trajectory_store.load(
            "user-1",
            response.trajectory_id,
        )
        self.assertEqual(trajectory.agent_mode, "model")
        self.assertEqual(len(trajectory.agent_decisions), 3)

    def test_model_can_recommend_tracks_from_external_provider_search(self) -> None:
        class SearchProvider:
            queries = []

            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                self.queries.append(query)
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:4uLU6hMCjMI75M1A2tKUQC",
                            provider="spotify",
                            title="Live Forever",
                            artist="Oasis",
                            album="Definitely Maybe",
                            tags={"britpop": 1.0, "british": 1.0, "rock": 1.0},
                            external_urls={
                                "spotify": (
                                    "https://open.spotify.com/track/"
                                    "4uLU6hMCjMI75M1A2tKUQC"
                                )
                            },
                        )
                    ],
                )

        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="tool",
                    tool_name="search_tracks",
                    arguments={"query": "britpop rock", "limit": 3},
                    summary="search external music provider",
                ),
            ]
        )
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[SearchProvider()]
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "推荐一首 britpop rock")

        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(
            response.ranked_songs[0]["song_id"],
            "spotify:track:4uLU6hMCjMI75M1A2tKUQC",
        )
        self.assertEqual(response.ranked_songs[0]["title"], "Live Forever")
        self.assertEqual(
            response.ranked_songs[0]["retrieval_sources"],
            ["spotify_search"],
        )
        self.assertEqual(
            response.ranked_songs[0]["spotify_track_id"],
            "4uLU6hMCjMI75M1A2tKUQC",
        )
        self.assertEqual(
            [call["tool"] for call in response.tool_calls],
            ["get_user_memory", "search_tracks", "search_tracks"],
        )
        self.assertGreater(
            response.ranked_songs[0]["score_breakdown"]["profile_match"],
            0,
        )
        self.assertEqual(
            response.tool_calls[1]["decision_source"],
            "program_provider_first",
        )

    def test_external_provider_search_preserves_year_and_indie_intent(self) -> None:
        class SearchProvider:
            queries = []

            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                self.queries.append(query)
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:old",
                            provider="spotify",
                            title="Old Indie",
                            artist="Band A",
                            album="Old Album",
                            release_year=2019,
                            tags={"indie": 1.0, "rock": 1.0},
                        ),
                        ProviderTrack(
                            track_id="spotify:track:new",
                            provider="spotify",
                            title="New Indie",
                            artist="Band B",
                            album="New Album",
                            release_year=2021,
                            tags={"indie": 1.0, "rock": 1.0},
                        ),
                    ],
                )

        search_provider = SearchProvider()
        provider = MockLLMProvider([])
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[search_provider]
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "推荐 1 首 2020 年之后的英伦独立摇滚",
        )

        self.assertEqual(
            search_provider.queries,
            ["2020 britpop british indie rock"],
        )
        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["spotify:track:new"],
        )
        trajectory = self.trajectory_store.load("user-1", response.trajectory_id)
        plan_by_phase = {
            item["phase"]: item for item in trajectory.plan
        }
        self.assertEqual(plan_by_phase["external_search"]["status"], "completed")
        self.assertEqual(
            plan_by_phase["candidate_ranking"]["status"],
            "skipped",
        )

    def test_external_provider_search_does_not_require_local_tags(self) -> None:
        class SearchProvider:
            queries = []

            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                self.queries.append(query)
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:wonderwall",
                            provider="spotify",
                            title="Wonderwall",
                            artist="Oasis",
                            album="(What's The Story) Morning Glory?",
                            release_year=1995,
                        )
                    ],
                )

        search_provider = SearchProvider()
        provider = MockLLMProvider([])
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[search_provider]
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "给我1首经典的英伦摇滚")

        self.assertEqual(
            search_provider.queries,
            ["britpop british rock"],
        )
        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["spotify:track:wonderwall"],
        )
        self.assertIn(
            "外部搜索结果",
            response.message,
        )

    def test_external_provider_search_uses_reference_artists_and_refinement_terms(
        self,
    ) -> None:
        class SearchProvider:
            queries = []

            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                self.queries.append(query)
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:wonderwall",
                            provider="spotify",
                            title="Wonderwall",
                            artist="Oasis",
                            album="(What's The Story) Morning Glory?",
                            release_year=1995,
                        )
                    ],
                )

        class SimilarArtistsProvider:
            @property
            def provider_name(self) -> str:
                return "fake-lastfm"

            def get_similar_artists(self, artist: str, *, limit=10):
                from rateyourdj.providers import (
                    ProviderSimilarArtist,
                    ProviderSimilarArtistsResult,
                )

                return ProviderSimilarArtistsResult(
                    provider="fake-lastfm",
                    artist=artist,
                    artists=[
                        ProviderSimilarArtist(
                            name="Pulp",
                            provider="fake-lastfm",
                            score=0.92,
                        ),
                        ProviderSimilarArtist(
                            name="Suede",
                            provider="fake-lastfm",
                            score=0.88,
                        ),
                    ][:limit],
                )

        search_provider = SearchProvider()
        provider = MockLLMProvider([])
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[search_provider],
                similar_artists_provider=SimilarArtistsProvider(),
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "给我 1 首还是不够像 Oasis，我想要更旋律一点的英伦摇滚",
        )

        self.assertEqual(response.stop_reason, "insufficient_candidates")
        self.assertGreaterEqual(len(search_provider.queries), 3)
        self.assertTrue(
            any("oasis" in query.casefold() for query in search_provider.queries)
        )
        self.assertTrue(
            any("pulp" in query.casefold() for query in search_provider.queries)
        )
        self.assertTrue(
            any("suede" in query.casefold() for query in search_provider.queries)
        )
        self.assertTrue(
            all(len(query) <= 180 for query in search_provider.queries)
        )

    def test_external_provider_failure_is_distinct_from_true_empty_results(
        self,
    ) -> None:
        class FailingSearchProvider:
            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                raise RuntimeError("spotify token endpoint unavailable")

        provider = MockLLMProvider([])
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[FailingSearchProvider()]
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "来点英伦摇滚，最好是oasis这样的")

        self.assertEqual(response.stop_reason, "external_search_failed")
        self.assertEqual(response.ranked_songs, [])
        self.assertIn("外部音乐搜索执行失败", response.message)
        self.assertEqual(
            [call["tool"] for call in response.tool_calls],
            ["get_user_memory", "search_tracks"],
        )

    def test_external_provider_search_excludes_negative_artist_tokens(self) -> None:
        class SearchProvider:
            queries = []

            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                self.queries.append(query)
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:song2",
                            provider="spotify",
                            title="Song 2",
                            artist="Blur",
                            album="Blur",
                            release_year=1997,
                        )
                    ],
                )

        class SimilarArtistsProvider:
            @property
            def provider_name(self) -> str:
                return "fake-lastfm"

            def get_similar_artists(self, artist: str, *, limit=10):
                from rateyourdj.providers import (
                    ProviderSimilarArtist,
                    ProviderSimilarArtistsResult,
                )

                if artist.casefold() == "oasis":
                    artists = [
                        ProviderSimilarArtist(
                            name="Pulp",
                            provider="fake-lastfm",
                            score=0.92,
                        ),
                        ProviderSimilarArtist(
                            name="Suede",
                            provider="fake-lastfm",
                            score=0.88,
                        ),
                    ]
                else:
                    artists = [
                        ProviderSimilarArtist(
                            name="Elastica",
                            provider="fake-lastfm",
                            score=0.81,
                        ),
                    ]
                return ProviderSimilarArtistsResult(
                    provider="fake-lastfm",
                    artist=artist,
                    artists=artists[:limit],
                )

        search_provider = SearchProvider()
        provider = MockLLMProvider([])
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[search_provider],
                similar_artists_provider=SimilarArtistsProvider(),
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "给我 1 首不要 Sex Pistols 这种，给我更像 Oasis / Blur 的",
        )

        self.assertGreaterEqual(len(search_provider.queries), 4)
        self.assertTrue(
            any("pulp" in query.casefold() for query in search_provider.queries)
        )
        self.assertTrue(
            any("suede" in query.casefold() for query in search_provider.queries)
        )
        self.assertTrue(
            any("elastica" in query.casefold() for query in search_provider.queries)
        )
        self.assertTrue(
            all(len(query) <= 180 for query in search_provider.queries)
        )

    def test_external_provider_filters_reference_artist_tracks_for_similarity_queries(
        self,
    ) -> None:
        class SearchProvider:
            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:oasis",
                            provider="spotify",
                            title="Don't Look Back In Anger",
                            artist="Oasis",
                            album="(What's The Story) Morning Glory?",
                            release_year=1995,
                            tags={"britpop": 1.0},
                        ),
                        ProviderTrack(
                            track_id="spotify:track:pulp",
                            provider="spotify",
                            title="Common People",
                            artist="Pulp",
                            album="Different Class",
                            release_year=1995,
                            tags={"britpop": 1.0},
                        ),
                    ],
                )

        class SimilarArtistsProvider:
            @property
            def provider_name(self) -> str:
                return "fake-lastfm"

            def get_similar_artists(self, artist: str, *, limit=10):
                from rateyourdj.providers import (
                    ProviderSimilarArtist,
                    ProviderSimilarArtistsResult,
                )

                return ProviderSimilarArtistsResult(
                    provider="fake-lastfm",
                    artist=artist,
                    artists=[
                        ProviderSimilarArtist(
                            name="Pulp",
                            provider="fake-lastfm",
                            score=0.92,
                        )
                    ][:limit],
                )

        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[SearchProvider()],
                similar_artists_provider=SimilarArtistsProvider(),
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=MockLLMProvider([]),
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "给我 1 首更像 Oasis 的英伦摇滚",
        )

        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["spotify:track:pulp"],
        )

    def test_external_provider_filters_irrelevant_low_signal_results(self) -> None:
        class SearchProvider:
            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:relevant",
                            provider="spotify",
                            title="Live Forever",
                            artist="Oasis",
                            album="Definitely Maybe",
                            release_year=1994,
                            tags={"britpop": 1.0, "rock": 1.0},
                        ),
                        ProviderTrack(
                            track_id="spotify:track:irrelevant",
                            provider="spotify",
                            title="H.I.P.-H.O.P.",
                            artist="Jazz Addixx",
                            album="Oxygen",
                            release_year=2005,
                        ),
                    ],
                )

        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[SearchProvider()]
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=MockLLMProvider([]),
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "来点英伦摇滚，最好是oasis这样的",
        )

        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["spotify:track:relevant"],
        )

    def test_external_provider_mode_does_not_fall_back_to_local_ranking(self) -> None:
        class EmptySearchProvider:
            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[],
                )

        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="try local fallback",
                ),
            ]
        )
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[EmptySearchProvider()]
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "推荐一首 rock")

        self.assertEqual(response.stop_reason, "insufficient_candidates")
        self.assertEqual(response.ranked_songs, [])
        self.assertEqual(
            [call["tool"] for call in response.tool_calls],
            ["get_user_memory", "search_tracks"],
        )

    def test_provider_failure_falls_back_to_rules(self) -> None:
        provider = MockLLMProvider(
            [LLMProviderError("temporary provider failure")]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="auto",
        )

        response = service.recommend("user-1", "推荐一首摇滚")

        self.assertEqual(response.agent_mode, "rules")
        self.assertEqual(response.provider, "mock")
        self.assertEqual(
            response.fallback_reason,
            "temporary provider failure",
        )
        self.assertEqual(response.ranked_songs[0]["song_id"], "rock-a")
        self.assertEqual(response.agent_decisions[-1]["kind"], "fallback")

    def test_invalid_model_response_is_retried_before_fallback(self) -> None:
        provider = MockLLMProvider(
            [
                LLMResponseError("invalid control tool arguments"),
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="rank songs after response correction",
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "推荐一首摇滚")

        self.assertEqual(response.agent_mode, "model")
        self.assertIsNone(response.fallback_reason)
        self.assertEqual(response.ranked_songs[0]["song_id"], "rock-a")
        self.assertEqual(
            response.agent_decisions[0]["kind"],
            "provider_response_error",
        )
        self.assertIn(
            "provider response rejected",
            provider.turns[1].validation_feedback[0],
        )

    def test_model_l4_call_is_expanded_before_query_filters(self) -> None:
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="update",
                    summary="add equivalent British aliases",
                    request_patch={
                        "preference_terms": [
                            "britpop",
                            "british rock",
                            "英伦摇滚",
                        ],
                    },
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={
                        "top_k": 5,
                        "candidate_pool_size": 5,
                    },
                    summary="rank songs",
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "推荐 5 首英伦摇滚")

        rank_call = next(
            call
            for call in response.tool_calls
            if call["tool"] == "L4.rank_candidates"
        )
        self.assertEqual(rank_call["arguments"]["top_k"], 25)
        self.assertEqual(
            rank_call["arguments"]["candidate_pool_size"],
            25,
        )
        self.assertEqual(
            response.parsed_request.preference_terms,
            ["british", "rock"],
        )

    def test_model_can_refine_toward_reference_artist_and_exclude_seen(self) -> None:
        class SearchProvider:
            queries = []

            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                self.queries.append(query)
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:wonderwall",
                            provider="spotify",
                            title="Wonderwall",
                            artist="Oasis",
                            album="(What's The Story) Morning Glory?",
                            tags={"british": 1.0, "rock": 1.0},
                        )
                    ],
                )

        class SimilarArtistsProvider:
            @property
            def provider_name(self) -> str:
                return "fake-lastfm"

            def get_similar_artists(self, artist: str, *, limit=10):
                from rateyourdj.providers import (
                    ProviderSimilarArtist,
                    ProviderSimilarArtistsResult,
                )

                return ProviderSimilarArtistsResult(
                    provider="fake-lastfm",
                    artist=artist,
                    artists=[
                        ProviderSimilarArtist(
                            name="Pulp",
                            provider="fake-lastfm",
                            score=0.92,
                        ),
                        ProviderSimilarArtist(
                            name="Suede",
                            provider="fake-lastfm",
                            score=0.88,
                        ),
                    ][:limit],
                )

        search_provider = SearchProvider()
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="update",
                    summary="refine toward Oasis and avoid previous batch",
                    request_patch={
                        "intent": "more",
                        "exclude_seen": True,
                        "reference_artists": ["Oasis"],
                        "avoid_artists": ["Sex Pistols"],
                        "refinement_notes": ["more melodic", "less punk"],
                    },
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="search_tracks",
                    arguments={"query": "oasis british rock", "limit": 5},
                    summary="search for refined candidates",
                ),
            ]
        )
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[search_provider],
                similar_artists_provider=SimilarArtistsProvider(),
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "还是不够像 Oasis，我想要更旋律一点的英伦摇滚",
        )

        self.assertEqual(response.parsed_request.intent, "more")
        self.assertTrue(response.parsed_request.exclude_seen)
        self.assertEqual(
            response.parsed_request.reference_artists,
            ["oasis"],
        )
        self.assertEqual(
            response.parsed_request.avoid_artists,
            ["sex pistols"],
        )
        self.assertEqual(
            response.parsed_request.refinement_notes,
            ["more melodic", "less punk"],
        )
        self.assertGreaterEqual(len(search_provider.queries), 4)
        self.assertTrue(
            any("pulp" in query.casefold() for query in search_provider.queries)
        )
        self.assertTrue(
            any("suede" in query.casefold() for query in search_provider.queries)
        )
        self.assertIn("oasis british rock", search_provider.queries[-1].casefold())
        self.assertEqual(response.ranked_songs, [])

    def test_external_provider_search_splits_long_blur_oasis_expansion_queries(self) -> None:
        class SearchProvider:
            queries = []

            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                self.queries.append(query)
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[],
                )

        class SimilarArtistsProvider:
            @property
            def provider_name(self) -> str:
                return "fake-lastfm"

            def get_similar_artists(self, artist: str, *, limit=10):
                from rateyourdj.providers import (
                    ProviderSimilarArtist,
                    ProviderSimilarArtistsResult,
                )

                names = {
                    "blur": [
                        "Pulp",
                        "Elastica",
                        "Damon Albarn",
                        "The Good, the Bad & the Queen",
                        "Gorillaz",
                        "Suede",
                    ],
                    "oasis": [
                        "Noel Gallagher's High Flying Birds",
                        "Liam Gallagher",
                        "Beady Eye",
                        "The Verve",
                        "Cast",
                        "Ocean Colour Scene",
                    ],
                }
                return ProviderSimilarArtistsResult(
                    provider="fake-lastfm",
                    artist=artist,
                    artists=[
                        ProviderSimilarArtist(
                            name=name,
                            provider="fake-lastfm",
                            score=0.9 - (index * 0.01),
                        )
                        for index, name in enumerate(names[artist.casefold()][:limit])
                    ],
                )

        search_provider = SearchProvider()
        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[search_provider],
                similar_artists_provider=SimilarArtistsProvider(),
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=MockLLMProvider([]),
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "不要 Sex Pistols 这种，给我更像 Blur / Oasis 的英伦摇滚",
        )

        self.assertEqual(response.stop_reason, "insufficient_candidates")
        self.assertGreaterEqual(len(search_provider.queries), 4)
        self.assertTrue(all(len(query) <= 180 for query in search_provider.queries))

    def test_external_provider_family_penalty_spreads_gallagher_results(self) -> None:
        class SearchProvider:
            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:noel-1",
                            provider="spotify",
                            title="Live Forever - Radio Session",
                            artist="Noel Gallagher's High Flying Birds",
                            album="Council Skies",
                            tags={"britpop": 1.0, "melodic": 1.0},
                        ),
                        ProviderTrack(
                            track_id="spotify:track:noel-2",
                            provider="spotify",
                            title="If I Had A Gun…",
                            artist="Noel Gallagher's High Flying Birds",
                            album="Noel Gallagher's High Flying Birds",
                            tags={"britpop": 1.0, "melodic": 1.0},
                        ),
                        ProviderTrack(
                            track_id="spotify:track:verve",
                            provider="spotify",
                            title="Lucky Man",
                            artist="The Verve",
                            album="Urban Hymns",
                            tags={"britpop": 1.0, "melodic": 1.0},
                        ),
                    ],
                )

        class SimilarArtistsProvider:
            @property
            def provider_name(self) -> str:
                return "fake-lastfm"

            def get_similar_artists(self, artist: str, *, limit=10):
                from rateyourdj.providers import (
                    ProviderSimilarArtist,
                    ProviderSimilarArtistsResult,
                )

                return ProviderSimilarArtistsResult(
                    provider="fake-lastfm",
                    artist=artist,
                    artists=[
                        ProviderSimilarArtist(
                            name="Noel Gallagher's High Flying Birds",
                            provider="fake-lastfm",
                            score=0.95,
                        ),
                        ProviderSimilarArtist(
                            name="The Verve",
                            provider="fake-lastfm",
                            score=0.88,
                        ),
                    ],
                )

        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[SearchProvider()],
                similar_artists_provider=SimilarArtistsProvider(),
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=MockLLMProvider([]),
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "来点更像 Oasis 的英伦摇滚，旋律一点，不要太朋克",
        )

        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs[:2]],
            ["spotify:track:noel-1", "spotify:track:verve"],
        )

    def test_reference_match_requires_exact_artist_not_substring(self) -> None:
        class SearchProvider:
            @property
            def provider_name(self) -> str:
                return "fake"

            def search_tracks(self, query, *, limit=10, market=None):
                return ProviderSearchResult(
                    provider="fake",
                    query=query,
                    tracks=[
                        ProviderTrack(
                            track_id="spotify:track:blurred",
                            provider="spotify",
                            title="Blurred Lines",
                            artist="Robin Thicke",
                            album="Blurred Lines",
                            release_year=2013,
                        )
                    ],
                )

        registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=ExternalMusicProvider(
                search_providers=[SearchProvider()]
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            model_tool_registry=registry,
            llm_provider=MockLLMProvider([]),
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "给我 1 首不要 Sex Pistols 这种，给我更像 Blur 的",
        )

        self.assertEqual(response.ranked_songs, [])

    def test_model_cannot_remove_rule_parsed_exclusion(self) -> None:
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="rank songs",
                    request_patch={
                        "top_k": 1,
                        "exclude_terms": [],
                    },
                ),
                AgentDecision(
                    kind="finish",
                    summary="finish with validated songs",
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "不要artistb")

        self.assertEqual(response.agent_mode, "model")
        self.assertEqual(response.parsed_request.exclude_terms, ["artistb"])
        self.assertEqual(response.ranked_songs[0]["song_id"], "rock-a")

    def test_program_rejects_early_finish_until_quantity_is_satisfied(self) -> None:
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="finish",
                    summary="finish before using tools",
                    request_patch={"top_k": 1},
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="rank after validation feedback",
                ),
                AgentDecision(
                    kind="finish",
                    summary="finish after ranking",
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "给我合适的音乐")

        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(len(response.ranked_songs), 1)
        self.assertIn(
            "finish rejected",
            provider.turns[1].validation_feedback[0],
        )

    def test_model_loop_is_capped_at_five_decisions(self) -> None:
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="tool",
                    tool_name="L1.inspect_user_profile",
                    arguments={},
                    summary=f"inspection {index}",
                )
                for index in range(6)
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend(
            "user-1",
            "推荐一首摇滚",
            max_steps=10,
        )

        self.assertEqual(response.agent_mode, "model")
        self.assertEqual(len(provider.turns), 5)
        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(response.ranked_songs[0]["song_id"], "rock-a")
        self.assertEqual(
            response.tool_calls[-1]["decision_source"],
            "program_guard",
        )

    def test_program_guard_recovers_from_repeated_model_updates(self) -> None:
        repeated_patch = {
            "top_k": 1,
            "preference_terms": ["rock"],
            "exclude_terms": ["artist b"],
        }
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="update",
                    summary="identify rock preference",
                    request_patch=repeated_patch,
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="L1.inspect_user_profile",
                    arguments={},
                    summary="inspect profile",
                ),
                AgentDecision(
                    kind="update",
                    summary="repeat request interpretation",
                    request_patch=repeated_patch,
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="L1.inspect_user_profile",
                    arguments={},
                    summary="inspect profile again",
                ),
                AgentDecision(
                    kind="update",
                    summary="repeat request interpretation again",
                    request_patch=repeated_patch,
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "给我类似的音乐")

        self.assertEqual(response.agent_mode, "model")
        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(response.parsed_request.preference_terms, ["rock"])
        self.assertEqual(response.parsed_request.exclude_terms, ["artist b"])
        self.assertEqual(response.ranked_songs[0]["song_id"], "rock-a")
        self.assertEqual(
            [call["tool"] for call in response.tool_calls],
            ["L1.inspect_user_profile", "L4.rank_candidates"],
        )
        self.assertEqual(
            response.tool_calls[-1]["decision_source"],
            "program_guard",
        )
        self.assertEqual(
            response.agent_decisions[-1]["kind"],
            "program_guard",
        )

    def test_model_cannot_enable_exclude_seen_for_normal_request(self) -> None:
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="update",
                    summary="incorrectly exclude session history",
                    request_patch={
                        "top_k": 1,
                        "exclude_seen": True,
                    },
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="rank songs",
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "推荐音乐")

        self.assertFalse(response.parsed_request.exclude_seen)
        self.assertEqual(response.stop_reason, "goal_satisfied")

    def test_specific_british_preference_beats_broad_rock_term(self) -> None:
        self.song_store.save(
            make_song(
                "british-rock",
                artist="British Artist",
                genre="rock",
                tag="british",
            )
        )
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="update",
                    summary="capture British rock preference",
                    request_patch={
                        "top_k": 1,
                        "preference_terms": ["british rock"],
                        "exclude_terms": ["artist b"],
                    },
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="rank songs",
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "推荐英伦摇滚")

        self.assertIn("rock", response.parsed_request.preference_terms)
        self.assertEqual(
            response.parsed_request.preference_terms,
            ["british", "rock"],
        )
        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["british-rock"],
        )

    def test_request_update_after_ranking_triggers_guarded_rerank(self) -> None:
        self.song_store.save(
            make_song(
                "british-rock",
                artist="British Artist",
                genre="rock",
                tag="british invasion",
            )
        )
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="update",
                    summary="start with a narrow unavailable term",
                    request_patch={
                        "top_k": 1,
                        "preference_terms": ["uk indie"],
                    },
                ),
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="rank narrow request",
                ),
                AgentDecision(
                    kind="update",
                    summary="add a supported British tag",
                    request_patch={
                        "preference_terms": ["british invasion"],
                    },
                ),
                AgentDecision(
                    kind="finish",
                    summary="try to finish",
                ),
                AgentDecision(
                    kind="finish",
                    summary="try to finish again",
                ),
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "推荐合适的音乐")

        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["british-rock"],
        )
        self.assertEqual(
            response.tool_calls[-1]["decision_source"],
            "program_guard",
        )

    def test_program_enforces_artist_diversity_on_tool_output(self) -> None:
        self.song_store.save(
            make_song(
                "rock-a-2",
                artist="Artist A",
                genre="rock",
                tag="rock",
            )
        )
        registry = AgentToolRegistry()
        registry.register(
            "L4.rank_candidates",
            lambda **_arguments: ToolObservation(
                tool="L4.rank_candidates",
                status="ok",
                data={
                    "seed_song_ids": ["seed"],
                    "missing_seed_song_ids": [],
                    "ranked_songs": [
                        {"song_id": "rock-a"},
                        {"song_id": "rock-a-2"},
                    ],
                },
            ),
        )
        provider = MockLLMProvider(
            [
                AgentDecision(
                    kind="tool",
                    tool_name="L4.rank_candidates",
                    arguments={},
                    summary="rank diverse songs",
                    request_patch={
                        "top_k": 2,
                        "max_per_artist": 1,
                    },
                ),
                *[
                    AgentDecision(
                        kind="finish",
                        summary="attempt to finish",
                    )
                    for _index in range(4)
                ],
            ]
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            tool_registry=registry,
            llm_provider=provider,
            agent_mode="model",
        )

        response = service.recommend("user-1", "给我不同歌手的音乐")

        self.assertEqual(response.agent_mode, "model")
        self.assertEqual(response.stop_reason, "insufficient_candidates")
        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["rock-a"],
        )

    def test_registry_exposes_read_only_l1_to_l5_tool_schemas(self) -> None:
        self.assertEqual(
            {schema["name"] for schema in self.service.tool_registry.model_schemas()},
            {
                "L1.inspect_user_profile",
                "L2.inspect_song_profile",
                "L3.retrieve_candidates",
                "L4.rank_candidates",
                "L5.inspect_feedback_state",
            },
        )

    def test_empty_profile_stops_before_candidate_tools(self) -> None:
        self.profile_store.save(UserProfile(user_id="empty-user"))

        response = self.service.recommend(
            "empty-user",
            "推荐两首摇滚",
        )

        self.assertEqual(response.stop_reason, "empty_profile")
        self.assertEqual(response.ranked_songs, [])
        self.assertEqual(
            [call["tool"] for call in response.tool_calls],
            ["L1.inspect_user_profile"],
        )

    def test_more_reuses_session_and_excludes_seen_songs(self) -> None:
        first = self.service.recommend(
            "user-1",
            "推荐一首摇滚，每位歌手最多一首",
        )
        second = self.service.recommend(
            "user-1",
            "换一批，不要刚才推荐过的歌曲",
            session_id=first.session_id,
        )

        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(second.parsed_request.intent, "more")
        self.assertEqual(second.parsed_request.top_k, 1)
        self.assertEqual(second.parsed_request.max_per_artist, 1)
        self.assertEqual(second.parsed_request.preference_terms, ["rock"])
        self.assertEqual(second.parsed_request.exclude_terms, [])
        session = self.service.session_store.load_or_create(
            "user-1",
            first.session_id,
        )
        self.assertEqual(session.current_intent, "more")
        self.assertEqual(session.last_user_query, "换一批，不要刚才推荐过的歌曲")
        self.assertTrue(session.active_constraints["exclude_seen"])
        self.assertEqual(
            session.last_recommendation_ids,
            [song["song_id"] for song in second.ranked_songs],
        )
        self.assertNotEqual(
            first.ranked_songs[0]["song_id"],
            second.ranked_songs[0]["song_id"],
        )

    def test_temporary_feedback_excludes_skipped_track_from_next_turn(self) -> None:
        first = self.service.recommend("user-1", "推荐一首摇滚")
        skipped_song_id = first.ranked_songs[0]["song_id"]
        self.service.session_store.load_or_create("user-1", first.session_id)
        self.service.model_tool_registry.call(
            "update_session_memory",
            user_id="user-1",
            session_id=first.session_id,
            patch={
                "temporary_feedback": [
                    {"track_id": skipped_song_id, "event": "skipped"}
                ]
            },
        )

        second = self.service.recommend(
            "user-1",
            "推荐一首摇滚",
            session_id=first.session_id,
        )

        self.assertNotEqual(second.ranked_songs[0]["song_id"], skipped_song_id)
        self.assertEqual(
            second.ranked_songs[0]["evidence"]["temporary_feedback_events"],
            ["skipped"],
        )

    def test_old_session_removes_seen_song_reference_exclusion(self) -> None:
        root = Path(self.temporary_directory.name) / "legacy-sessions"
        root.mkdir()
        session_id = "legacy-session"
        (root / f"{session_id}.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "user_id": "user-1",
                    "turn_count": 1,
                    "preference_terms": ["rock"],
                    "exclude_terms": [
                        "pink floyd",
                        "刚才推荐过的歌曲",
                    ],
                    "seen_song_ids": ["rock-a"],
                    "last_trajectory_id": None,
                    "updated_at": "2026-06-15T00:00:00+00:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        session = JsonSessionStore(root).load_or_create(
            "user-1",
            session_id,
        )

        self.assertEqual(session.exclude_terms, ["pink floyd"])
        self.assertIsNone(session.last_top_k)
        self.assertEqual(session.seen_track_ids, ["rock-a"])
        self.assertEqual(session.schema_version, 1)

    def test_new_session_persists_v1_short_term_memory_shape(self) -> None:
        response = self.service.recommend("user-1", "推荐一首摇滚")

        session = self.service.session_store.load_or_create(
            "user-1",
            response.session_id,
        )

        self.assertEqual(session.schema_version, 1)
        self.assertEqual(session.current_intent, "recommend")
        self.assertEqual(session.last_user_query, "推荐一首摇滚")
        self.assertEqual(session.preference_terms, ["rock"])
        self.assertEqual(
            session.last_recommendation_ids,
            [song["song_id"] for song in response.ranked_songs],
        )
        self.assertEqual(session.last_run_id, response.trajectory_id)
        self.assertTrue(session.created_at)

    def test_insufficient_candidates_retries_and_records_diagnostics(self) -> None:
        response = self.service.recommend("user-1", "推荐五首摇滚")

        self.assertEqual(response.stop_reason, "insufficient_candidates")
        self.assertEqual(response.attempts, 3)
        self.assertEqual(
            [call["tool"] for call in response.tool_calls],
            [
                "L1.inspect_user_profile",
                "L4.rank_candidates",
                "L4.rank_candidates",
                "L4.rank_candidates",
                "L3.retrieve_candidates",
            ],
        )
        self.assertIn(
            "validated suggested action",
            response.tool_calls[1]["decision"],
        )
        self.assertEqual(
            response.tool_calls[1]["selected_action"]["tool"],
            "L4.rank_candidates",
        )

    def test_retry_arguments_come_from_tool_suggested_actions(self) -> None:
        registry = AgentToolRegistry()
        rank_calls: list[dict[str, object]] = []
        registry.register(
            "L1.inspect_user_profile",
            lambda **_arguments: ToolObservation(
                tool="L1.inspect_user_profile",
                status="ok",
                data={"collection_count": 1},
            ),
        )

        def rank_tool(**arguments: object) -> ToolObservation:
            rank_calls.append(dict(arguments))
            if len(rank_calls) == 1:
                return ToolObservation(
                    tool="L4.rank_candidates",
                    status="partial",
                    data={
                        "seed_song_ids": ["seed"],
                        "missing_seed_song_ids": [],
                        "ranked_songs": [],
                    },
                    retryable=True,
                    suggested_actions=[
                        {
                            "tool": "L4.rank_candidates",
                            "arguments": {"candidate_pool_size": 17},
                            "reason": "test-specific pool",
                        },
                        {
                            "tool": "L4.rank_candidates",
                            "arguments": {"min_retrieval_score": 0.23},
                            "reason": "test-specific threshold",
                        },
                    ],
                )
            return ToolObservation(
                tool="L4.rank_candidates",
                status="ok",
                data={
                    "seed_song_ids": ["seed"],
                    "missing_seed_song_ids": [],
                    "ranked_songs": [{"song_id": "rock-a"}],
                },
            )

        registry.register("L4.rank_candidates", rank_tool)
        registry.register(
            "L3.retrieve_candidates",
            lambda **_arguments: ToolObservation(
                tool="L3.retrieve_candidates",
                status="empty",
                data={"candidates": []},
            ),
        )
        service = RecommendationAgentService(
            self.service.ranking_service,
            self.song_store,
            self.trajectory_store,
            tool_registry=registry,
        )

        response = service.recommend("user-1", "推荐一首摇滚")

        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(len(rank_calls), 2)
        self.assertEqual(rank_calls[1]["candidate_pool_size"], 17)
        self.assertEqual(rank_calls[1]["min_retrieval_score"], 0.23)
        self.assertEqual(rank_calls[1]["top_k"], 5)
        self.assertEqual(
            response.tool_calls[1]["selected_action"]["arguments"],
            {
                "candidate_pool_size": 17,
                "min_retrieval_score": 0.23,
            },
        )

    def test_trajectory_store_lists_newest_first(self) -> None:
        first = self.service.recommend("user-1", "推荐一首摇滚")
        second = self.service.recommend("user-1", "推荐一首爵士")

        trajectories = self.trajectory_store.list_for_user("user-1")
        self.assertEqual(
            {item.trajectory_id for item in trajectories},
            {first.trajectory_id, second.trajectory_id},
        )

    def test_trajectory_store_appends_concurrent_feedback(self) -> None:
        response = self.service.recommend("user-1", "推荐一首摇滚")
        errors: list[BaseException] = []

        def append(index: int) -> None:
            try:
                self.trajectory_store.append_feedback(
                    "user-1",
                    response.trajectory_id,
                    {
                        "feedback_type": "like",
                        "song_id": "rock-a",
                        "timestamp": f"2026-06-11T00:00:{index:02d}+00:00",
                        "reward_score": 0.6,
                        "recommendation_context": {
                            "trajectory_id": response.trajectory_id,
                        },
                    },
                )
            except BaseException as error:
                errors.append(error)

        threads = [
            threading.Thread(target=append, args=(index,))
            for index in range(10)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        trajectory = self.trajectory_store.load(
            "user-1",
            response.trajectory_id,
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(trajectory.feedback_events), 10)
        self.assertEqual(len(trajectory.feedback_contexts), 10)
        self.assertEqual(trajectory.collection_writes, [])
        self.assertEqual(
            trajectory.feedback_contexts[0]["feedback_type"],
            "like",
        )
        self.assertEqual(
            trajectory.feedback_contexts[0]["trajectory_id"],
            response.trajectory_id,
        )

    def test_trajectory_store_records_collection_write_from_favorite_feedback(self) -> None:
        response = self.service.recommend("user-1", "推荐一首摇滚")

        self.trajectory_store.append_feedback(
            "user-1",
            response.trajectory_id,
            {
                "feedback_type": "favorite",
                "song_id": "rock-a",
                "timestamp": "2026-06-11T00:00:00+00:00",
                "reward_score": 0.8,
                "recommendation_context": {
                    "trajectory_id": response.trajectory_id,
                    "rank": 1,
                    "final_score": 0.6,
                    "source": "web",
                    "track": {
                        "title": "rock-a",
                        "artist": "Artist A",
                    },
                },
            },
        )

        trajectory = self.trajectory_store.load(
            "user-1",
            response.trajectory_id,
        )
        self.assertEqual(len(trajectory.feedback_events), 1)
        self.assertEqual(len(trajectory.feedback_contexts), 1)
        self.assertEqual(len(trajectory.collection_writes), 1)
        self.assertEqual(
            trajectory.feedback_contexts[0]["recommendation_rank"],
            1,
        )
        self.assertEqual(
            trajectory.feedback_contexts[0]["feedback_source"],
            "web",
        )
        self.assertEqual(
            trajectory.collection_writes[0]["action"],
            "add_track",
        )
        self.assertEqual(
            trajectory.collection_writes[0]["feedback_type"],
            "favorite",
        )
        self.assertEqual(
            trajectory.collection_writes[0]["track"]["artist"],
            "Artist A",
        )


class L6CliTests(unittest.TestCase):
    def test_schema_command_prints_agent_contract(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "rateyourdj.l6.cli", "schema"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(json.loads(result.stdout), agent_schema())

    def test_deepseek_provider_requires_environment_key(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment.pop("DEEPSEEK_API_KEY", None)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rateyourdj.l6.cli",
                "--llm-provider",
                "deepseek",
                "recommend",
                "demo-user",
                "推荐一首歌",
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("DEEPSEEK_API_KEY is not configured", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

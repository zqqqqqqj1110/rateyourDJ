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
    AgentDecision,
    AgentToolRegistryV1,
    AgentToolRegistry,
    JsonSessionStore,
    JsonTrajectoryStore,
    LLMProviderError,
    LLMResponseError,
    MockLLMProvider,
    RecommendationAgentService,
    agent_schema,
    parse_agent_request,
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
        self.assertEqual(request.exclude_terms, [])

    def test_rejects_empty_query(self) -> None:
        with self.assertRaises(ValueError):
            parse_agent_request(" ")


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
            [call["tool"] for call in trajectory.tool_calls[:2]],
            ["L1.inspect_user_profile", "L4.rank_candidates"],
        )
        self.assertEqual(trajectory.stop_reason, "insufficient_candidates")
        self.assertTrue(trajectory.plan)
        self.assertEqual(
            trajectory.recommendations[0]["song_id"],
            "rock-a",
        )

    def test_unquoted_compact_artist_name_is_excluded(self) -> None:
        response = self.service.recommend("user-1", "不要artistb")

        self.assertNotIn(
            "rock-b",
            [song["song_id"] for song in response.ranked_songs],
        )

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
            ["get_user_memory", "search_tracks"],
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

        self.assertEqual(search_provider.queries, ["2020 british indie rock"])
        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["spotify:track:new"],
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

        self.assertEqual(search_provider.queries, ["british rock"])
        self.assertEqual(response.stop_reason, "goal_satisfied")
        self.assertEqual(
            [song["song_id"] for song in response.ranked_songs],
            ["spotify:track:wonderwall"],
        )
        self.assertIn(
            "外部搜索结果",
            response.message,
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
        self.assertNotEqual(
            first.ranked_songs[0]["song_id"],
            second.ranked_songs[0]["song_id"],
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

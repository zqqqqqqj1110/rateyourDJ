import tempfile
import unittest
from pathlib import Path

from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l6 import AgentToolRegistryV1, agent_tool_schemas


CORE_AGENT_TOOLS = {
    "get_user_memory",
    "get_session_memory",
    "update_session_memory",
    "propose_memory_update",
    "commit_memory_update",
    "search_tracks",
    "get_track_metadata",
    "get_artist_profile",
    "get_similar_tracks",
    "rank_candidates",
    "explain_recommendations",
    "record_feedback",
    "save_to_collection",
}
from rateyourdj.providers import (
    ExternalMusicProvider,
    ProviderSearchResult,
    ProviderTrack,
    TrackQuery,
)


def make_song(song_id: str, *, artist: str = "Artist") -> SongProfile:
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
    song.source_tags["lastfm_track_tags"] = {"rock": 1.0}
    song.genres = {"rock": 1.0}
    song.confidence_score = 1.0
    return song


class AgentToolRegistryV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_store = JsonProfileStore(root / "profiles")
        self.song_store = JsonSongStore(root / "songs")
        self.profile_store.save(
            UserProfile(
                user_id="user-1",
                collection_song_ids=["seed"],
                genre_preferences={"rock": 1.0},
                tag_preferences={"rock": 1.0},
            )
        )
        self.song_store.save(make_song("seed", artist="Seed Artist"))
        self.song_store.save(make_song("candidate", artist="Candidate Artist"))
        self.registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_model_schemas_use_agent_tool_names(self) -> None:
        schema_names = {schema["name"] for schema in self.registry.model_schemas()}

        self.assertIn("get_user_memory", schema_names)
        self.assertIn("get_session_memory", schema_names)
        self.assertIn("update_session_memory", schema_names)
        self.assertIn("propose_memory_update", schema_names)
        self.assertIn("commit_memory_update", schema_names)
        self.assertIn("get_track_metadata", schema_names)
        self.assertIn("get_artist_profile", schema_names)
        self.assertIn("get_similar_tracks", schema_names)
        self.assertIn("rank_candidates", schema_names)
        self.assertIn("explain_recommendations", schema_names)
        self.assertIn("record_feedback", schema_names)
        self.assertIn("save_to_collection", schema_names)
        self.assertNotIn("L1.inspect_user_profile", schema_names)
        self.assertNotIn("L4.rank_candidates", schema_names)

    def test_schema_catalog_includes_core_tools(self) -> None:
        schema_names = {schema["name"] for schema in agent_tool_schemas()}

        self.assertEqual(schema_names, CORE_AGENT_TOOLS)

    def test_registered_agent_facing_tools_have_schemas(self) -> None:
        schema_names = {schema["name"] for schema in agent_tool_schemas()}
        legacy_names = {
            "L1.inspect_user_profile",
            "L2.inspect_song_profile",
            "L3.retrieve_candidates",
            "L4.rank_candidates",
        }
        agent_names = set(self.registry.names()) - legacy_names

        self.assertTrue(agent_names <= schema_names)

    def test_get_user_memory_wraps_l1_observation(self) -> None:
        observation = self.registry.call("get_user_memory", user_id="user-1")

        self.assertEqual(observation.tool, "get_user_memory")
        self.assertEqual(observation.status, "ok")
        self.assertEqual(observation.data["user_id"], "user-1")
        self.assertEqual(observation.data["collection_count"], 1)

    def test_get_track_metadata_wraps_l2_observation(self) -> None:
        observation = self.registry.call(
            "get_track_metadata",
            track_ids=["seed"],
        )

        self.assertEqual(observation.tool, "get_track_metadata")
        self.assertEqual(observation.status, "ok")
        self.assertEqual(observation.data["tracks"][0]["song_id"], "seed")
        self.assertEqual(observation.data["missing_track_ids"], [])

    def test_rank_candidates_maps_suggested_actions_to_agent_names(self) -> None:
        observation = self.registry.call(
            "rank_candidates",
            user_id="user-1",
            limit=5,
        )

        self.assertEqual(observation.tool, "rank_candidates")
        self.assertEqual(observation.status, "partial")
        self.assertTrue(observation.suggested_actions)
        self.assertEqual(
            observation.suggested_actions[0]["tool"],
            "rank_candidates",
        )

    def test_record_feedback_maps_agent_event_to_existing_feedback(self) -> None:
        observation = self.registry.call(
            "record_feedback",
            user_id="user-1",
            track_id="candidate",
            event="liked",
            context={"rank": 1},
        )

        self.assertEqual(observation.tool, "record_feedback")
        self.assertEqual(observation.status, "ok")
        self.assertEqual(
            observation.data["feedback"]["feedback_type"],
            "like",
        )

    def test_session_memory_tools_read_and_update_session_state(self) -> None:
        session = self.registry.call(
            "get_session_memory",
            user_id="user-1",
            session_id="session-1",
        )

        self.assertEqual(session.tool, "get_session_memory")
        self.assertEqual(session.data["seen_track_ids"], [])
        self.assertEqual(session.data["current_intent"], "recommend")

        updated = self.registry.call(
            "update_session_memory",
            user_id="user-1",
            session_id="session-1",
            patch={
                "current_intent": "more",
                "last_user_query": "换一批",
                "preference_terms": ["rock"],
                "exclude_terms": ["artist b"],
                "seen_track_ids": ["candidate"],
                "seed_track_ids": ["seed"],
                "last_run_id": "run-1",
                "last_recommendation_ids": ["candidate"],
                "temporary_feedback": [
                    {"track_id": "candidate", "event": "skipped"}
                ],
            },
        )
        session = self.registry.call(
            "get_session_memory",
            user_id="user-1",
            session_id="session-1",
        )

        self.assertEqual(updated.status, "ok")
        self.assertEqual(session.data["current_intent"], "more")
        self.assertEqual(session.data["last_user_query"], "换一批")
        self.assertEqual(session.data["preference_terms"], ["rock"])
        self.assertEqual(session.data["exclude_terms"], ["artist b"])
        self.assertEqual(session.data["seen_track_ids"], ["candidate"])
        self.assertEqual(session.data["seed_track_ids"], ["seed"])
        self.assertEqual(session.data["last_run_id"], "run-1")
        self.assertEqual(session.data["last_recommendation_ids"], ["candidate"])
        self.assertEqual(
            session.data["temporary_feedback"][0]["event"],
            "skipped",
        )

    def test_memory_proposal_tools_return_policy_effects(self) -> None:
        proposal = self.registry.call(
            "propose_memory_update",
            user_id="user-1",
            source="feedback_pattern",
            proposal={
                "field": "preferred_genres.rock",
                "value": "rock",
                "delta": 0.1,
                "confidence": 0.8,
                "reason": "Repeated positive feedback.",
            },
        )
        committed = self.registry.call(
            "commit_memory_update",
            user_id="user-1",
            proposal_id=proposal.data["proposal_id"],
            run_id="run-1",
        )

        self.assertTrue(proposal.data["accepted_by_policy"])
        self.assertTrue(committed.data["committed"])
        self.assertEqual(
            committed.data["memory_updates"][0]["field"],
            "preferred_genres.rock",
        )

    def test_get_artist_profile_reads_local_artist_context(self) -> None:
        observation = self.registry.call(
            "get_artist_profile",
            artist_names=["Candidate Artist"],
        )

        self.assertEqual(observation.tool, "get_artist_profile")
        self.assertEqual(observation.status, "ok")
        self.assertEqual(observation.data["artists"][0]["name"], "Candidate Artist")
        self.assertEqual(observation.data["artists"][0]["genres"], ["rock"])

    def test_explain_and_save_collection_tools_return_structured_effects(self) -> None:
        explanation = self.registry.call(
            "explain_recommendations",
            user_id="user-1",
            message="推荐一首 rock",
            ranked_tracks=[
                {
                    "song_id": "candidate",
                    "title": "candidate",
                    "ranking_reasons": ["匹配 rock 偏好"],
                }
            ],
        )
        saved = self.registry.call(
            "save_to_collection",
            user_id="user-1",
            track_id="candidate",
            source="agent_recommendation",
            run_id="run-1",
        )

        self.assertEqual(explanation.status, "ok")
        self.assertEqual(
            explanation.data["recommendations"][0]["reasons"][0]["type"],
            "ranking_evidence",
        )
        self.assertTrue(saved.data["saved"])
        self.assertEqual(saved.data["collection_count"], 2)


class FakeSearchProvider:
    @property
    def provider_name(self):
        return "fake"

    def search_tracks(self, query, *, limit=10, market=None):
        return ProviderSearchResult(
            provider="fake",
            query=query,
            tracks=[
                ProviderTrack(
                    track_id="fake:track:1",
                    provider="fake",
                    title="Live Forever",
                    artist="Oasis",
                )
            ],
        )


class FakeMetadataProvider:
    @property
    def provider_name(self):
        return "fake"

    def get_track_metadata(self, query: TrackQuery):
        return ProviderTrack(
            track_id="fake:track:metadata",
            provider="fake",
            title=query.title,
            artist=query.artist,
            album=query.album,
            tags={"britpop": 1.0},
        )


class AgentToolRegistryV1ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.profile_store = JsonProfileStore(root / "profiles")
        self.song_store = JsonSongStore(root / "songs")
        self.music_provider = ExternalMusicProvider(
            search_providers=[FakeSearchProvider()],
            metadata_provider=FakeMetadataProvider(),
        )
        self.registry = AgentToolRegistryV1.default(
            self.profile_store,
            self.song_store,
            music_provider=self.music_provider,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_search_tracks_uses_external_provider(self) -> None:
        schema_names = {schema["name"] for schema in self.registry.model_schemas()}

        self.assertIn("search_tracks", schema_names)

        observation = self.registry.call(
            "search_tracks",
            query="britpop",
            limit=5,
            market="AU",
        )

        self.assertEqual(observation.tool, "search_tracks")
        self.assertEqual(observation.status, "ok")
        self.assertEqual(
            observation.data["provider_results"][0]["provider"],
            "fake",
        )
        self.assertEqual(
            observation.data["tracks"][0]["track_id"],
            "fake:track:1",
        )

    def test_get_track_metadata_can_use_external_query(self) -> None:
        observation = self.registry.call(
            "get_track_metadata",
            queries=[
                {
                    "title": "Live Forever",
                    "artist": "Oasis",
                    "album": "Definitely Maybe",
                }
            ],
        )

        self.assertEqual(observation.tool, "get_track_metadata")
        self.assertEqual(observation.status, "ok")
        self.assertEqual(
            observation.data["tracks"][0]["track_id"],
            "fake:track:metadata",
        )
        self.assertEqual(observation.data["tracks"][0]["tags"]["britpop"], 1.0)


if __name__ == "__main__":
    unittest.main()

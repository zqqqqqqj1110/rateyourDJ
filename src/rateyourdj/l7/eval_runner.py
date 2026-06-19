from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.l4 import RecommendationRankingService
from rateyourdj.l6 import (
    AgentResponse,
    AgentToolRegistryV1,
    JsonSessionStore,
    JsonTrajectoryStore,
    MockLLMProvider,
    RecommendationAgentService,
)
from rateyourdj.providers import (
    ExternalMusicProvider,
    ProviderSearchResult,
    ProviderTrack,
)

from .eval_cases import EVAL_CASES_V1
from .models import EvalCaseResult, EvalSuiteReport


class RecommendationEvalSuite:
    def __init__(
        self,
        cases: list[dict[str, Any]] | None = None,
    ) -> None:
        self.cases = list(cases or EVAL_CASES_V1)

    def run(
        self,
        *,
        case_ids: list[str] | None = None,
    ) -> EvalSuiteReport:
        selected = [
            case
            for case in self.cases
            if case_ids is None or case["id"] in set(case_ids)
        ]
        results = [self._run_case(case) for case in selected]
        category_counts = Counter(case["category"] for case in selected)
        failed_case_ids = [result.case_id for result in results if not result.passed]
        return EvalSuiteReport(
            suite_name="eval_cases_v1",
            case_count=len(results),
            passed_count=sum(result.passed for result in results),
            failed_count=sum(not result.passed for result in results),
            category_counts=dict(sorted(category_counts.items())),
            failed_case_ids=failed_case_ids,
            cases=results,
        )

    def _run_case(self, case: dict[str, Any]) -> EvalCaseResult:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_store = JsonProfileStore(root / "profiles")
            song_store = JsonSongStore(root / "songs")
            trajectory_store = JsonTrajectoryStore(root / "trajectories")
            session_store = JsonSessionStore(root / "sessions")
            self._seed_song_store(song_store)
            self._seed_profile_store(
                profile_store,
                case["user_id"],
                case.get("profile_key", "rock"),
            )

            provider = None
            llm_provider = None
            agent_mode = "rules"
            if case["category"] == "provider":
                provider = ExternalMusicProvider(
                    search_providers=[
                        _StaticSearchProvider(case.get("provider_tracks", []))
                    ]
                )
                llm_provider = MockLLMProvider([])
                agent_mode = "model"

            registry = AgentToolRegistryV1.default(
                profile_store,
                song_store,
                music_provider=provider,
                session_store=session_store,
            )
            service = RecommendationAgentService(
                RecommendationRankingService(profile_store, song_store),
                song_store,
                trajectory_store,
                session_store=session_store,
                model_tool_registry=registry,
                llm_provider=llm_provider,
                agent_mode=agent_mode,
            )
            session_id = "eval-session"
            self._apply_session_setup(
                session_store,
                case["user_id"],
                session_id,
                case.get("session_setup", {}),
            )
            prelude_responses: list[AgentResponse] = []
            for query in case.get("prelude_queries", []):
                prelude_responses.append(
                    service.recommend(
                        case["user_id"],
                        query,
                        session_id=session_id,
                    )
                )
            response = service.recommend(
                case["user_id"],
                case["query"],
                session_id=session_id,
            )
            trajectory = trajectory_store.load(case["user_id"], response.trajectory_id)
            session = session_store.load_or_create(case["user_id"], session_id)
            failure_reasons = _validate_case(
                case,
                response=response,
                trajectory=trajectory.to_dict(),
                session=_session_snapshot(session),
                prelude_responses=prelude_responses,
            )
            return EvalCaseResult(
                case_id=case["id"],
                category=case["category"],
                passed=not failure_reasons,
                failure_reasons=failure_reasons,
                stop_reason=response.stop_reason,
                recommendation_count=len(response.ranked_songs),
                tool_names=[call["tool"] for call in response.tool_calls],
            )

    @staticmethod
    def _seed_song_store(song_store: JsonSongStore) -> None:
        for spec in _song_specs():
            song = SongProfile.empty(spec["song_id"])
            song.metadata.update(
                {
                    "title": spec["title"],
                    "artist": spec["artist"],
                    "album": spec.get("album", "Album"),
                    "release_year": spec.get("release_year", 2000),
                    "duration_ms": 200_000,
                    "version_type": "original",
                }
            )
            song.source_tags["lastfm_track_tags"] = dict(spec.get("tags", {}))
            song.source_tags["lastfm_artist_tags"] = dict(spec.get("artist_tags", {}))
            song.genres = dict(spec.get("genres", {}))
            song.confidence_score = 1.0
            song_store.save(song)

    @staticmethod
    def _seed_profile_store(
        profile_store: JsonProfileStore,
        user_id: str,
        profile_key: str,
    ) -> None:
        template = _profile_templates().get(profile_key)
        if template is None:
            raise ValueError(f"unknown profile_key: {profile_key}")
        if profile_key == "empty":
            return
        profile_store.save(
            UserProfile(
                user_id=user_id,
                collection_song_ids=list(template["collection_song_ids"]),
                artist_preferences=dict(template.get("artist_preferences", {})),
                genre_preferences=dict(template.get("genre_preferences", {})),
                tag_preferences=dict(template.get("tag_preferences", {})),
            )
        )

    @staticmethod
    def _apply_session_setup(
        session_store: JsonSessionStore,
        user_id: str,
        session_id: str,
        patch: dict[str, Any],
    ) -> None:
        session = session_store.load_or_create(user_id, session_id)
        for key, value in patch.items():
            setattr(session, key, value)
        session_store.save(session)


class _StaticSearchProvider:
    def __init__(self, tracks: list[dict[str, Any]]) -> None:
        self._tracks = [
            ProviderTrack(**track)
            for track in tracks
        ]

    @property
    def provider_name(self) -> str:
        return "fake"

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> ProviderSearchResult:
        return ProviderSearchResult(
            provider="spotify",
            query=query,
            tracks=self._tracks[:limit],
        )


def _validate_case(
    case: dict[str, Any],
    *,
    response: AgentResponse,
    trajectory: dict[str, Any],
    session: dict[str, Any],
    prelude_responses: list[AgentResponse],
) -> list[str]:
    expected = case.get("expected", {})
    reasons: list[str] = []
    ranked_song_ids = [song["song_id"] for song in response.ranked_songs]
    ranked_artists = [str(song.get("artist") or "") for song in response.ranked_songs]
    tool_names = [call["tool"] for call in response.tool_calls]
    evidence = response.ranked_songs[0].get("evidence", {}) if response.ranked_songs else {}
    if expected.get("stop_reason") and response.stop_reason != expected["stop_reason"]:
        reasons.append(
            f"stop_reason expected {expected['stop_reason']} got {response.stop_reason}"
        )
    if "min_result_count" in expected and len(response.ranked_songs) < int(expected["min_result_count"]):
        reasons.append("result count below minimum")
    if "max_result_count" in expected and len(response.ranked_songs) > int(expected["max_result_count"]):
        reasons.append("result count above maximum")
    for track_id in expected.get("included_track_ids", []):
        if track_id not in ranked_song_ids:
            reasons.append(f"missing expected track {track_id}")
    for track_id in expected.get("excluded_track_ids", []):
        if track_id in ranked_song_ids:
            reasons.append(f"found excluded track {track_id}")
    for artist in expected.get("excluded_artists", []):
        if artist in ranked_artists:
            reasons.append(f"found excluded artist {artist}")
    if "tool_names_exact" in expected and tool_names != expected["tool_names_exact"]:
        reasons.append("tool path did not match exact expectation")
    for tool_name in expected.get("tool_names_include", []):
        if tool_name not in tool_names:
            reasons.append(f"missing tool {tool_name}")
    for tool_name in expected.get("tool_names_exclude", []):
        if tool_name in tool_names:
            reasons.append(f"unexpected tool {tool_name}")
    if expected.get("intent") and response.parsed_request.intent != expected["intent"]:
        reasons.append("parsed intent mismatch")
    for term in expected.get("preference_terms_include", []):
        if term not in response.parsed_request.preference_terms:
            reasons.append(f"missing parsed preference term {term}")
    for term in expected.get("exclude_terms_include", []):
        if term not in response.parsed_request.exclude_terms:
            reasons.append(f"missing parsed exclude term {term}")
    if expected.get("session_current_intent") and session["current_intent"] != expected["session_current_intent"]:
        reasons.append("session current_intent mismatch")
    if "session_exclude_seen" in expected and bool(session["active_constraints"].get("exclude_seen")) != bool(expected["session_exclude_seen"]):
        reasons.append("session exclude_seen mismatch")
    if expected.get("session_last_user_query") and session.get("last_user_query") != expected["session_last_user_query"]:
        reasons.append("session last_user_query mismatch")
    for phase_name, status in expected.get("phase_status", {}).items():
        phase_item = next(
            (item for item in trajectory.get("plan", []) if item.get("phase") == phase_name),
            None,
        )
        if phase_item is None or phase_item.get("status") != status:
            reasons.append(f"phase {phase_name} status mismatch")
    if expected.get("exclude_previous_results") and prelude_responses:
        previous = {
            song["song_id"]
            for song in prelude_responses[-1].ranked_songs
        }
        if previous & set(ranked_song_ids):
            reasons.append("result repeated a previous recommendation")
    if "max_per_artist" in expected:
        artist_counts = Counter(ranked_artists)
        if any(count > int(expected["max_per_artist"]) for count in artist_counts.values() if count):
            reasons.append("artist diversity constraint failed")
    for track_id in expected.get("evidence_seed_track_ids", []):
        if track_id not in list(evidence.get("seed_track_ids", [])):
            reasons.append(f"missing seed evidence {track_id}")
    for event in expected.get("evidence_feedback_events", []):
        if event not in list(evidence.get("temporary_feedback_events", [])):
            reasons.append(f"missing feedback evidence {event}")
    for key in expected.get("evidence_active_constraint_keys", []):
        if key not in dict(evidence.get("active_constraints", {})):
            reasons.append(f"missing active constraint evidence {key}")
    return reasons


def _session_snapshot(session: Any) -> dict[str, Any]:
    return {
        "current_intent": session.current_intent,
        "last_user_query": session.last_user_query,
        "active_constraints": dict(session.active_constraints),
        "last_recommendation_ids": list(session.last_recommendation_ids),
    }


def _profile_templates() -> dict[str, dict[str, Any]]:
    return {
        "rock": {
            "collection_song_ids": ["seed-rock"],
            "genre_preferences": {"rock": 1.0},
            "tag_preferences": {"rock": 1.0, "british": 0.8, "indie": 0.6},
        },
        "mixed": {
            "collection_song_ids": [
                "seed-rock",
                "jazz-a",
                "soul-a",
                "folk-a",
                "electronic-a",
                "ambient-a",
                "punk-a",
                "metal-a",
                "country-a",
                "blues-a",
            ],
            "genre_preferences": {
                "rock": 1.0,
                "jazz": 0.8,
                "soul": 0.8,
                "folk": 0.8,
                "electronic": 0.8,
                "ambient": 0.8,
                "punk": 0.8,
                "metal": 0.8,
                "country": 0.8,
                "blues": 0.8,
            },
            "tag_preferences": {
                "rock": 1.0,
                "jazz": 0.8,
                "soul": 0.8,
                "folk": 0.8,
                "electronic": 0.8,
                "ambient": 0.8,
                "punk": 0.8,
                "metal": 0.8,
                "country": 0.8,
                "blues": 0.8,
                "british": 0.7,
            },
        },
        "empty": {},
    }


def _song_specs() -> list[dict[str, Any]]:
    return [
        {
            "song_id": "seed-rock",
            "title": "Seed Rock",
            "artist": "Seed Artist",
            "genres": {"rock": 1.0},
            "tags": {"rock": 1.0},
        },
        {
            "song_id": "rock-a",
            "title": "Rock A",
            "artist": "Artist A",
            "genres": {"rock": 1.0},
            "tags": {"rock": 1.0},
        },
        {
            "song_id": "rock-b",
            "title": "Rock B",
            "artist": "Artist B",
            "genres": {"rock": 1.0},
            "tags": {"rock": 1.0},
        },
        {
            "song_id": "british-rock",
            "title": "British Rock",
            "artist": "British Artist",
            "genres": {"rock": 1.0},
            "tags": {"british": 1.0, "rock": 1.0},
        },
        {
            "song_id": "indie-new",
            "title": "New Indie",
            "artist": "Band New",
            "release_year": 2021,
            "genres": {"rock": 1.0, "indie rock": 1.0},
            "tags": {"indie": 1.0, "rock": 1.0, "british": 1.0},
        },
        {
            "song_id": "indie-old",
            "title": "Old Indie",
            "artist": "Band Old",
            "release_year": 2019,
            "genres": {"rock": 1.0, "indie rock": 1.0},
            "tags": {"indie": 1.0, "rock": 1.0},
        },
        {
            "song_id": "jazz-a",
            "title": "Jazz A",
            "artist": "Artist C",
            "genres": {"jazz": 1.0},
            "tags": {"jazz": 1.0},
        },
        {
            "song_id": "jazz-b",
            "title": "Jazz B",
            "artist": "Artist J",
            "genres": {"jazz": 1.0},
            "tags": {"jazz": 1.0},
        },
        {
            "song_id": "soul-a",
            "title": "Soul A",
            "artist": "Artist D",
            "genres": {"soul": 1.0},
            "tags": {"soul": 1.0},
        },
        {
            "song_id": "folk-a",
            "title": "Folk A",
            "artist": "Artist E",
            "genres": {"folk": 1.0},
            "tags": {"folk": 1.0},
        },
        {
            "song_id": "electronic-a",
            "title": "Electronic A",
            "artist": "DJ Pulse",
            "genres": {"electronic": 1.0},
            "tags": {"electronic": 1.0},
        },
        {
            "song_id": "ambient-a",
            "title": "Ambient A",
            "artist": "Drift Unit",
            "genres": {"ambient": 1.0},
            "tags": {"ambient": 1.0},
        },
        {
            "song_id": "punk-a",
            "title": "Punk A",
            "artist": "Artist P",
            "genres": {"punk": 1.0},
            "tags": {"punk": 1.0},
        },
        {
            "song_id": "metal-a",
            "title": "Metal A",
            "artist": "Artist M",
            "genres": {"metal": 1.0},
            "tags": {"metal": 1.0},
        },
        {
            "song_id": "country-a",
            "title": "Country A",
            "artist": "Artist K",
            "genres": {"country": 1.0},
            "tags": {"country": 1.0},
        },
        {
            "song_id": "blues-a",
            "title": "Blues A",
            "artist": "Artist L",
            "genres": {"blues": 1.0},
            "tags": {"blues": 1.0},
        },
    ]

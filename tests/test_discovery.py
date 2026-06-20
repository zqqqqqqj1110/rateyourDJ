"""Tests for generative track discovery with external grounding."""

import unittest

from rateyourdj.domain import (
    DiscoveryResult,
    DiscoveryService,
    GeneratedCandidate,
    TasteSeedTrackGenerator,
)
from rateyourdj.domain.generators import (
    DeepSeekTrackGenerator,
    TrackGeneratorError,
)
from rateyourdj.providers import (
    ExternalMusicProvider,
    ProviderError,
    ProviderTrack,
    TrackQuery,
)


class StubGenerator:
    """Returns a fixed list of candidates regardless of input."""

    def __init__(self, candidates, *, name="stub"):
        self._candidates = candidates
        self._name = name
        self.calls = []

    @property
    def name(self):
        return self._name

    def generate(self, *, intent, user_taste, count, exclude_artists):
        self.calls.append(
            {
                "intent": intent,
                "user_taste": user_taste,
                "count": count,
                "exclude_artists": list(exclude_artists),
            }
        )
        return list(self._candidates)


class FailingGenerator:
    @property
    def name(self):
        return "failing"

    def generate(self, *, intent, user_taste, count, exclude_artists):
        raise RuntimeError("model unavailable")


class FakeMetadataProvider:
    """Confirms a track only if (title, artist) is in the known catalog."""

    def __init__(self, known):
        # known: dict[(title_lower, artist_lower)] -> ProviderTrack
        self._known = known
        self.queries = []

    @property
    def provider_name(self):
        return "fake-metadata"

    def get_track_metadata(self, query: TrackQuery) -> ProviderTrack:
        self.queries.append(query)
        key = (query.title.casefold(), query.artist.casefold())
        if key not in self._known:
            raise ProviderError(f"no track for {query.title} by {query.artist}")
        return self._known[key]


def _track(title, artist, track_id):
    return ProviderTrack(
        track_id=track_id,
        provider="spotify",
        title=title,
        artist=artist,
        album="Some Album",
        release_year=1995,
        preview_url="https://preview",
    )


def _provider_with(known):
    return ExternalMusicProvider(metadata_provider=FakeMetadataProvider(known))


class DiscoveryServiceTests(unittest.TestCase):
    def test_grounds_real_tracks_and_drops_hallucinations(self):
        known = {
            ("live forever", "oasis"): _track("Live Forever", "Oasis", "id-1"),
            ("common people", "pulp"): _track("Common People", "Pulp", "id-2"),
        }
        generator = StubGenerator(
            [
                GeneratedCandidate("Live Forever", "Oasis", "britpop anthem"),
                GeneratedCandidate("Imaginary Song", "Made Up Band", "n/a"),
                GeneratedCandidate("Common People", "Pulp", "class commentary"),
            ]
        )
        service = DiscoveryService(generator, _provider_with(known))

        result = service.discover(intent="britpop please", count=5)

        self.assertIsInstance(result, DiscoveryResult)
        self.assertEqual(result.generated, 3)
        self.assertEqual(result.grounded, 2)
        self.assertEqual(result.dropped, 1)
        grounded_titles = {track.title for track in result.tracks}
        self.assertEqual(grounded_titles, {"Live Forever", "Common People"})
        drop_reasons = {item["drop_reason"] for item in result.dropped_candidates}
        self.assertEqual(drop_reasons, {"not_found"})

    def test_hallucination_rate_reported(self):
        known = {("live forever", "oasis"): _track("Live Forever", "Oasis", "id-1")}
        generator = StubGenerator(
            [
                GeneratedCandidate("Live Forever", "Oasis"),
                GeneratedCandidate("Fake One", "Ghost"),
                GeneratedCandidate("Fake Two", "Ghost"),
                GeneratedCandidate("Fake Three", "Ghost"),
            ]
        )
        service = DiscoveryService(generator, _provider_with(known))

        result = service.discover(intent="anything", count=5)

        self.assertEqual(result.hallucination_rate, 0.75)

    def test_excluded_artists_are_dropped_before_grounding(self):
        known = {
            ("live forever", "oasis"): _track("Live Forever", "Oasis", "id-1"),
            ("girls and boys", "blur"): _track("Girls & Boys", "Blur", "id-3"),
        }
        # Note: provider returns canonical title "Girls & Boys".
        known[("girls & boys", "blur")] = known.pop(("girls and boys", "blur"))
        generator = StubGenerator(
            [
                GeneratedCandidate("Live Forever", "Oasis"),
                GeneratedCandidate("Girls & Boys", "Blur"),
            ]
        )
        provider = _provider_with(known)
        service = DiscoveryService(generator, provider)

        result = service.discover(
            intent="britpop", count=5, exclude_artists=["Blur"]
        )

        self.assertEqual(result.grounded, 1)
        self.assertEqual(result.tracks[0].artist, "Oasis")
        excluded = [
            item
            for item in result.dropped_candidates
            if item["drop_reason"] == "excluded_artist"
        ]
        self.assertEqual(len(excluded), 1)
        # Excluded artist should never reach the provider.
        metadata = provider.metadata_provider
        self.assertNotIn(
            "blur",
            {q.artist.casefold() for q in metadata.queries},
        )

    def test_stops_at_requested_count(self):
        known = {
            (f"song {i}".casefold(), "artist"): _track(f"Song {i}", "Artist", f"id-{i}")
            for i in range(10)
        }
        generator = StubGenerator(
            [GeneratedCandidate(f"Song {i}", "Artist") for i in range(10)]
        )
        service = DiscoveryService(generator, _provider_with(known))

        result = service.discover(intent="lots", count=3)

        self.assertEqual(result.grounded, 3)

    def test_overgenerate_factor_requests_more_than_count(self):
        generator = StubGenerator([])
        service = DiscoveryService(
            generator, _provider_with({}), overgenerate_factor=3
        )

        service.discover(intent="x", count=4)

        self.assertEqual(generator.calls[0]["count"], 12)

    def test_generator_failure_is_reported_not_raised(self):
        service = DiscoveryService(FailingGenerator(), _provider_with({}))

        result = service.discover(intent="x", count=5)

        self.assertEqual(result.grounded, 0)
        self.assertEqual(result.generated, 0)
        self.assertTrue(result.diagnostics)
        self.assertIn("failing", result.diagnostics[0])

    def test_duplicate_grounded_tracks_are_dropped(self):
        track = _track("Live Forever", "Oasis", "id-1")
        known = {("live forever", "oasis"): track}
        generator = StubGenerator(
            [
                GeneratedCandidate("Live Forever", "Oasis"),
                GeneratedCandidate("live forever", "OASIS"),  # same song
            ]
        )
        service = DiscoveryService(generator, _provider_with(known))

        result = service.discover(intent="x", count=5)

        # The second is removed as a duplicate candidate before grounding.
        self.assertEqual(result.grounded, 1)


class TasteSeedGeneratorTests(unittest.TestCase):
    def test_proposes_from_seed_tracks(self):
        generator = TasteSeedTrackGenerator()
        taste = {
            "seed_tracks": [
                {"title": "Live Forever", "artist": "Oasis"},
                {"title": "Common People", "artist": "Pulp"},
            ]
        }

        candidates = generator.generate(
            intent="x", user_taste=taste, count=5, exclude_artists=[]
        )

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].title, "Live Forever")

    def test_handles_missing_seed_tracks(self):
        generator = TasteSeedTrackGenerator()

        candidates = generator.generate(
            intent="x", user_taste={}, count=5, exclude_artists=[]
        )

        self.assertEqual(candidates, [])


class DeepSeekGeneratorTests(unittest.TestCase):
    def test_parses_tool_call_into_candidates(self):
        def fake_request(payload):
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "propose_tracks",
                                        "arguments": (
                                            '{"tracks": ['
                                            '{"title": "Live Forever", '
                                            '"artist": "Oasis", '
                                            '"reason": "britpop"},'
                                            '{"title": "", "artist": "Skip"}'
                                            "]}"
                                        ),
                                    }
                                }
                            ]
                        }
                    }
                ]
            }

        generator = DeepSeekTrackGenerator("key", request_json=fake_request)

        candidates = generator.generate(
            intent="britpop", user_taste={}, count=5, exclude_artists=[]
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "Live Forever")
        self.assertEqual(candidates[0].reason, "britpop")

    def test_raises_on_malformed_response(self):
        def fake_request(payload):
            return {"choices": [{"message": {}}]}

        generator = DeepSeekTrackGenerator("key", request_json=fake_request)

        with self.assertRaises(TrackGeneratorError):
            generator.generate(
                intent="x", user_taste={}, count=5, exclude_artists=[]
            )

    def test_from_env_returns_none_without_key(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(DeepSeekTrackGenerator.from_env())


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from rateyourdj.providers import (
    CollectorMetadataProvider,
    ExternalMusicProvider,
    LastfmSimilarArtistsProvider,
    ProviderError,
    ProviderSearchResult,
    ProviderTrack,
    ProviderSimilarArtistsResult,
    SpotifySearchProvider,
    TrackQuery,
    configured_music_provider_from_env,
)


class FakeSpotifyCollector:
    def __init__(self) -> None:
        self.token_calls = 0

    def _access_token(self) -> str:
        self.token_calls += 1
        return "token"

    def collect_track(self, title, artist, album=None):
        return {
            "source": "Spotify",
            "spotify_track_id": "spotify-id",
            "title": title,
            "artists": [artist],
            "album": album or "Album",
            "release_year": 1995,
            "duration_ms": 210000,
        }


class FakeMusicBrainzCollector:
    def collect_recording(self, title, artist):
        return {
            "source": "MusicBrainz",
            "musicbrainz_recording_id": "mb-id",
            "title": title,
            "artists": [artist],
            "album": "Album",
            "release_year": 1995,
            "duration_ms": 211000,
            "score": 100,
        }


class FakeLastfmCollector:
    def collect_tags(self, title, artist):
        return {
            "source": "Last.fm",
            "track": title,
            "artist": artist,
            "track_tags": [
                {"name": "britpop", "count": 100},
                {"name": "rock", "count": 50},
            ],
            "artist_tags": [
                {"name": "britpop", "count": 80},
                {"name": "alternative", "count": 40},
            ],
        }


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
                    title="Song",
                    artist="Artist",
                )
            ],
        )


class FakeLastfmSimilarCollector:
    def collect_similar_artists(self, artist, *, limit=10):
        return {
            "source": "Last.fm",
            "artist": artist,
            "similar_artists": [
                {"name": "Pulp", "match": 0.92, "url": "https://last.fm/pulp"},
                {"name": "Suede", "match": 0.88, "url": "https://last.fm/suede"},
            ][:limit],
        }


class MusicProviderTests(unittest.TestCase):
    def test_spotify_search_provider_normalizes_tracks(self):
        requests = []

        def fake_request(url, *, headers=None):
            requests.append((url, headers))
            return {
                "tracks": {
                    "items": [
                        {
                            "id": "track-id",
                            "name": "Live Forever",
                            "artists": [
                                {
                                    "id": "artist-id",
                                    "name": "Oasis",
                                    "external_urls": {
                                        "spotify": "https://artist"
                                    },
                                }
                            ],
                            "album": {
                                "name": "Definitely Maybe",
                                "release_date": "1994-08-29",
                                "images": [{"url": "https://image"}],
                            },
                            "duration_ms": 276000,
                            "preview_url": None,
                            "external_urls": {"spotify": "https://track"},
                        }
                    ]
                }
            }

        provider = SpotifySearchProvider(
            FakeSpotifyCollector(),
            request=fake_request,
        )

        result = provider.search_tracks("britpop", limit=1, market="AU")

        self.assertEqual(result.provider, "spotify")
        self.assertEqual(result.tracks[0].track_id, "spotify:track:track-id")
        self.assertEqual(result.tracks[0].artist, "Oasis")
        self.assertEqual(result.tracks[0].release_year, 1994)
        self.assertIn("market=AU", requests[0][0])
        self.assertEqual(requests[0][1]["Authorization"], "Bearer token")

    def test_collector_metadata_provider_merges_sources_and_tags(self):
        provider = CollectorMetadataProvider(
            spotify=FakeSpotifyCollector(),
            musicbrainz=FakeMusicBrainzCollector(),
            lastfm=FakeLastfmCollector(),
        )

        track = provider.get_track_metadata(
            TrackQuery(title="Live Forever", artist="Oasis", album="Definitely Maybe")
        )

        self.assertEqual(track.track_id, "spotify:track:spotify-id")
        self.assertEqual(track.provider, "spotify")
        self.assertEqual(track.title, "Live Forever")
        self.assertEqual(track.tags["britpop"], 1.0)
        self.assertEqual(track.tags["rock"], 0.5)
        self.assertIn("musicbrainz", track.raw)
        self.assertIn("lastfm", track.raw)

    def test_collector_metadata_provider_reports_total_failure(self):
        provider = CollectorMetadataProvider()

        with self.assertRaises(ProviderError):
            provider.get_track_metadata(
                TrackQuery(title="Missing", artist="Nobody")
            )

    def test_external_music_provider_facade_delegates_search_and_metadata(self):
        metadata = CollectorMetadataProvider(spotify=FakeSpotifyCollector())
        provider = ExternalMusicProvider(
            search_providers=[FakeSearchProvider()],
            metadata_provider=metadata,
            similar_artists_provider=LastfmSimilarArtistsProvider(
                FakeLastfmSimilarCollector()
            ),
        )

        results = provider.search_tracks("rock")
        track = provider.get_track_metadata(
            TrackQuery(title="Song", artist="Artist")
        )
        similar_artists = provider.get_similar_artists("Oasis", limit=2)

        self.assertEqual(results[0].tracks[0].track_id, "fake:track:1")
        self.assertEqual(track.track_id, "spotify:track:spotify-id")
        self.assertIsInstance(similar_artists, ProviderSimilarArtistsResult)
        self.assertEqual(similar_artists.artists[0].name, "Pulp")

    def test_lastfm_similar_artists_provider_normalizes_results(self):
        provider = LastfmSimilarArtistsProvider(FakeLastfmSimilarCollector())

        result = provider.get_similar_artists("Oasis", limit=2)

        self.assertEqual(result.provider, "lastfm")
        self.assertEqual(result.artist, "Oasis")
        self.assertEqual(result.artists[0].name, "Pulp")
        self.assertAlmostEqual(result.artists[0].score, 0.92)
        self.assertEqual(
            result.artists[0].external_urls["lastfm"],
            "https://last.fm/pulp",
        )

    def test_external_music_provider_requires_configured_capability(self):
        provider = ExternalMusicProvider()

        with self.assertRaises(ProviderError):
            provider.search_tracks("rock")
        with self.assertRaises(ProviderError):
            provider.get_track_metadata(TrackQuery(title="Song", artist="Artist"))

    def test_configured_music_provider_from_env_requires_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(configured_music_provider_from_env())

    def test_configured_music_provider_from_env_builds_available_capabilities(self):
        with patch.dict(
            "os.environ",
            {
                "SPOTIFY_CLIENT_ID": "client",
                "SPOTIFY_CLIENT_SECRET": "secret",
                "LASTFM_API_KEY": "lastfm",
            },
            clear=True,
        ):
            provider = configured_music_provider_from_env()

        self.assertIsNotNone(provider)
        assert provider is not None
        self.assertEqual(len(provider.search_providers), 1)
        self.assertIsNotNone(provider.metadata_provider)
        self.assertIsNotNone(provider.similar_artists_provider)


if __name__ == "__main__":
    unittest.main()

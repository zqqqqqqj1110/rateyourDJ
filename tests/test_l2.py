import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rateyourdj.l2 import (
    EXTERNAL_ID_FIELDS,
    METADATA_FIELDS,
    SOURCE_TAG_FIELDS,
    GenreNormalizer,
    JsonSongStore,
    SongDataMerger,
    SongProfile,
    SongProfileService,
    SongValidationError,
    SourceMatchError,
    empty_song_dict,
    import_song_dictionary,
    merge_and_store_song,
    select_preferred_version,
    song_schema,
    validate_song_patch,
)


SPOTIFY_ORIGINAL = {
    "source": "Spotify",
    "spotify_track_id": "spotify-original",
    "title": "Wonderwall",
    "artists": ["Oasis"],
    "album": "(What's The Story) Morning Glory?",
    "release_year": 1995,
    "duration_ms": 258773,
}

SPOTIFY_REMASTERED = {
    **SPOTIFY_ORIGINAL,
    "spotify_track_id": "spotify-remastered",
    "title": "Wonderwall - Remastered",
    "album": "(What's The Story) Morning Glory? (Remastered)",
}

SPOTIFY_LIVE = {
    **SPOTIFY_ORIGINAL,
    "spotify_track_id": "spotify-live",
    "title": "Wonderwall - Live at Knebworth",
    "album": "Knebworth 1996",
}

MUSICBRAINZ_REMASTERED = {
    "source": "MusicBrainz",
    "musicbrainz_recording_id": "mb-remastered",
    "title": "Wonderwall (Remastered)",
    "artists": ["Oasis"],
    "album": "(What's The Story) Morning Glory? (Remastered)",
    "release_year": 1995,
    "duration_ms": 258700,
    "score": 100,
}

LASTFM_TAGS = {
    "source": "Last.fm",
    "track": "Wonderwall",
    "artist": "Oasis",
    "track_tags": [
        {"name": "britpop", "count": 100},
        {"name": "rock", "count": 100},
        {"name": "90s", "count": 50},
        {"name": "alternative", "count": 46},
        {"name": "oasis", "count": 18},
        {"name": "Love", "count": 1},
    ],
    "artist_tags": [
        {"name": "britpop", "count": 100},
        {"name": "rock", "count": 84},
        {"name": "british", "count": 46},
        {"name": "alternative rock", "count": 38},
        {"name": "Manchester", "count": 17},
    ],
}


class SongFrameworkTests(unittest.TestCase):
    def test_empty_song_contains_complete_slim_l2_framework(self) -> None:
        song = empty_song_dict("song-1")

        self.assertEqual(tuple(song["external_ids"]), EXTERNAL_ID_FIELDS)
        self.assertEqual(tuple(song["metadata"]), METADATA_FIELDS)
        self.assertEqual(tuple(song["source_tags"]), SOURCE_TAG_FIELDS)
        self.assertEqual(song["genres"], {})
        self.assertEqual(song["data_source"], {})
        self.assertIsNone(song["confidence_score"])
        self.assertNotIn("moods", song)
        self.assertNotIn("embedding", song)

    def test_schema_describes_only_current_l2_sections(self) -> None:
        schema = song_schema()

        self.assertEqual(
            set(schema),
            {
                "song_id",
                "external_ids",
                "metadata",
                "source_tags",
                "genres",
                "data_source",
                "confidence_score",
                "version",
                "updated_at",
            },
        )
        self.assertIn("version_type", schema["metadata"])

    def test_full_song_round_trip(self) -> None:
        original = SongProfile.empty("song-1")
        loaded = SongProfile.from_dict(original.to_dict())

        self.assertEqual(original.to_dict(), loaded.to_dict())


class SongValidationTests(unittest.TestCase):
    def test_accepts_partial_dictionary(self) -> None:
        patch = validate_song_patch(
            {
                "external_ids": {"spotify_track_id": "spotify-1"},
                "metadata": {
                    "title": "Wonderwall",
                    "artist": "Oasis",
                    "version_type": "remastered",
                },
                "source_tags": {
                    "lastfm_track_tags": {"britpop": 1.0, "rock": 0.8}
                },
                "genres": {"britpop": 1.0},
                "data_source": {"genres": ["Last.fm", "GenreNormalizer"]},
                "confidence_score": 0.85,
            }
        )

        self.assertEqual(patch["metadata"]["version_type"], "remastered")
        self.assertEqual(patch["genres"]["britpop"], 1.0)

    def test_rejects_removed_and_invalid_fields(self) -> None:
        invalid_patches = (
            {"aligned_features": {}},
            {"metadata": {"popularity": 90}},
            {"metadata": {"duration_ms": -1}},
            {"metadata": {"version_type": "studio"}},
            {"genres": {"rock": 1.1}},
            {"source_tags": {"lastfm_track_tags": ["rock"]}},
            {"confidence_score": -0.1},
            {"embedding": [0.1, 0.2]},
        )

        for patch in invalid_patches:
            with self.subTest(patch=patch):
                with self.assertRaises(SongValidationError):
                    validate_song_patch(patch)


class VersionMatchingTests(unittest.TestCase):
    def test_prefers_remastered_then_original_then_live(self) -> None:
        selected = select_preferred_version(
            [SPOTIFY_LIVE, SPOTIFY_ORIGINAL, SPOTIFY_REMASTERED]
        )
        self.assertEqual(selected["spotify_track_id"], "spotify-remastered")

        selected_without_remaster = select_preferred_version(
            [SPOTIFY_LIVE, SPOTIFY_ORIGINAL]
        )
        self.assertEqual(
            selected_without_remaster["spotify_track_id"], "spotify-original"
        )

    def test_cross_source_metadata_also_uses_version_priority(self) -> None:
        profile = SongDataMerger().merge(
            "wonderwall",
            spotify=SPOTIFY_LIVE,
            musicbrainz={
                **MUSICBRAINZ_REMASTERED,
                "title": "Wonderwall",
                "album": "(What's The Story) Morning Glory?",
            },
        )

        self.assertEqual(profile.metadata["version_type"], "original")
        self.assertEqual(profile.metadata["title"], "Wonderwall")


class GenreNormalizerTests(unittest.TestCase):
    def test_maps_genres_and_removes_non_genre_tags(self) -> None:
        genres = GenreNormalizer().normalize(
            LASTFM_TAGS["track_tags"],
            LASTFM_TAGS["artist_tags"],
            artist="Oasis",
        )

        self.assertEqual(genres["britpop"], 1.0)
        self.assertEqual(genres["rock"], 1.0)
        self.assertEqual(genres["alternative_rock"], 0.46)
        self.assertNotIn("90s", genres)
        self.assertNotIn("british", genres)
        self.assertNotIn("oasis", genres)
        self.assertNotIn("love", genres)


class SongMergerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.service = SongProfileService(JsonSongStore(self.root))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_merges_three_sources_and_persists_profile(self) -> None:
        profile = self.service.merge_and_save_sources(
            "wonderwall-oasis",
            spotify=[SPOTIFY_LIVE, SPOTIFY_ORIGINAL, SPOTIFY_REMASTERED],
            musicbrainz=[MUSICBRAINZ_REMASTERED],
            lastfm=LASTFM_TAGS,
        )
        stored_path = self.root / "wonderwall-oasis.json"
        stored = json.loads(stored_path.read_text(encoding="utf-8"))

        self.assertTrue(stored_path.exists())
        self.assertEqual(
            profile.external_ids["spotify_track_id"], "spotify-remastered"
        )
        self.assertEqual(profile.metadata["version_type"], "remastered")
        self.assertEqual(profile.genres["britpop"], 1.0)
        self.assertEqual(profile.to_dict(), stored)
        self.assertGreaterEqual(profile.confidence_score, 0.9)
        self.assertEqual(
            profile.data_source["external_ids.spotify_track_id"], ["Spotify"]
        )

    def test_recollecting_a_song_increments_the_profile_version(self) -> None:
        first = self.service.merge_and_save_sources(
            "wonderwall-oasis",
            spotify=SPOTIFY_ORIGINAL,
        )
        second = self.service.merge_and_save_sources(
            "wonderwall-oasis",
            spotify=SPOTIFY_REMASTERED,
        )

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(
            self.service.get_song_profile("wonderwall-oasis").version,
            2,
        )

    def test_functional_merge_api_writes_requested_directory(self) -> None:
        profile = merge_and_store_song(
            "wonderwall",
            spotify=SPOTIFY_ORIGINAL,
            lastfm=LASTFM_TAGS,
            data_dir=self.root,
        )

        self.assertEqual(profile.metadata["title"], "Wonderwall")
        self.assertTrue((self.root / "wonderwall.json").exists())

    def test_rejects_cross_source_identity_mismatch(self) -> None:
        wrong_musicbrainz = {
            **MUSICBRAINZ_REMASTERED,
            "title": "Don't Look Back in Anger",
        }
        with self.assertRaises(SourceMatchError):
            SongDataMerger().merge(
                "wrong",
                spotify=SPOTIFY_REMASTERED,
                musicbrainz=wrong_musicbrainz,
            )

    def test_import_patch_remains_available(self) -> None:
        profile = import_song_dictionary(
            "song-2",
            {
                "metadata": {"title": "Song Title", "artist": "Artist"},
                "genres": {"rock": 0.8},
            },
            self.root,
        )

        self.assertEqual(profile.metadata["title"], "Song Title")
        self.assertEqual(profile.genres["rock"], 0.8)

    def test_rejects_unsafe_song_id(self) -> None:
        with self.assertRaises(ValueError):
            self.service.get_song_profile("../other")


class L2CliTests(unittest.TestCase):
    def test_schema_command_prints_current_framework(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "rateyourdj.l2.cli", "schema"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertIn("external_ids", output)
        self.assertIn("genres", output)
        self.assertNotIn("aligned_features", output)


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rateyourdj.l2 import (
    ALIGNED_FEATURE_FIELDS,
    METADATA_FIELDS,
    JsonSongStore,
    SongProfile,
    SongProfileService,
    SongValidationError,
    empty_song_dict,
    import_song_dictionary,
    song_schema,
    validate_song_patch,
)


class SongFrameworkTests(unittest.TestCase):
    def test_empty_song_contains_complete_l2_framework(self) -> None:
        song = empty_song_dict("song-1")

        self.assertEqual(tuple(song["metadata"]), METADATA_FIELDS)
        self.assertEqual(
            tuple(song["aligned_features"]), ALIGNED_FEATURE_FIELDS
        )
        self.assertEqual(song["avoid_tags"], {})
        self.assertEqual(song["semantic_tags"], {})
        self.assertEqual(song["source_tags"], {})
        self.assertEqual(song["data_source"], {})
        self.assertIsNone(song["confidence_score"])
        self.assertEqual(song["embedding_text"], "")
        self.assertEqual(song["embedding"], [])

    def test_schema_describes_all_l2_sections(self) -> None:
        schema = song_schema()

        self.assertEqual(
            set(schema),
            {
                "metadata",
                "aligned_features",
                "avoid_tags",
                "semantic_tags",
                "source_tags",
                "data_source",
                "confidence_score",
                "embedding_text",
                "embedding",
            },
        )
        self.assertEqual(
            set(schema["aligned_features"]), set(ALIGNED_FEATURE_FIELDS)
        )

    def test_full_song_round_trip(self) -> None:
        original = SongProfile.empty("song-1")
        loaded = SongProfile.from_dict(original.to_dict())

        self.assertEqual(original.to_dict(), loaded.to_dict())


class SongValidationTests(unittest.TestCase):
    def test_accepts_partial_dictionary_from_collectors(self) -> None:
        patch = validate_song_patch(
            {
                "metadata": {
                    "title": "Wonderwall",
                    "artist": "Oasis",
                    "release_year": 1995,
                    "duration_ms": 258000,
                    "popularity": 0.9,
                },
                "aligned_features": {
                    "genres": {"britpop": 0.9},
                    "moods": {"nostalgic": 0.8},
                    "tempo": {"medium": 0.8},
                },
                "avoid_tags": {"too_noisy": 0.2},
                "semantic_tags": {"warm": 0.7},
                "source_tags": {"lastfm": ["britpop", "rock"]},
                "data_source": {
                    "metadata": ["MusicBrainz"],
                    "genres": ["Last.fm"],
                },
                "confidence_score": 0.85,
                "embedding_text": "A warm Britpop song.",
                "embedding": [0.1, -0.2, 0.3],
            }
        )

        self.assertEqual(patch["metadata"]["title"], "Wonderwall")
        self.assertEqual(
            patch["aligned_features"]["genres"]["britpop"], 0.9
        )
        self.assertEqual(patch["embedding"], [0.1, -0.2, 0.3])

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaises(SongValidationError):
            validate_song_patch({"unknown_section": {}})

        with self.assertRaises(SongValidationError):
            validate_song_patch({"metadata": {"unknown_field": "value"}})

        with self.assertRaises(SongValidationError):
            validate_song_patch(
                {"aligned_features": {"unknown_field": {}}}
            )

    def test_rejects_invalid_scores_and_types(self) -> None:
        invalid_patches = (
            {"metadata": {"duration_ms": -1}},
            {"metadata": {"release_year": "1995"}},
            {"metadata": {"popularity": 101}},
            {"aligned_features": {"genres": {"rock": 1.1}}},
            {"source_tags": {"lastfm": "rock"}},
            {"confidence_score": -0.1},
            {"embedding": [0.1, "invalid"]},
        )

        for patch in invalid_patches:
            with self.subTest(patch=patch):
                with self.assertRaises(SongValidationError):
                    validate_song_patch(patch)


class SongMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = JsonSongStore(self.root)
        self.service = SongProfileService(self.store)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_init_persists_complete_framework(self) -> None:
        profile = self.service.get_song_profile("song-1")
        stored = json.loads(
            (self.root / "song-1.json").read_text(encoding="utf-8")
        )

        self.assertEqual(profile.to_dict(), stored)
        self.assertEqual(
            set(stored["aligned_features"]), set(ALIGNED_FEATURE_FIELDS)
        )

    def test_import_merges_features_and_replaces_scalar_fields(self) -> None:
        self.service.import_song_patch(
            "song-1",
            {
                "metadata": {"title": "Wonderwall", "artist": "Oasis"},
                "aligned_features": {
                    "genres": {"britpop": 0.9},
                    "moods": {"warm": 0.6},
                },
                "source_tags": {"lastfm": ["britpop"]},
                "embedding": [0.1, 0.2],
            },
        )
        updated = self.service.import_song_patch(
            "song-1",
            {
                "metadata": {"album": "Morning Glory"},
                "aligned_features": {
                    "genres": {"alternative_rock": 0.7}
                },
                "source_tags": {"lastfm": ["britpop", "rock"]},
                "confidence_score": 0.8,
                "embedding": [0.3, 0.4],
            },
        )

        self.assertEqual(updated.metadata["title"], "Wonderwall")
        self.assertEqual(updated.metadata["album"], "Morning Glory")
        self.assertEqual(
            updated.aligned_features["genres"],
            {"britpop": 0.9, "alternative_rock": 0.7},
        )
        self.assertEqual(updated.aligned_features["moods"], {"warm": 0.6})
        self.assertEqual(
            updated.source_tags["lastfm"], ["britpop", "rock"]
        )
        self.assertEqual(updated.confidence_score, 0.8)
        self.assertEqual(updated.embedding, [0.3, 0.4])

    def test_functional_import_api(self) -> None:
        profile = import_song_dictionary(
            "song-2",
            {"metadata": {"title": "Song Title"}},
            self.root,
        )

        self.assertEqual(profile.metadata["title"], "Song Title")

    def test_rejects_unsafe_song_id(self) -> None:
        with self.assertRaises(ValueError):
            self.service.get_song_profile("../other")


class L2CliTests(unittest.TestCase):
    def test_schema_command_prints_framework(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "rateyourdj.l2.cli", "schema"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertIn("metadata", output)
        self.assertIn("aligned_features", output)
        self.assertIn("embedding", output)


if __name__ == "__main__":
    unittest.main()

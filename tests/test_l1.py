import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from rateyourdj.l1 import (
    PREFERENCE_FIELDS,
    JsonProfileStore,
    ProfileValidationError,
    UserProfile,
    UserProfileService,
    empty_profile_dict,
    import_profile_dictionary,
    profile_schema,
    validate_profile_patch,
)


class ProfileFrameworkTests(unittest.TestCase):
    def test_empty_profile_matches_collection_schema(self) -> None:
        profile = empty_profile_dict("user-1")

        self.assertEqual(profile["collection_song_ids"], [])
        for field_name in PREFERENCE_FIELDS:
            self.assertEqual(profile[field_name], {})
        self.assertEqual(profile["feedback_memory"], [])
        self.assertEqual(profile["version"], 1)

    def test_schema_describes_all_importable_fields(self) -> None:
        schema = profile_schema()

        self.assertEqual(
            set(schema),
            {
                "collection_song_ids",
                "artist_preferences",
                "genre_preferences",
                "tag_preferences",
                "feedback_memory",
            },
        )

    def test_full_profile_round_trip(self) -> None:
        original = UserProfile.empty("user-1")
        loaded = UserProfile.from_dict(original.to_dict())

        self.assertEqual(original.to_dict(), loaded.to_dict())


class DictionaryValidationTests(unittest.TestCase):
    def test_accepts_collection_profile_patch(self) -> None:
        patch = validate_profile_patch(
            {
                "collection_song_ids": ["song-1", "song-2", "song-1"],
                "artist_preferences": {"Oasis": 0.8},
                "genre_preferences": {"britpop": 0.9},
                "tag_preferences": {"rock": 0.7},
                "feedback_memory": [
                    {
                        "feedback_type": "favorite",
                        "song_id": "song-3",
                        "timestamp": "2026-06-10T00:00:00+00:00",
                        "reward_score": 0.8,
                    }
                ],
            }
        )

        self.assertEqual(
            patch["collection_song_ids"], ["song-1", "song-2"]
        )
        self.assertEqual(patch["artist_preferences"], {"Oasis": 0.8})
        self.assertEqual(patch["genre_preferences"], {"britpop": 0.9})
        self.assertEqual(patch["tag_preferences"], {"rock": 0.7})

    def test_rejects_old_and_unknown_fields(self) -> None:
        invalid_patches = (
            {"long_term_preference": {}},
            {"short_term_intent": {}},
            {"negative_preference": {}},
            {"unknown_section": {}},
        )

        for patch in invalid_patches:
            with self.subTest(patch=patch):
                with self.assertRaises(ProfileValidationError):
                    validate_profile_patch(patch)

    def test_rejects_invalid_song_ids_weights_and_feedback(self) -> None:
        invalid_patches = (
            {"collection_song_ids": "song-1"},
            {"collection_song_ids": [""]},
            {"artist_preferences": {"Oasis": 1.2}},
            {"genre_preferences": {"rock": True}},
            {"tag_preferences": {"": 0.5}},
            {"feedback_memory": [{"unknown_field": "value"}]},
            {"feedback_memory": [{}]},
            {"feedback_memory": [{"feedback_type": "unknown"}]},
        )

        for patch in invalid_patches:
            with self.subTest(patch=patch):
                with self.assertRaises(ProfileValidationError):
                    validate_profile_patch(patch)


class ProfileMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = JsonProfileStore(self.root)
        self.service = UserProfileService(self.store)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_init_persists_complete_framework(self) -> None:
        profile = self.service.get_user_profile("user-1")
        stored = json.loads(
            (self.root / "user-1.json").read_text(encoding="utf-8")
        )

        self.assertEqual(profile.to_dict(), stored)
        self.assertEqual(stored["collection_song_ids"], [])
        self.assertEqual(stored["genre_preferences"], {})

    def test_existing_legacy_profile_is_migrated_and_persisted(self) -> None:
        legacy = {
            "user_id": "legacy-user",
            "long_term_preference": {"genres": {"rock": 0.8}},
            "short_term_intent": {"genres": ["rock"]},
            "negative_preference": {"genres": {}},
            "feedback_memory": [],
            "version": 1,
            "updated_at": "2026-06-09T00:00:00+00:00",
        }
        (self.root / "legacy-user.json").write_text(
            json.dumps(legacy),
            encoding="utf-8",
        )

        profile = self.service.get_user_profile("legacy-user")
        stored = json.loads(
            (self.root / "legacy-user.json").read_text(encoding="utf-8")
        )

        self.assertEqual(profile.collection_song_ids, [])
        self.assertEqual(profile.artist_preferences, {})
        self.assertEqual(profile.genre_preferences, {})
        self.assertEqual(profile.tag_preferences, {})
        self.assertEqual(profile.version, 2)
        self.assertNotIn("long_term_preference", stored)
        self.assertEqual(stored, profile.to_dict())

    def test_import_merges_collection_preferences_and_feedback(self) -> None:
        self.service.import_profile_patch(
            "user-1",
            {
                "collection_song_ids": ["song-1", "song-2"],
                "artist_preferences": {"Oasis": 0.8},
                "genre_preferences": {"rock": 0.4},
                "tag_preferences": {"britpop": 0.9},
                "feedback_memory": [
                    {
                        "feedback_type": "play",
                        "song_id": "song-3",
                        "timestamp": "2026-06-10T00:00:00+00:00",
                        "reward_score": 0.1,
                    }
                ],
            },
        )
        updated = self.service.import_profile_patch(
            "user-1",
            {
                "collection_song_ids": ["song-2", "song-4"],
                "artist_preferences": {"Blur": 0.6},
                "genre_preferences": {
                    "rock": 0.5,
                    "alternative": 0.7,
                },
                "tag_preferences": {"90s": 0.4},
                "feedback_memory": [
                    {
                        "feedback_type": "favorite",
                        "song_id": "song-4",
                        "timestamp": "2026-06-10T00:01:00+00:00",
                        "reward_score": 0.8,
                    }
                ],
            },
        )

        self.assertEqual(
            updated.collection_song_ids,
            ["song-1", "song-2", "song-4"],
        )
        self.assertEqual(
            updated.artist_preferences, {"Oasis": 0.8, "Blur": 0.6}
        )
        self.assertEqual(
            updated.genre_preferences,
            {"rock": 0.5, "alternative": 0.7},
        )
        self.assertEqual(
            updated.tag_preferences, {"britpop": 0.9, "90s": 0.4}
        )
        self.assertEqual(len(updated.feedback_memory), 2)

    def test_concurrent_feedback_imports_do_not_overwrite_each_other(self) -> None:
        errors: list[BaseException] = []

        def record_feedback(index: int) -> None:
            try:
                service = UserProfileService(JsonProfileStore(self.root))
                service.import_profile_patch(
                    "concurrent-user",
                    {
                        "feedback_memory": [
                            {
                                "feedback_type": "like",
                                "song_id": f"song-{index}",
                                "timestamp": "2026-06-11T00:00:00+00:00",
                                "reward_score": 0.6,
                            }
                        ]
                    },
                )
            except BaseException as error:
                errors.append(error)

        threads = [
            threading.Thread(target=record_feedback, args=(index,))
            for index in range(20)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        profile = self.store.load("concurrent-user")
        self.assertEqual(errors, [])
        self.assertEqual(len(profile.feedback_memory), 20)
        self.assertEqual(
            {record["song_id"] for record in profile.feedback_memory},
            {f"song-{index}" for index in range(20)},
        )
        self.assertEqual(profile.version, 21)

    def test_replace_requires_complete_profile_data(self) -> None:
        with self.assertRaises(ValueError):
            self.service.replace_profile_data(
                "user-1", {"collection_song_ids": []}
            )

    def test_collection_rebuild_can_preserve_newer_feedback(self) -> None:
        stale_data = {
            "collection_song_ids": ["song-1"],
            "artist_preferences": {"Artist": 1.0},
            "genre_preferences": {"rock": 1.0},
            "tag_preferences": {"rock": 1.0},
            "feedback_memory": [],
        }
        self.service.import_profile_patch(
            "user-1",
            {
                "feedback_memory": [
                    {
                        "feedback_type": "like",
                        "song_id": "song-2",
                        "timestamp": "2026-06-11T00:00:00+00:00",
                        "reward_score": 0.6,
                    }
                ]
            },
        )

        rebuilt = self.service.replace_profile_data(
            "user-1",
            stale_data,
            preserve_feedback=True,
        )

        self.assertEqual(len(rebuilt.feedback_memory), 1)
        self.assertEqual(rebuilt.feedback_memory[0]["song_id"], "song-2")

    def test_functional_import_api(self) -> None:
        profile = import_profile_dictionary(
            "user-2",
            {
                "collection_song_ids": ["song-10"],
                "genre_preferences": {"jazz": 0.7},
            },
            self.root,
        )

        self.assertEqual(profile.collection_song_ids, ["song-10"])
        self.assertEqual(profile.genre_preferences, {"jazz": 0.7})

    def test_rejects_unsafe_user_id(self) -> None:
        with self.assertRaises(ValueError):
            self.service.get_user_profile("../other")


class CliTests(unittest.TestCase):
    def test_schema_command_prints_collection_framework(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "rateyourdj.l1.cli", "schema"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertIn("collection_song_ids", output)
        self.assertIn("artist_preferences", output)
        self.assertIn("genre_preferences", output)
        self.assertIn("tag_preferences", output)
        self.assertIn("feedback_memory", output)


if __name__ == "__main__":
    unittest.main()

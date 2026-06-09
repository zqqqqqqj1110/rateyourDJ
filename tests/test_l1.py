import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rateyourdj.l1 import (
    LONG_TERM_FIELDS,
    NEGATIVE_FIELDS,
    SHORT_TERM_LIST_FIELDS,
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
    def test_empty_profile_contains_complete_l1_framework(self) -> None:
        profile = empty_profile_dict("user-1")

        self.assertEqual(
            tuple(profile["long_term_preference"]), LONG_TERM_FIELDS
        )
        self.assertEqual(
            tuple(profile["negative_preference"]), NEGATIVE_FIELDS
        )
        for field_name in SHORT_TERM_LIST_FIELDS:
            self.assertEqual(profile["short_term_intent"][field_name], [])
        self.assertEqual(
            profile["short_term_intent"]["exploration_level"], "balanced"
        )
        self.assertEqual(profile["feedback_memory"], [])

    def test_schema_describes_all_migration_sections(self) -> None:
        schema = profile_schema()

        self.assertEqual(
            set(schema),
            {
                "long_term_preference",
                "short_term_intent",
                "negative_preference",
                "feedback_memory",
            },
        )
        self.assertEqual(
            set(schema["long_term_preference"]), set(LONG_TERM_FIELDS)
        )

    def test_full_profile_round_trip(self) -> None:
        original = UserProfile.empty("user-1")
        loaded = UserProfile.from_dict(original.to_dict())

        self.assertEqual(original.to_dict(), loaded.to_dict())


class DictionaryValidationTests(unittest.TestCase):
    def test_accepts_partial_dictionary_from_later_modules(self) -> None:
        patch = validate_profile_patch(
            {
                "long_term_preference": {
                    "genres": {"britpop": 0.8},
                    "artists": {"Oasis": 0.9},
                },
                "short_term_intent": {
                    "reference_songs": ["Wonderwall"],
                    "exploration_level": "safe",
                },
                "negative_preference": {
                    "sound_textures": {"noisy": 0.7}
                },
                "feedback_memory": [
                    {
                        "feedback_type": "favorite",
                        "song_id": "song-1",
                        "query": "current query",
                        "timestamp": "2026-06-09T00:00:00+00:00",
                        "song_tags": {"genres": ["britpop"]},
                        "reward_score": 0.8,
                    }
                ],
            }
        )

        self.assertEqual(
            patch["long_term_preference"]["genres"]["britpop"], 0.8
        )
        self.assertEqual(
            patch["short_term_intent"]["reference_songs"], ["Wonderwall"]
        )

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ProfileValidationError):
            validate_profile_patch({"unknown_section": {}})

        with self.assertRaises(ProfileValidationError):
            validate_profile_patch(
                {"short_term_intent": {"unknown_field": []}}
            )

    def test_rejects_invalid_weights_and_types(self) -> None:
        with self.assertRaises(ProfileValidationError):
            validate_profile_patch(
                {"long_term_preference": {"genres": {"rock": 1.2}}}
            )

        with self.assertRaises(ProfileValidationError):
            validate_profile_patch(
                {"short_term_intent": {"genres": "rock"}}
            )

        with self.assertRaises(ProfileValidationError):
            validate_profile_patch(
                {
                    "short_term_intent": {
                        "exploration_level": "unknown"
                    }
                }
            )

        with self.assertRaises(ProfileValidationError):
            validate_profile_patch(
                {"feedback_memory": [{"unknown_field": "value"}]}
            )


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
        self.assertEqual(
            set(stored["long_term_preference"]), set(LONG_TERM_FIELDS)
        )

    def test_import_merges_weights_replaces_intent_fields_and_appends_memory(
        self,
    ) -> None:
        self.service.import_profile_patch(
            "user-1",
            {
                "long_term_preference": {
                    "genres": {"rock": 0.4},
                    "artists": {"Oasis": 0.8},
                },
                "short_term_intent": {"genres": ["rock"]},
                "feedback_memory": [
                    {
                        "feedback_type": "play",
                        "song_id": "song-1",
                        "timestamp": "2026-06-09T00:00:00+00:00",
                    }
                ],
            },
        )
        updated = self.service.import_profile_patch(
            "user-1",
            {
                "long_term_preference": {
                    "genres": {"britpop": 0.9}
                },
                "short_term_intent": {"genres": ["britpop"]},
                "negative_preference": {
                    "sound_textures": {"noisy": 0.6}
                },
                "feedback_memory": [
                    {
                        "feedback_type": "favorite",
                        "song_id": "song-2",
                        "timestamp": "2026-06-09T00:01:00+00:00",
                        "reward_score": 0.8,
                    }
                ],
            },
        )

        self.assertEqual(
            updated.long_term_preference["genres"],
            {"rock": 0.4, "britpop": 0.9},
        )
        self.assertEqual(updated.long_term_preference["artists"], {"Oasis": 0.8})
        self.assertEqual(updated.short_term_intent["genres"], ["britpop"])
        self.assertEqual(
            updated.negative_preference["sound_textures"], {"noisy": 0.6}
        )
        self.assertEqual(
            updated.feedback_memory,
            [
                {
                    "feedback_type": "play",
                    "song_id": "song-1",
                    "timestamp": "2026-06-09T00:00:00+00:00",
                },
                {
                    "feedback_type": "favorite",
                    "song_id": "song-2",
                    "timestamp": "2026-06-09T00:01:00+00:00",
                    "reward_score": 0.8,
                },
            ],
        )

    def test_functional_import_api(self) -> None:
        profile = import_profile_dictionary(
            "user-2",
            {"short_term_intent": {"languages": ["English"]}},
            self.root,
        )

        self.assertEqual(
            profile.short_term_intent["languages"], ["English"]
        )

    def test_rejects_unsafe_user_id(self) -> None:
        with self.assertRaises(ValueError):
            self.service.get_user_profile("../other")


class CliTests(unittest.TestCase):
    def test_schema_command_prints_framework_without_example_input(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "rateyourdj.l1.cli", "schema"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        output = json.loads(result.stdout)

        self.assertIn("long_term_preference", output)
        self.assertIn("short_term_intent", output)
        self.assertIn("negative_preference", output)
        self.assertIn("feedback_memory", output)


if __name__ == "__main__":
    unittest.main()

"""Tests for the zero-dependency .env loader."""

import os
import tempfile
import unittest
from pathlib import Path

from rateyourdj.config import load_dotenv


class LoadDotenvTests(unittest.TestCase):
    def _write(self, content: str) -> Path:
        directory = tempfile.mkdtemp()
        path = Path(directory) / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_simple_keys(self):
        path = self._write("FOO=bar\nBAZ=qux\n")
        os.environ.pop("FOO", None)
        os.environ.pop("BAZ", None)

        loaded = load_dotenv(path)

        self.assertEqual(loaded["FOO"], "bar")
        self.assertEqual(os.environ["FOO"], "bar")
        self.assertEqual(os.environ["BAZ"], "qux")
        os.environ.pop("FOO", None)
        os.environ.pop("BAZ", None)

    def test_strips_quotes_comments_and_export(self):
        path = self._write(
            '# a comment\n'
            "\n"
            'export TOKEN="secret value"\n'
            "EMPTY=\n"
            "PLAIN='single'\n"
        )
        for key in ("TOKEN", "EMPTY", "PLAIN"):
            os.environ.pop(key, None)

        load_dotenv(path)

        self.assertEqual(os.environ["TOKEN"], "secret value")
        self.assertEqual(os.environ["EMPTY"], "")
        self.assertEqual(os.environ["PLAIN"], "single")
        for key in ("TOKEN", "EMPTY", "PLAIN"):
            os.environ.pop(key, None)

    def test_does_not_override_existing_by_default(self):
        path = self._write("PRESET=from_file\n")
        os.environ["PRESET"] = "from_env"

        load_dotenv(path)

        self.assertEqual(os.environ["PRESET"], "from_env")
        os.environ.pop("PRESET", None)

    def test_override_replaces_existing(self):
        path = self._write("PRESET=from_file\n")
        os.environ["PRESET"] = "from_env"

        load_dotenv(path, override=True)

        self.assertEqual(os.environ["PRESET"], "from_file")
        os.environ.pop("PRESET", None)

    def test_missing_file_returns_empty(self):
        result = load_dotenv(Path(tempfile.mkdtemp()) / "nope.env")

        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from rateyourdj.experiment import (
    MANIFEST_SCHEMA_VERSION,
    build_run_manifest,
    file_sha256,
    write_run_manifest,
)


class RunManifestTest(unittest.TestCase):
    def test_manifest_fingerprints_data_and_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "queries.jsonl"
            data.write_text('{"q": 1}\n', encoding="utf-8")
            manifest = build_run_manifest(
                "run-1",
                kind="baseline",
                config={"top_k": 10},
                data_files=[data, Path(tmp) / "missing.jsonl"],
                model={"provider": "deepseek"},
                seed=7,
                metrics={"n": 1},
            )
            self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertEqual(manifest["data"][str(data)], file_sha256(data))
            self.assertIsNone(manifest["data"][str(Path(tmp) / "missing.jsonl")])
            self.assertIn("commit", manifest["git"])
            path = write_run_manifest(Path(tmp) / "run", manifest)
            self.assertEqual(json.loads(path.read_text("utf-8"))["run_id"], "run-1")


if __name__ == "__main__":
    unittest.main()

"""Dense index: embeddings.npy + ids.json + docs.jsonl + manifest.json.

Encoding runs in chunks saved under ``parts/`` so an interrupted build resumes.
Search is exact cosine similarity with numpy (14k x 1024 floats ~ 57 MB), which
is simpler and more reproducible than an approximate index at this size.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .documents import DOC_VERSION, documents_digest, song_document
from .encoder import Encoder

INDEX_ROOT = Path("data/index")
CHUNK = 1024


@dataclass
class VectorIndex:
    ids: list[str]
    matrix: np.ndarray            # (n, dim), L2-normalised float32
    manifest: dict[str, Any]

    def __post_init__(self) -> None:
        self.row = {song_id: i for i, song_id in enumerate(self.ids)}

    @property
    def version(self) -> str:
        return self.manifest["index_version"]

    def vector(self, song_id: str) -> np.ndarray | None:
        i = self.row.get(song_id)
        return None if i is None else self.matrix[i]


def index_version(catalog_version: str, model_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model_name.lower()).strip("-")
    return f"idx-{catalog_version}-{slug}-{DOC_VERSION}"


def build_index(songs: list[dict[str, Any]], encoder: Encoder, *, catalog_version: str,
                root: str | Path = INDEX_ROOT, batch_size: int = 32, log=print) -> VectorIndex:
    docs = [(s["song_id"], song_document(s)) for s in songs]
    version = index_version(catalog_version, encoder.name)
    out = Path(root) / version
    parts = out / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    digest = documents_digest(docs)
    marker = parts / "docs_sha256.txt"
    if marker.is_file() and marker.read_text().strip() != digest:
        for stale in parts.glob("part-*.npy"):
            stale.unlink()
    marker.write_text(digest)

    chunks = []
    for start in range(0, len(docs), CHUNK):
        path = parts / f"part-{start // CHUNK:04d}.npy"
        texts = [text for _, text in docs[start : start + CHUNK]]
        if path.is_file():
            part = np.load(path)
            if part.shape[0] == len(texts):
                chunks.append(part)
                continue
        log(f"[index] encoding {start + 1}-{start + len(texts)} / {len(docs)}")
        part = encoder.encode(texts, batch_size=batch_size)
        np.save(path, part)
        chunks.append(part)

    matrix = np.vstack(chunks).astype(np.float32) if chunks else np.zeros((0, encoder.dim), np.float32)
    np.save(out / "embeddings.npy", matrix)
    (out / "ids.json").write_text(json.dumps([sid for sid, _ in docs]) + "\n", "utf-8")
    with (out / "docs.jsonl").open("w", encoding="utf-8") as handle:
        for song_id, text in docs:
            handle.write(json.dumps({"song_id": song_id, "text": text}, ensure_ascii=False) + "\n")
    manifest = {
        "index_version": version, "model": encoder.name, "dim": int(matrix.shape[1]),
        "doc_version": DOC_VERSION, "catalog_version": catalog_version, "count": len(docs),
        "docs_sha256": digest, "similarity": "cosine (exact, numpy)",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    (Path(root) / "LATEST").write_text(version + "\n", "utf-8")
    log(f"[index] {len(docs)} songs -> {out}")
    return VectorIndex([sid for sid, _ in docs], matrix, manifest)


def load_index(root: str | Path = INDEX_ROOT, version: str | None = None) -> VectorIndex | None:
    root = Path(root)
    if version is None:
        latest = root / "LATEST"
        if not latest.is_file():
            return None
        version = latest.read_text().strip()
    out = root / version
    if not (out / "embeddings.npy").is_file():
        return None
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    ids = json.loads((out / "ids.json").read_text("utf-8"))
    return VectorIndex(ids, np.load(out / "embeddings.npy"), manifest)

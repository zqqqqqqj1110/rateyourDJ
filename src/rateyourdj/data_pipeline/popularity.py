"""Global popularity reference and bucket assignment (current version: see BUCKET_VERSION).

ListenBrainz exposes per-recording listener counts but no global percentile,
so the global distribution is *estimated*:

1. reservoir-sample recording MBIDs from the MusicBrainz core dump
   (``mbdump.tar.bz2`` -> ``mbdump/recording``, streamed, never fully extracted);
2. look up their ListenBrainz listener counts;
3. population = sampled recordings with >= 1 listener (videos excluded);
4. a song's global_percentile is its mid-rank in that empirical distribution.
"""

from __future__ import annotations

import bisect
import json
import random
import tarfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from rateyourdj.contracts import BUCKET_THRESHOLDS, BUCKET_VERSION, assign_bucket

from .catalog import read_jsonl, write_jsonl
from .sources import ListenBrainzClient

RECORDING_MEMBER = "mbdump/recording"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iter_recording_rows(path: str | Path, log=print) -> Iterator[list[str]]:
    """Yield tab-split rows of the MB ``recording`` table.

    ``path`` may be the full ``mbdump.tar.bz2`` (streamed) or an already
    extracted ``recording`` file.
    """
    path = Path(path)
    if path.name.endswith((".tar.bz2", ".tbz2", ".tar")):
        mode = "r|bz2" if path.name.endswith(("bz2", "tbz2")) else "r|"
        with tarfile.open(path, mode) as archive:
            started = time.monotonic()
            for member in archive:
                if not member.name.endswith(RECORDING_MEMBER):
                    if member.size > 50_000_000:
                        log(f"[sample] skipping {member.name} ({member.size / 1e9:.1f} GB), "
                            f"{(time.monotonic() - started) / 60:.1f} min elapsed")
                    continue
                if member.name.endswith(RECORDING_MEMBER):
                    log(f"[sample] reading {member.name} ({member.size / 1e9:.1f} GB)")
                    handle = archive.extractfile(member)
                    if handle is None:
                        break
                    for raw in iter(handle.readline, b""):
                        yield raw.decode("utf-8", "replace").rstrip("\n").split("\t")
                    return
        raise FileNotFoundError(f"{RECORDING_MEMBER} not found in {path}")
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            yield raw.rstrip("\n").split("\t")


def reservoir_sample_mbids(rows: Iterable[list[str]], k: int, *, seed: int = 20260926,
                           progress_every: int = 5_000_000, log=print) -> tuple[list[str], int]:
    """Uniform sample of k recording gids (column 1), skipping videos (column 8 == 't')."""
    rng = random.Random(seed)
    sample: list[str] = []
    seen = 0
    for row in rows:
        if len(row) < 2 or (len(row) > 8 and row[8] == "t"):
            continue
        seen += 1
        if len(sample) < k:
            sample.append(row[1])
        else:
            j = rng.randrange(seen)
            if j < k:
                sample[j] = row[1]
        if progress_every and seen % progress_every == 0:
            log(f"[sample] scanned {seen:,} recordings")
    return sample, seen


def build_reference(sample_mbids: list[str], lb: ListenBrainzClient, *, total_recordings: int,
                    dump_label: str, log=print) -> dict[str, Any]:
    counts = lb.recording_popularity(sample_mbids)
    listeners = sorted(int(v["listener_count"]) for v in counts.values()
                       if v.get("listener_count") and int(v["listener_count"]) >= 1)
    if not listeners:
        raise RuntimeError("no sampled recording has listeners; check the ListenBrainz responses")
    reference = {
        "bucket_version": BUCKET_VERSION,
        "metric": "listener_count",
        "population": "MusicBrainz recordings with >= 1 ListenBrainz listener (videos excluded)",
        "dump": dump_label,
        "total_recordings_scanned": total_recordings,
        "sample_size": len(sample_mbids),
        "population_sample_size": len(listeners),
        "population_share": round(len(listeners) / len(sample_mbids), 6),
        "thresholds": {
            "head_min_listeners": _quantile(listeners, BUCKET_THRESHOLDS["head"]),
            "mid_min_listeners": _quantile(listeners, BUCKET_THRESHOLDS["mid"]),
        },
        "quantiles": {f"p{q}": _quantile(listeners, q / 100) for q in (10, 25, 50, 75, 90, 95, 99)},
        "sorted_listener_counts": listeners,
        "created_at": _now(),
    }
    log(f"[reference] {len(listeners):,}/{len(sample_mbids):,} sampled recordings have listeners; "
        f"thresholds {reference['thresholds']}")
    return reference


def _quantile(sorted_values: list[int], q: float) -> int:
    index = min(len(sorted_values) - 1, max(0, int(q * len(sorted_values))))
    return sorted_values[index]


def global_percentile(listeners: int | None, sorted_counts: list[int]) -> float | None:
    """Mid-rank percentile in [0, 1]; 0 listeners -> 0.0; unknown -> None."""
    if listeners is None:
        return None
    if listeners <= 0:
        return 0.0
    lo = bisect.bisect_left(sorted_counts, listeners)
    hi = bisect.bisect_right(sorted_counts, listeners)
    return round(min(1.0, (lo + 0.5 * (hi - lo)) / len(sorted_counts)), 6)


def apply_buckets(songs: list[dict[str, Any]], reference: dict[str, Any]) -> dict[str, int]:
    counts = reference["sorted_listener_counts"]
    stamp = _now()
    by_genre: dict[str, list[int]] = defaultdict(list)
    for song in songs:
        pop = song["popularity"]
        pct = global_percentile(pop.get("listener_count"), counts)
        pop.update({"global_percentile": pct, "bucket": assign_bucket(pct),
                    "bucket_version": BUCKET_VERSION, "computed_at": stamp,
                    "reference": {"dump": reference["dump"],
                                  "population_sample_size": reference["population_sample_size"]}})
        for genre in song.get("genres", [])[:3]:
            if pop.get("listener_count") is not None:
                by_genre[genre].append(int(pop["listener_count"]))
    for values in by_genre.values():
        values.sort()
    distribution: dict[str, int] = defaultdict(int)
    for song in songs:
        pop = song["popularity"]
        pop["genre_percentile"] = {
            g: global_percentile(pop.get("listener_count"), by_genre[g])
            for g in song.get("genres", [])[:3]
            if by_genre.get(g) and len(by_genre[g]) >= 20 and pop.get("listener_count") is not None
        }
        distribution[pop["bucket"]] += 1
    return dict(distribution)


def refresh_thresholds(reference: dict[str, Any]) -> dict[str, Any]:
    """Re-derive thresholds for the current BUCKET_VERSION from the stored sample (no resampling)."""
    counts = reference["sorted_listener_counts"]
    reference["bucket_version"] = BUCKET_VERSION
    reference["thresholds"] = {
        "head_min_listeners": _quantile(counts, BUCKET_THRESHOLDS["head"]),
        "mid_min_listeners": _quantile(counts, BUCKET_THRESHOLDS["mid"]),
        "head_top_share": round(1 - BUCKET_THRESHOLDS["head"], 4),
        "mid_top_share": round(1 - BUCKET_THRESHOLDS["mid"], 4),
    }
    return reference


def bucket_catalog(songs_path: str | Path, reference_path: str | Path, log=print) -> dict[str, int]:
    reference = refresh_thresholds(json.loads(Path(reference_path).read_text("utf-8")))
    Path(reference_path).write_text(json.dumps(reference, ensure_ascii=False) + "\n", "utf-8")
    log(f"[bucket] {reference['bucket_version']} thresholds {reference['thresholds']}")
    songs = read_jsonl(Path(songs_path))
    distribution = apply_buckets(songs, reference)
    write_jsonl(Path(songs_path), songs)
    log(f"[bucket] {distribution}")
    return distribution

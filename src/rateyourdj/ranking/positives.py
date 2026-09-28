"""Weak positives (weak-pos-v1) for Recall@K / NDCG@K.

For every seed song, ListenBrainz Labs similar-recordings returns recordings that
real users co-listened with it in the same sessions. Those found in the catalog
become the branch's positives. "Weak": they describe what people who like the
seed also play (not this user's own taste) and lean towards popular songs. They
are independent of the catalog expansion, which used artist-level similarity.
Stage 4/5 replaces them with hidden positives from similar users' listen logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from rateyourdj.data_pipeline.sources import DEFAULT_SIMILAR_RECORDINGS_ALGORITHM, ListenBrainzClient

POSITIVES_VERSION = "weak-pos-v1"


def build_weak_positives(context: dict[str, Any], songs: list[dict[str, Any]],
                         lb: ListenBrainzClient) -> dict[str, Any]:
    mbid_to_song: dict[str, str] = {}
    for s in songs:
        ext = s.get("external_ids", {})
        for m in [ext.get("musicbrainz_recording"), *ext.get("musicbrainz_recording_merged", [])]:
            if m:
                mbid_to_song.setdefault(m, s["song_id"])
    by_id = {s["song_id"]: s for s in songs}
    branches: dict[str, Any] = {}
    for b in context["seed_branches"]:
        per_seed: dict[str, list[str]] = {}
        returned = 0
        for sid, mbid in zip(b["seed_song_ids"], b.get("seed_recording_mbids", [])):
            similar = lb.similar_recordings(mbid)
            returned += len(similar)
            hits = [mbid_to_song[r["recording_mbid"]] for r in similar if r["recording_mbid"] in mbid_to_song]
            per_seed[sid] = [h for h in dict.fromkeys(hits) if h not in b["seed_song_ids"]]
        union = list(dict.fromkeys(h for hs in per_seed.values() for h in hs))
        branches[b["branch_id"]] = {
            "song_ids": union,
            "by_seed": per_seed,
            "returned": returned,
            "in_catalog": len(union),
            "buckets": {k: sum(by_id[s]["popularity"]["bucket"] == k for s in union)
                        for k in ("head", "mid", "tail", "unknown")},
        }
    return {"version": POSITIVES_VERSION, "source": "ListenBrainz Labs similar-recordings",
            "algorithm": DEFAULT_SIMILAR_RECORDINGS_ALGORITHM,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "branches": branches}


def positives_for(positives: dict[str, Any], expect: str) -> set[str]:
    branches = positives["branches"]
    if expect == "both":
        return {s for b in branches.values() for s in b["song_ids"]}
    return set(branches.get(expect, {}).get("song_ids", []))


# ---------------------------------------------------------------- hidden-pos-v1 (stage 5)
HIDDEN_POSITIVES_VERSION = "hidden-pos-v1"


def build_two_hop_positives(context: dict[str, Any], songs: list[dict[str, Any]], lb: ListenBrainzClient, *,
                            hop1_expand: int = 40) -> dict[str, Any]:
    """Two-hop co-listen positives: seed -> similar recordings (hop 1) -> their similar recordings
    (hop 2), all from real ListenBrainz listening sessions. Songs found in the catalog become the
    branch's hidden positives, with ``support`` = how many distinct paths reach them.

    Evaluation only (never in a reward): they come from other people's listening behaviour and
    are independent of our embeddings, tags and ranking. Hop 2 reaches further from the seeds
    than weak-pos-v1 and so includes more mid / tail songs."""
    mbid_to_song: dict[str, str] = {}
    for s in songs:
        ext = s.get("external_ids", {})
        for m in [ext.get("musicbrainz_recording"), *ext.get("musicbrainz_recording_merged", [])]:
            if m:
                mbid_to_song.setdefault(m, s["song_id"])
    by_id = {s["song_id"]: s for s in songs}
    branches: dict[str, Any] = {}
    calls = 0
    for b in context["seed_branches"]:
        seed_ids = set(b["seed_song_ids"])
        support: dict[str, int] = {}
        hop_of: dict[str, int] = {}
        for mbid in b.get("seed_recording_mbids", []):
            hop1 = lb.similar_recordings(mbid)
            calls += 1
            for r in hop1:
                sid = mbid_to_song.get(r["recording_mbid"])
                if sid and sid not in seed_ids:
                    support[sid] = support.get(sid, 0) + 1
                    hop_of[sid] = 1
            for r in hop1[:hop1_expand]:
                hop2 = lb.similar_recordings(r["recording_mbid"])
                calls += 1
                for r2 in hop2:
                    sid = mbid_to_song.get(r2["recording_mbid"])
                    if sid and sid not in seed_ids:
                        support[sid] = support.get(sid, 0) + 1
                        hop_of.setdefault(sid, 2)
        ranked = sorted(support, key=lambda s: (-support[s], s))
        branches[b["branch_id"]] = {
            "song_ids": ranked,
            "support": {s: support[s] for s in ranked},
            "hop": {s: hop_of[s] for s in ranked},
            "in_catalog": len(ranked),
            "buckets": {k: sum(by_id[s]["popularity"]["bucket"] == k for s in ranked)
                        for k in ("head", "mid", "tail", "unknown")},
        }
    return {"version": HIDDEN_POSITIVES_VERSION, "source": "ListenBrainz Labs similar-recordings, two hops",
            "algorithm": DEFAULT_SIMILAR_RECORDINGS_ALGORITHM, "hop1_expand": hop1_expand, "api_calls": calls,
            "use": "evaluation / checkpoint selection only - never part of a reward",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "branches": branches}

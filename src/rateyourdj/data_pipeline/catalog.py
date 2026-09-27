"""Build the stage 1 catalog from a participant's seed branches.

expand_artists  seed artists -> LB similar artists (2 hops) + MB tag artists
collect_songs   LB top recordings per artist (with listener counts) -> dedupe
enrich          LB batch metadata (tags, release, year)
write           data/catalog/processed/{songs,artists}.jsonl + manifest.json

Popularity buckets are NOT assigned here (bucket 'unknown'); see popularity.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rateyourdj.contracts import BUCKET_VERSION, make_song_id, normalize_name, validate_record
from rateyourdj.contracts.v2 import SCHEMA_VERSIONS

from .sources import (ListenBrainzClient, MusicBrainzClient, is_bootleg_release, is_variant_title,
                      split_title, title_parts)

CATALOG_ROOT = Path("data/catalog")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dedupe_key(artist_credit: str, title: str, artist_mbids: list[str] | None = None) -> str:
    """Same song = same artists (by MBID when known, else credit text) + base title + parts."""
    parts = ",".join(str(p) for p in sorted(title_parts(title)))
    who = ",".join(sorted(m for m in artist_mbids or [] if m)) or normalize_name(artist_credit)
    return who + "|" + normalize_name(split_title(title)[0]) + "|" + parts


def stratified_pick(rows: list[dict[str, Any]], top_n: int, deep_n: int) -> list[dict[str, Any]]:
    """Keep the top_n most-listened rows plus deep_n rows spread evenly over the rest.

    Rows are assumed sorted by popularity (as ListenBrainz returns them); spreading
    the remainder brings album tracks and B-sides, not just an artist's hits.
    """
    head, rest = rows[:top_n], rows[top_n:]
    if deep_n <= 0 or not rest:
        return head
    if len(rest) <= deep_n:
        return head + rest
    step = len(rest) / deep_n
    return head + [rest[int(i * step)] for i in range(deep_n)]


# ------------------------------------------------------------------ artists


def expand_artists(context: dict[str, Any], config: dict[str, Any],
                   lb: ListenBrainzClient, mb: MusicBrainzClient,
                   log=print) -> dict[str, dict[str, Any]]:
    """artist_mbid -> {name, branches: {branch_id: affinity}, sources: [...]}"""
    exp = config.get("expansion", {})
    hop1_n, hop2_n = exp.get("similar_hop1", 25), exp.get("similar_hop2", 8)
    cap = exp.get("max_artists_per_branch", 300)
    per_tag = exp.get("tag_artists_per_tag", 60)
    tags_by_branch = {b["branch_id"]: b.get("expansion_tags", []) for b in config["branches"]}
    artists: dict[str, dict[str, Any]] = {}

    def add(mbid: str, name: str | None, branch: str, affinity: float, source: str) -> None:
        entry = artists.setdefault(mbid, {"mbid": mbid, "name": name, "branches": {}, "sources": []})
        entry["name"] = entry["name"] or name
        entry["branches"][branch] = round(max(entry["branches"].get(branch, 0.0), affinity), 4)
        if source not in entry["sources"]:
            entry["sources"].append(source)

    for branch in context["seed_branches"]:
        bid = branch["branch_id"]
        before = len([a for a in artists.values() if bid in a["branches"]])
        for seed_artist in branch.get("seed_artists_mbid", []):
            add(seed_artist, None, bid, 1.0, "seed")
            hop1 = lb.similar_artists(seed_artist)[:hop1_n]
            top1 = max([h["score"] for h in hop1] or [1.0]) or 1.0
            for h in hop1:
                a1 = 0.8 * h["score"] / top1
                add(h["mbid"], h["name"], bid, a1, "similar_hop1")
                hop2 = lb.similar_artists(h["mbid"])[:hop2_n]
                top2 = max([x["score"] for x in hop2] or [1.0]) or 1.0
                for x in hop2:
                    add(x["mbid"], x["name"], bid, a1 * 0.6 * x["score"] / top2, "similar_hop2")
        for tag in tags_by_branch.get(bid, []):
            for a in mb.search_artists_by_tag(tag, limit=min(per_tag, 100)):
                add(a["mbid"], a["name"], bid, 0.3, f"mb_tag:{tag}")
        members = sorted((a for a in artists.values() if bid in a["branches"]),
                         key=lambda a: -a["branches"][bid])
        for dropped in members[cap:]:
            dropped["branches"].pop(bid, None)
        log(f"[expand] {bid}: {min(len(members), cap)} artists (+{len(members) - before} found)")
    return {k: v for k, v in artists.items() if v["branches"]}


# ------------------------------------------------------------------ songs


def collect_songs(artists: dict[str, dict[str, Any]], config: dict[str, Any],
                  lb: ListenBrainzClient, log=print) -> list[dict[str, Any]]:
    exp = config.get("expansion", {})
    top_n = exp.get("top_recordings_per_artist", 10)
    deep_n = exp.get("deep_recordings_per_artist", 30)
    floor_ratio = exp.get("deep_min_listener_ratio", 0.001)
    floor_abs = exp.get("deep_min_listeners", 10)
    best: dict[str, dict[str, Any]] = {}
    stats = {"raw": 0, "variants_dropped": 0, "bootlegs_dropped": 0, "duplicates_merged": 0}
    for i, artist in enumerate(artists.values(), 1):
        rows = lb.top_recordings_for_artist(artist["mbid"])
        stats["raw"] += len(rows)
        clean = []
        for r in rows:
            if is_variant_title(r["title"] or ""):
                stats["variants_dropped"] += 1
            elif is_bootleg_release(r.get("release_title")):
                stats["bootlegs_dropped"] += 1
            else:
                clean.append(r)
        # collapse duplicate listings of one song first, so they don't eat sampling slots
        by_key: dict[str, dict[str, Any]] = {}
        for r in clean:
            key = _dedupe_key(r["artist_credit"] or artist["name"] or "", r["title"] or "",
                              r.get("artist_mbids"))
            if key not in by_key:
                by_key[key] = dict(r)
                continue
            stats["duplicates_merged"] += 1
            kept = by_key[key]
            if (r.get("listener_count") or 0) > (kept.get("listener_count") or 0):
                winner = dict(r)
                winner["merged_mbids"] = kept.get("merged_mbids", []) + [kept["recording_mbid"]]
                by_key[key] = winner
            else:
                kept.setdefault("merged_mbids", []).append(r["recording_mbid"])
        unique = sorted(by_key.values(), key=lambda r: -(r.get("listener_count") or 0))
        top = unique[0].get("listener_count") or 0 if unique else 0
        floor = max(floor_abs, top * floor_ratio)
        eligible = unique[:top_n] + [r for r in unique[top_n:] if (r.get("listener_count") or 0) >= floor]
        for r in stratified_pick(eligible, top_n, deep_n):
            key = _dedupe_key(r["artist_credit"] or artist["name"] or "", r["title"] or "",
                              r.get("artist_mbids"))
            r = {**r, "source_artist_mbid": artist["mbid"]}
            if key in best:
                stats["duplicates_merged"] += 1
                if (r.get("listener_count") or 0) > (best[key].get("listener_count") or 0):
                    r["merged_mbids"] = (r.get("merged_mbids", []) + best[key].get("merged_mbids", [])
                                         + [best[key]["recording_mbid"]])
                    best[key] = r
                else:
                    best[key].setdefault("merged_mbids", []).append(r["recording_mbid"])
                continue
            best[key] = r
        if i % 50 == 0:
            log(f"[songs] {i}/{len(artists)} artists, {len(best)} songs")
    log(f"[songs] {len(best)} songs; {stats}")
    collect_songs.last_stats = stats  # type: ignore[attr-defined]
    return list(best.values())


def _tags(meta: dict[str, Any]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, source in (("recording_tags", "recording"), ("release_group_tags", "release_group"),
                        ("artist_tags", "artist")):
        values = meta.get(key) or []
        top = max([c for _, c in values] or [1]) or 1
        for name, count in values:
            name = normalize_name(name)
            if name and name not in out:
                out[name] = {"name": name, "weight": round(count / top, 4),
                             "source": f"listenbrainz:{source}"}
    return sorted(out.values(), key=lambda t: -t["weight"])[:25]


def build_song_profiles(songs: list[dict[str, Any]], artists: dict[str, dict[str, Any]],
                        metadata: dict[str, dict[str, Any]], fetched_at: str) -> list[dict[str, Any]]:
    profiles = []
    for s in songs:
        mbid = s["recording_mbid"]
        meta = metadata.get(mbid, {})
        song_id, basis = make_song_id(recording_mbid=mbid)
        artist_list = meta.get("artists") or [
            {"name": s.get("artist_credit"), "mbid": m} for m in s.get("artist_mbids") or []]
        release = meta.get("release") or {}
        tags = _tags(meta)
        branches: dict[str, float] = {}
        for a_mbid in s.get("artist_mbids") or [s.get("source_artist_mbid")]:
            for b, v in (artists.get(a_mbid) or {}).get("branches", {}).items():
                branches[b] = max(branches.get(b, 0.0), v)
        record = {
            "schema_version": SCHEMA_VERSIONS["song_profile"],
            "song_id": song_id,
            "id_basis": basis,
            "title": meta.get("title") or s.get("title"),
            "artist_credit": meta.get("artist_credit") or s.get("artist_credit"),
            "artists": [{"name": a.get("name"), "mbid": a.get("mbid")} for a in artist_list],
            "release": {"title": release.get("title") or s.get("release_title"),
                        "mbid": release.get("mbid") or s.get("release_mbid"),
                        "year": release.get("year"), "country": None},
            "duration_ms": meta.get("length_ms") or s.get("length_ms"),
            "external_ids": {"musicbrainz_recording": mbid,
                             "musicbrainz_recording_merged": s.get("merged_mbids", []),
                             "isrc": [], "spotify_track": None, "youtube_video": None},
            "tags": tags,
            "genres": [t["name"] for t in tags[:5]],
            "popularity": {
                "listen_count": s.get("listen_count"),
                "listener_count": s.get("listener_count"),
                "source": "listenbrainz",
                "metric": "listener_count",
                "global_percentile": None,
                "genre_percentile": {},
                "bucket": "unknown",
                "bucket_version": BUCKET_VERSION,
                "computed_at": None,
            },
            "playback": {"source": "none", "url": None, "verified": False,
                         "verification_method": None, "verified_at": None},
            "branch_affinity": {k: round(v, 4) for k, v in branches.items()},
            "rag_doc_id": None,
            "provenance": [
                {"source": "listenbrainz", "license": "CC0", "fetched_at": fetched_at},
                {"source": "musicbrainz", "license": "CC0", "fetched_at": fetched_at},
            ],
        }
        validate_record("song_profile", record)
        profiles.append(record)
    return profiles


# ------------------------------------------------------------------ io


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_catalog(context: dict[str, Any], config: dict[str, Any], *,
                  lb: ListenBrainzClient, mb: MusicBrainzClient,
                  catalog_root: str | Path = CATALOG_ROOT, max_artists: int | None = None,
                  log=print) -> dict[str, Any]:
    root = Path(catalog_root)
    fetched_at = _now()
    artists = expand_artists(context, config, lb, mb, log=log)
    if max_artists:  # smoke runs: keep the highest-affinity artists
        ranked = sorted(artists.values(), key=lambda a: -max(a["branches"].values()))
        artists = {a["mbid"]: a for a in ranked[:max_artists]}
    songs = collect_songs(artists, config, lb, log=log)
    stats = dict(getattr(collect_songs, "last_stats", {}))
    # seeds must be in the catalog even if expansion or per-artist caps missed them
    present = {s["recording_mbid"] for s in songs}
    present |= {m for s in songs for m in s.get("merged_mbids", [])}
    missing = [m for b in context["seed_branches"] for m in b.get("seed_recording_mbids", [])
               if m not in present]
    if missing:
        pop = lb.recording_popularity(missing)
        for b in context["seed_branches"]:
            for m in b.get("seed_recording_mbids", []):
                if m in missing:
                    songs.append({"recording_mbid": m, "title": None, "artist_credit": None,
                                  "artist_mbids": list(b.get("seed_artists_mbid", [])),
                                  "source_artist_mbid": (b.get("seed_artists_mbid") or [None])[0],
                                  **pop.get(m, {"listen_count": None, "listener_count": None})})
        stats["seeds_added_explicitly"] = len(missing)
    metadata = lb.recording_metadata([s["recording_mbid"] for s in songs])
    profiles = build_song_profiles(songs, artists, metadata, fetched_at)

    # seeds are always part of the catalog (flagged), even if expansion missed them
    seed_ids = {sid for b in context["seed_branches"] for sid in b["seed_song_ids"]}
    for p in profiles:
        p["is_seed"] = p["song_id"] in seed_ids

    write_jsonl(root / "processed" / "songs.jsonl", profiles)
    write_jsonl(root / "processed" / "artists.jsonl", list(artists.values()))
    manifest = {
        "catalog_version": "catalog-" + fetched_at[:10].replace("-", ""),
        "built_at": fetched_at,
        "user_id": context["user_id"],
        "sources": [
            {"name": "MusicBrainz", "license": "CC0 (core data)", "api": "https://musicbrainz.org/ws/2"},
            {"name": "ListenBrainz", "license": "CC0", "api": "https://api.listenbrainz.org/1"},
        ],
        "config": config.get("expansion", {}),
        "counts": {"artists": len(artists), "songs": len(profiles),
                   "seeds_in_catalog": sum(p["is_seed"] for p in profiles),
                   "seeds_total": len(seed_ids), **stats},
        "http": {"lb": lb.http.stats, "mb": mb.http.stats},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                       "utf-8")
    log(f"[catalog] {len(profiles)} songs from {len(artists)} artists -> {root / 'processed'}")
    return manifest

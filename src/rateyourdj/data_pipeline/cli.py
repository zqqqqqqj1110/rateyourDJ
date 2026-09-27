"""rateyourdj-catalog: stage 1 pipeline commands (run on a networked machine).

  init-seeds        resolve seed songs -> data/users/<id>/context.json (+ seeds_resolved.json)
  build             expand artists, collect songs, write data/catalog/processed/songs.jsonl
  sample-reference  estimate the global listener distribution from mbdump.tar.bz2
  bucket            assign head/mid/tail using the reference
  resolve-playback  resolve Spotify / YouTube links on demand (seeds, ids, or a bucket)
  report            write data/catalog/report.{json,md}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rateyourdj.config import load_dotenv

from .catalog import build_catalog, read_jsonl, write_jsonl
from .playback import (PlaybackResolver, YouTubeResolver, apply_playback,
                       spotify_search_from_env)
from .popularity import (bucket_catalog, build_reference, iter_recording_rows,
                         reservoir_sample_mbids)
from .report import write_report
from .seeds import resolve_participant_seeds
from .sources import ListenBrainzClient, MusicBrainzClient
from .user_context import load_user_context


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="rateyourdj-catalog", description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config/participant_001.seeds.json")
    parser.add_argument("--catalog-root", default="data/catalog")
    parser.add_argument("--users-root", default="data/users")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-seeds")
    build = sub.add_parser("build")
    build.add_argument("--max-artists", type=int, help="smoke run: only the top-N artists")
    sample = sub.add_parser("sample-reference")
    sample.add_argument("--dump", required=True, help="mbdump.tar.bz2 or an extracted recording file")
    sample.add_argument("--dump-label", default="mbdump")
    sample.add_argument("--sample-size", type=int, default=200_000)
    sub.add_parser("bucket")
    playback = sub.add_parser("resolve-playback")
    playback.add_argument("--seeds", action="store_true")
    playback.add_argument("--song-id", action="append", default=[])
    playback.add_argument("--bucket", choices=["head", "mid", "tail", "unknown"])
    playback.add_argument("--limit", type=int, default=0)
    playback.add_argument("--force", action="store_true")
    sub.add_parser("report")
    args = parser.parse_args(argv)

    root = Path(args.catalog_root)
    raw = root / "raw"
    songs_path = root / "processed" / "songs.jsonl"
    config = json.loads(Path(args.config).read_text("utf-8"))
    user_id = config["user_id"]

    if args.command == "init-seeds":
        result = resolve_participant_seeds(args.config, cache_root=raw, users_root=args.users_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("请检查 seeds_resolved.json 里每首种子选中的录音是否正确。")
        return 0 if not result["unresolved"] else 1

    context = load_user_context(user_id, args.users_root, legacy_root=None)

    if args.command == "build":
        manifest = build_catalog(context, config, lb=ListenBrainzClient(raw), mb=MusicBrainzClient(raw),
                                 catalog_root=root, max_artists=args.max_artists)
        print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
        return 0

    if args.command == "sample-reference":
        mbids, total = reservoir_sample_mbids(iter_recording_rows(args.dump), args.sample_size)
        print(f"[sample] {total:,} recordings scanned, {len(mbids):,} sampled; querying ListenBrainz...")
        reference = build_reference(mbids, ListenBrainzClient(raw), total_recordings=total,
                                    dump_label=args.dump_label)
        out = root / "popularity_reference.json"
        out.write_text(json.dumps(reference, ensure_ascii=False) + "\n", "utf-8")
        print(json.dumps({k: v for k, v in reference.items() if k != "sorted_listener_counts"},
                         ensure_ascii=False, indent=2))
        return 0

    if args.command == "bucket":
        bucket_catalog(songs_path, root / "popularity_reference.json")
        return 0

    if args.command == "resolve-playback":
        songs = read_jsonl(songs_path)
        seed_ids = {sid for b in context["seed_branches"] for sid in b["seed_song_ids"]}
        chosen = [s for s in songs if (args.seeds and s["song_id"] in seed_ids)
                  or s["song_id"] in args.song_id
                  or (args.bucket and s["popularity"]["bucket"] == args.bucket)]
        if args.limit:
            chosen = chosen[: args.limit]
        resolver = PlaybackResolver(root / "playback_cache.json", mb=MusicBrainzClient(raw),
                                    youtube=YouTubeResolver(raw),
                                    spotify_search=spotify_search_from_env())
        summary: dict[str, int] = {}
        try:
            for song in chosen:
                result = resolver.resolve(song, force=args.force)
                apply_playback(song, result)
                key = result["source"] if result["source"] != "none" else "none:" + result.get("reason", "")
                summary[key] = summary.get(key, 0) + 1
        finally:
            resolver.save()
            write_jsonl(songs_path, songs)
        print(json.dumps({"resolved": len(chosen), **summary}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "report":
        report = write_report(root, read_jsonl(songs_path), context)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
from typing import Any

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore

from .models import retrieval_schema
from .service import CandidateRetrievalService


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="rateyourDJ L3 candidate retrieval"
    )
    parser.add_argument(
        "--profile-dir",
        default="data/user_profiles",
        help="L1 profile JSON directory",
    )
    parser.add_argument(
        "--song-dir",
        default="data/song_profiles",
        help="L2 song JSON directory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("schema", help="print the L3 candidate schema")

    retrieve = subparsers.add_parser(
        "retrieve", help="retrieve candidates for a stored user"
    )
    retrieve.add_argument("user_id")
    retrieve.add_argument("--top-k", type=int, default=20)
    retrieve.add_argument("--max-per-artist", type=int, default=2)
    retrieve.add_argument("--min-score", type=float, default=0.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "schema":
        _print_json(retrieval_schema())
        return

    service = CandidateRetrievalService(
        JsonProfileStore(args.profile_dir),
        JsonSongStore(args.song_dir),
    )
    _print_json(
        service.retrieve(
            args.user_id,
            top_k=args.top_k,
            max_per_artist=args.max_per_artist,
            min_score=args.min_score,
        ).to_dict()
    )


if __name__ == "__main__":
    main()

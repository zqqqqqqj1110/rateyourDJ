from __future__ import annotations

import argparse
import json
from typing import Any

from rateyourdj.l1 import JsonProfileStore
from rateyourdj.l2 import JsonSongStore

from .models import ranking_schema
from .service import RecommendationRankingService


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="rateyourDJ L4 recommendation ranking"
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
    subparsers.add_parser("schema", help="print the L4 ranking schema")

    rank = subparsers.add_parser(
        "rank",
        help="retrieve and rank recommendations for a stored user",
    )
    rank.add_argument("user_id")
    rank.add_argument("--top-k", type=int, default=20)
    rank.add_argument("--candidate-pool-size", type=int)
    rank.add_argument("--max-per-artist", type=int, default=2)
    rank.add_argument("--min-retrieval-score", type=float, default=0.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "schema":
        _print_json(ranking_schema())
        return

    service = RecommendationRankingService(
        JsonProfileStore(args.profile_dir),
        JsonSongStore(args.song_dir),
    )
    _print_json(
        service.rank(
            args.user_id,
            top_k=args.top_k,
            candidate_pool_size=args.candidate_pool_size,
            max_per_artist=args.max_per_artist,
            min_retrieval_score=args.min_retrieval_score,
        ).to_dict()
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from rateyourdj.providers import configured_music_provider_from_env

from .deepseek import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    configured_llm_provider,
)
from .models import agent_schema
from .tools import request_recommendations


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="rateyourDJ L6 recommendation agent"
    )
    parser.add_argument("--profile-dir", default="data/user_profiles")
    parser.add_argument("--song-dir", default="data/song_profiles")
    parser.add_argument("--trajectory-dir", default="data/trajectories")
    parser.add_argument("--session-dir", default="data/sessions")
    parser.add_argument(
        "--agent-mode",
        choices=("auto", "model", "rules"),
        default="auto",
        help="model uses a configured provider; auto falls back to rules",
    )
    parser.add_argument(
        "--llm-provider",
        choices=("auto", "deepseek", "none"),
        default="auto",
        help="auto uses DeepSeek when DEEPSEEK_API_KEY is configured",
    )
    parser.add_argument(
        "--deepseek-model",
        default=os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
    )
    parser.add_argument(
        "--deepseek-base-url",
        default=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("schema", help="print the L6 request schema")
    recommend = subparsers.add_parser(
        "recommend",
        help="run one natural-language recommendation request",
    )
    recommend.add_argument("user_id")
    recommend.add_argument("query")
    recommend.add_argument("--default-top-k", type=int, default=10)
    recommend.add_argument("--session-id")
    recommend.add_argument("--max-steps", type=int, default=5)
    return parser


def main() -> None:
    from rateyourdj.config import load_dotenv

    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "schema":
        _print_json(agent_schema())
        return
    try:
        llm_provider = configured_llm_provider(
            args.llm_provider,
            model=args.deepseek_model,
            base_url=args.deepseek_base_url,
        )
    except ValueError as error:
        parser.error(str(error))
    response = request_recommendations(
        args.user_id,
        args.query,
        profile_dir=args.profile_dir,
        song_dir=args.song_dir,
        trajectory_dir=args.trajectory_dir,
        session_dir=args.session_dir,
        default_top_k=args.default_top_k,
        session_id=args.session_id,
        max_steps=args.max_steps,
        agent_mode=args.agent_mode,
        llm_provider=llm_provider,
        music_provider=configured_music_provider_from_env(),
    )
    _print_json(response.to_dict())


if __name__ == "__main__":
    main()

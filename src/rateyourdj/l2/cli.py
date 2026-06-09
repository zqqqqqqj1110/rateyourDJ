from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .models import song_schema, validate_song_patch
from .service import SongProfileService
from .store import JsonSongStore


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="rateyourDJ L2 song store")
    parser.add_argument(
        "--data-dir", default="data/song_profiles", help="song JSON directory"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("schema", help="print the accepted L2 dictionary schema")

    init = subparsers.add_parser("init", help="create an empty complete song profile")
    init.add_argument("song_id")

    show = subparsers.add_parser("show", help="show a stored song profile")
    show.add_argument("song_id")

    validate = subparsers.add_parser(
        "validate", help="validate a partial song dictionary"
    )
    validate.add_argument("json_file", type=Path)

    import_command = subparsers.add_parser(
        "import", help="validate and merge a dictionary into a song profile"
    )
    import_command.add_argument("song_id")
    import_command.add_argument("json_file", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    service = SongProfileService(JsonSongStore(args.data_dir))

    if args.command == "schema":
        _print_json(song_schema())
        return
    if args.command in {"init", "show"}:
        _print_json(service.get_song_profile(args.song_id).to_dict())
        return
    if args.command == "validate":
        _print_json(validate_song_patch(_load_json(args.json_file)))
        return
    if args.command == "import":
        _print_json(
            service.import_song_patch(
                args.song_id, _load_json(args.json_file)
            ).to_dict()
        )


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .models import profile_schema, validate_profile_patch
from .service import UserProfileService
from .store import JsonProfileStore


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="rateyourDJ L1 profile store")
    parser.add_argument(
        "--data-dir", default="data/user_profiles", help="profile JSON directory"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("schema", help="print the accepted L1 dictionary schema")

    init = subparsers.add_parser("init", help="create an empty complete profile")
    init.add_argument("user_id")

    show = subparsers.add_parser("show", help="show a stored user profile")
    show.add_argument("user_id")

    validate = subparsers.add_parser(
        "validate", help="validate a partial profile dictionary"
    )
    validate.add_argument("json_file", type=Path)

    import_command = subparsers.add_parser(
        "import", help="validate and merge a dictionary into a profile"
    )
    import_command.add_argument("user_id")
    import_command.add_argument("json_file", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    service = UserProfileService(JsonProfileStore(args.data_dir))

    if args.command == "schema":
        _print_json(profile_schema())
        return
    if args.command in {"init", "show"}:
        _print_json(service.get_user_profile(args.user_id).to_dict())
        return
    if args.command == "validate":
        _print_json(validate_profile_patch(_load_json(args.json_file)))
        return
    if args.command == "import":
        _print_json(
            service.import_profile_patch(
                args.user_id, _load_json(args.json_file)
            ).to_dict()
        )


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import os

from .album import collect_album
from .catalog import ALBUMS_BY_KEY, BATCH_1, BATCH_2
from .lastfm import LastfmCollector
from .musicbrainz import MusicBrainzCollector
from .spotify import SpotifyCollector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build local rateyourDJ datasets")
    subparsers = parser.add_subparsers(dest="command", required=True)
    album = subparsers.add_parser("album", help="collect a supported album")
    album.add_argument(
        "album_key",
        choices=["all", "batch-1", "batch-2", *sorted(ALBUMS_BY_KEY)],
    )
    album.add_argument("--user-id", default="demo-user")
    album.add_argument("--song-data-dir", default="data/song_profiles")
    album.add_argument("--user-data-dir", default="data/user_profiles")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "album":
        missing = [
            name
            for name in (
                "SPOTIFY_CLIENT_ID",
                "SPOTIFY_CLIENT_SECRET",
                "LASTFM_API_KEY",
            )
            if not os.getenv(name)
        ]
        if missing:
            raise SystemExit(
                "missing environment variables: " + ", ".join(missing)
            )
        spotify = SpotifyCollector(
            os.environ["SPOTIFY_CLIENT_ID"],
            os.environ["SPOTIFY_CLIENT_SECRET"],
        )
        musicbrainz = MusicBrainzCollector()
        lastfm = LastfmCollector(os.environ["LASTFM_API_KEY"])
        if args.album_key == "all":
            albums = list(ALBUMS_BY_KEY.values())
        elif args.album_key == "batch-1":
            albums = list(BATCH_1)
        elif args.album_key == "batch-2":
            albums = list(BATCH_2)
        else:
            albums = [ALBUMS_BY_KEY[args.album_key]]
        results = [
            collect_album(
                album,
                spotify=spotify,
                musicbrainz=musicbrainz,
                lastfm=lastfm,
                song_data_dir=args.song_data_dir,
                user_id=args.user_id,
                user_data_dir=args.user_data_dir,
            )
            for album in albums
        ]
        result = results[0] if len(results) == 1 else {
            "requested_albums": len(results),
            "requested_tracks": sum(
                item["requested_tracks"] for item in results
            ),
            "stored_tracks": sum(item["stored_tracks"] for item in results),
            "albums": results,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

"""External metadata collectors and batch dataset builders."""

from .album import (
    AlbumDefinition,
    AlbumTrack,
    collect_album,
    rebuild_user_profile,
)
from .catalog import ALBUMS, ALBUMS_BY_KEY, PINK_FLOYD_THE_WALL

__all__ = [
    "ALBUMS",
    "ALBUMS_BY_KEY",
    "PINK_FLOYD_THE_WALL",
    "AlbumDefinition",
    "AlbumTrack",
    "collect_album",
    "rebuild_user_profile",
]

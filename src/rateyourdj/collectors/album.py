from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from rateyourdj.l1 import JsonProfileStore, UserProfileService
from rateyourdj.l2 import JsonSongStore, SongProfile, SongProfileService
from rateyourdj.l2.matching import records_match


@dataclass(frozen=True, slots=True)
class AlbumTrack:
    number: int
    title: str


@dataclass(frozen=True, slots=True)
class AlbumDefinition:
    key: str
    artist: str
    album: str
    tracks: tuple[AlbumTrack, ...]


def _album(
    key: str,
    artist: str,
    title: str,
    tracks: tuple[str, ...],
) -> AlbumDefinition:
    return AlbumDefinition(
        key=key,
        artist=artist,
        album=title,
        tracks=tuple(
            AlbumTrack(number, track)
            for number, track in enumerate(tracks, start=1)
        ),
    )


PINK_FLOYD_THE_WALL = AlbumDefinition(
    key="pink-floyd-the-wall",
    artist="Pink Floyd",
    album="The Wall",
    tracks=tuple(
        AlbumTrack(number, title)
        for number, title in enumerate(
            (
                "In the Flesh?",
                "The Thin Ice",
                "Another Brick in the Wall, Part 1",
                "The Happiest Days of Our Lives",
                "Another Brick in the Wall, Part 2",
                "Mother",
                "Goodbye Blue Sky",
                "Empty Spaces",
                "Young Lust",
                "One of My Turns",
                "Don't Leave Me Now",
                "Another Brick in the Wall, Part 3",
                "Goodbye Cruel World",
                "Hey You",
                "Is There Anybody Out There?",
                "Nobody Home",
                "Vera",
                "Bring the Boys Back Home",
                "Comfortably Numb",
                "The Show Must Go On",
                "In the Flesh",
                "Run Like Hell",
                "Waiting for the Worms",
                "Stop",
                "The Trial",
                "Outside the Wall",
            ),
            start=1,
        )
    ),
)


class SpotifyProtocol(Protocol):
    def collect_track(
        self,
        title: str,
        artist: str,
        album: str | None = None,
    ) -> dict[str, Any]: ...


class MusicBrainzProtocol(Protocol):
    def collect_recording(self, title: str, artist: str) -> dict[str, Any]: ...


class LastfmProtocol(Protocol):
    def collect_tags(self, title: str, artist: str) -> dict[str, Any]: ...


def song_id_for(album: AlbumDefinition, track: AlbumTrack) -> str:
    slug = track.title.casefold().replace("?", "-question")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return f"{album.key}-{track.number:02d}-{slug}"


def collect_album(
    album: AlbumDefinition,
    *,
    spotify: SpotifyProtocol,
    musicbrainz: MusicBrainzProtocol,
    lastfm: LastfmProtocol,
    song_data_dir: str | Path = "data/song_profiles",
    user_id: str | None = None,
    user_data_dir: str | Path = "data/user_profiles",
) -> dict[str, Any]:
    song_service = SongProfileService(JsonSongStore(song_data_dir))
    profiles: list[SongProfile] = []
    failures: list[dict[str, str]] = []

    for track in album.tracks:
        source_data: dict[str, Any] = {}
        errors: list[str] = []
        for source_name, collector in (
            (
                "spotify",
                lambda: spotify.collect_track(
                    track.title,
                    album.artist,
                    album.album,
                ),
            ),
            (
                "musicbrainz",
                lambda: musicbrainz.collect_recording(track.title, album.artist),
            ),
            ("lastfm", lambda: lastfm.collect_tags(track.title, album.artist)),
        ):
            try:
                record = collector()
                reference = {
                    "title": track.title,
                    "artist": album.artist,
                }
                if not records_match(reference, record):
                    raise LookupError(
                        f"returned {record.get('title') or record.get('track')} "
                        f"by {record.get('artist') or record.get('artists')}"
                    )
                source_data[source_name] = record
            except (LookupError, RuntimeError, TimeoutError, ValueError) as error:
                errors.append(f"{source_name}: {error}")

        if not source_data:
            failures.append({"title": track.title, "error": "; ".join(errors)})
            continue
        try:
            profile = song_service.merge_and_save_sources(
                song_id_for(album, track),
                spotify=source_data.get("spotify"),
                musicbrainz=source_data.get("musicbrainz"),
                lastfm=source_data.get("lastfm"),
            )
        except ValueError as error:
            failures.append(
                {
                    "title": track.title,
                    "error": "; ".join([*errors, f"merge: {error}"]),
                }
            )
            continue
        profiles.append(profile)
        if errors:
            failures.append({"title": track.title, "error": "; ".join(errors)})

    if user_id:
        _update_user_profile(
            user_id,
            profiles,
            song_data_dir=song_data_dir,
            user_data_dir=user_data_dir,
        )
    return {
        "album": album.album,
        "artist": album.artist,
        "requested_tracks": len(album.tracks),
        "stored_tracks": len(profiles),
        "song_ids": [profile.song_id for profile in profiles],
        "failures": failures,
    }


def _update_user_profile(
    user_id: str,
    new_profiles: list[SongProfile],
    *,
    song_data_dir: str | Path,
    user_data_dir: str | Path,
) -> None:
    service = UserProfileService(JsonProfileStore(user_data_dir))
    existing = service.get_user_profile(user_id)
    song_ids = list(existing.collection_song_ids)
    for profile in new_profiles:
        if profile.song_id not in song_ids:
            song_ids.append(profile.song_id)

    song_store = JsonSongStore(song_data_dir)
    profiles = [
        song_store.load(song_id)
        for song_id in song_ids
        if song_store.exists(song_id)
    ]
    artist_counts: dict[str, float] = defaultdict(float)
    genre_totals: dict[str, float] = defaultdict(float)
    tag_totals: dict[str, float] = defaultdict(float)
    for profile in profiles:
        artist = profile.metadata.get("artist")
        if artist:
            artist_counts[str(artist)] += 1
        for genre, score in profile.genres.items():
            genre_totals[genre] += score
        for score_map in profile.source_tags.values():
            for tag, score in score_map.items():
                tag_totals[tag] += score

    service.replace_profile_data(
        user_id,
        {
            "collection_song_ids": [profile.song_id for profile in profiles],
            "artist_preferences": _normalize_totals(artist_counts),
            "genre_preferences": _normalize_totals(genre_totals),
            "tag_preferences": _normalize_totals(tag_totals),
            "feedback_memory": existing.feedback_memory,
        },
    )


def _normalize_totals(values: dict[str, float]) -> dict[str, float]:
    maximum = max(values.values(), default=0)
    if maximum <= 0:
        return {}
    return {
        key: round(value / maximum, 4)
        for key, value in sorted(values.items())
    }

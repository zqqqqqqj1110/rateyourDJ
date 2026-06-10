import json
import tempfile
import unittest
from pathlib import Path

from rateyourdj.collectors.album import (
    PINK_FLOYD_THE_WALL,
    collect_album,
    rebuild_user_profile,
    song_id_for,
)
from rateyourdj.collectors.cli import build_parser
from rateyourdj.l1 import JsonProfileStore, UserProfile
from rateyourdj.l2 import JsonSongStore, SongProfile
from rateyourdj.collectors.catalog import ALBUMS_BY_KEY, BATCH_2
from rateyourdj.l2.matching import normalize_identity


class FakeSpotify:
    def collect_track(self, title, artist, album=None):
        return {
            "source": "Spotify",
            "spotify_track_id": f"spotify-{normalize_identity(title)}",
            "title": title,
            "artists": [artist],
            "album": f"{album} (2011 Remastered)",
            "release_year": 1979,
            "duration_ms": 180000,
        }


class FakeMusicBrainz:
    def collect_recording(self, title, artist):
        return {
            "source": "MusicBrainz",
            "musicbrainz_recording_id": f"mb-{normalize_identity(title)}",
            "title": title,
            "artists": [artist],
            "album": "The Wall",
            "release_year": 1979,
            "duration_ms": 180000,
            "score": 100,
        }


class FakeLastfm:
    def collect_tags(self, title, artist):
        return {
            "source": "Last.fm",
            "track": title,
            "artist": artist,
            "track_tags": [
                {"name": "progressive rock", "count": 100},
                {"name": "rock", "count": 80},
                {"name": "70s", "count": 50},
            ],
            "artist_tags": [
                {"name": "progressive rock", "count": 100},
                {"name": "psychedelic", "count": 90},
            ],
        }


class TimeoutSpotify:
    def collect_track(self, title, artist, album=None):
        raise TimeoutError("read operation timed out")


class AlbumCollectorTests(unittest.TestCase):
    def test_album_collection_does_not_default_to_a_user(self):
        args = build_parser().parse_args(["album", "pink-floyd-the-wall"])

        self.assertIsNone(args.user_id)

    def test_catalog_contains_requested_album_editions(self):
        first_batch_counts = {
            "pink-floyd-the-wall": 26,
            "frank-sinatra-in-the-wee-small-hours": 16,
            "sly-and-the-family-stone-theres-a-riot-goin-on": 11,
            "elvis-costello-this-years-model-expanded": 25,
            "bob-dylan-1963": 13,
            "the-who-tommy": 24,
            "creedence-clearwater-revival-green-river": 9,
            "elton-john-goodbye-yellow-brick-road-expanded": 21,
        }
        for key, count in first_batch_counts.items():
            self.assertEqual(len(ALBUMS_BY_KEY[key].tracks), count)

    def test_second_batch_contains_10_albums_and_139_tracks(self):
        expected_counts = {
            "stevie-wonder-talking-book": 10,
            "dusty-springfield-dusty-in-memphis-expanded": 25,
            "johnny-cash-at-folsom-prison-expanded": 19,
            "the-beatles-let-it-be": 12,
            "bruce-springsteen-born-in-the-usa": 12,
            "aretha-franklin-lady-soul-expanded": 14,
            "aretha-franklin-i-never-loved-a-man": 11,
            "jimi-hendrix-experience-axis-bold-as-love": 13,
            "paul-simon-graceland": 11,
            "the-zombies-odessey-and-oracle-stereo": 12,
        }
        self.assertEqual(
            {album.key: len(album.tracks) for album in BATCH_2},
            expected_counts,
        )
        self.assertEqual(sum(expected_counts.values()), 139)
        self.assertEqual(len(ALBUMS_BY_KEY), 18)

    def test_the_wall_manifest_has_26_unique_tracks(self):
        self.assertEqual(len(PINK_FLOYD_THE_WALL.tracks), 26)
        ids = {
            song_id_for(PINK_FLOYD_THE_WALL, track)
            for track in PINK_FLOYD_THE_WALL.tracks
        }
        self.assertEqual(len(ids), 26)
        self.assertNotEqual(
            normalize_identity("In the Flesh?"),
            normalize_identity("In the Flesh"),
        )

    def test_collects_album_into_l2_and_updates_l1(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            song_dir = root / "songs"
            user_dir = root / "users"
            result = collect_album(
                PINK_FLOYD_THE_WALL,
                spotify=FakeSpotify(),
                musicbrainz=FakeMusicBrainz(),
                lastfm=FakeLastfm(),
                song_data_dir=song_dir,
                user_id="pink-floyd-user",
                user_data_dir=user_dir,
            )

            self.assertEqual(result["stored_tracks"], 26)
            self.assertEqual(result["failures"], [])
            self.assertEqual(len(list(song_dir.glob("*.json"))), 26)

            profile = json.loads(
                (user_dir / "pink-floyd-user.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(profile["collection_song_ids"]), 26)
            self.assertEqual(profile["artist_preferences"], {"Pink Floyd": 1.0})
            self.assertEqual(
                profile["genre_preferences"]["progressive_rock"],
                1.0,
            )

    def test_multiple_album_runs_reaggregate_the_full_collection(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            song_dir = root / "songs"
            user_dir = root / "users"
            first = next(iter(ALBUMS_BY_KEY.values()))
            second = list(ALBUMS_BY_KEY.values())[1]

            for album in (first, second):
                collect_album(
                    album,
                    spotify=FakeSpotify(),
                    musicbrainz=FakeMusicBrainz(),
                    lastfm=FakeLastfm(),
                    song_data_dir=song_dir,
                    user_id="collector",
                    user_data_dir=user_dir,
                )

            profile = json.loads(
                (user_dir / "collector.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                len(profile["collection_song_ids"]),
                len(first.tracks) + len(second.tracks),
            )
            self.assertIn("Pink Floyd", profile["artist_preferences"])
            self.assertIn("Frank Sinatra", profile["artist_preferences"])

    def test_source_timeout_does_not_abort_the_album(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            album = list(ALBUMS_BY_KEY.values())[6]
            result = collect_album(
                album,
                spotify=TimeoutSpotify(),
                musicbrainz=FakeMusicBrainz(),
                lastfm=FakeLastfm(),
                song_data_dir=root / "songs",
            )

            self.assertEqual(result["stored_tracks"], len(album.tracks))
            self.assertEqual(len(result["failures"]), len(album.tracks))
            self.assertIn("spotify: read operation timed out", result["failures"][0]["error"])

    def test_rebuild_profile_uses_only_current_collection_and_filters_noise(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            song_dir = root / "songs"
            user_dir = root / "users"
            song_store = JsonSongStore(song_dir)
            user_store = JsonProfileStore(user_dir)

            selected = SongProfile.empty("selected")
            selected.metadata["artist"] = "Selected Artist"
            selected.genres = {"rock": 1.0}
            selected.source_tags["lastfm_track_tags"] = {
                "rock": 1.0,
                "1979": 0.8,
                "selected artist": 0.6,
                "wrong track streaming": 0.5,
            }
            ignored = SongProfile.empty("ignored")
            ignored.metadata["artist"] = "Ignored Artist"
            ignored.genres = {"jazz": 1.0}
            same_artist = SongProfile.empty("same-artist")
            same_artist.metadata["artist"] = "SELECTED ARTIST"
            same_artist.genres = {"rock": 1.0}
            song_store.save(selected)
            song_store.save(ignored)
            song_store.save(same_artist)
            user_store.save(
                UserProfile(
                    user_id="user-1",
                    collection_song_ids=["selected", "same-artist"],
                    artist_preferences={"Ignored Artist": 1.0},
                    genre_preferences={"jazz": 1.0},
                    tag_preferences={"1979": 1.0},
                )
            )

            rebuild_user_profile(
                "user-1",
                song_data_dir=song_dir,
                user_data_dir=user_dir,
            )

            rebuilt = user_store.load("user-1")
            self.assertEqual(
                rebuilt.collection_song_ids,
                ["selected", "same-artist"],
            )
            self.assertEqual(
                rebuilt.artist_preferences,
                {"Selected Artist": 1.0},
            )
            self.assertEqual(rebuilt.genre_preferences, {"rock": 1.0})
            self.assertEqual(rebuilt.tag_preferences, {"rock": 1.0})


if __name__ == "__main__":
    unittest.main()

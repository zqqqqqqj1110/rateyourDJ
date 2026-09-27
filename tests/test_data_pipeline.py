import io
import json
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from rateyourdj.contracts import make_song_id, validate_record
from rateyourdj.data_pipeline.catalog import build_catalog, read_jsonl
from rateyourdj.data_pipeline.http_cache import CachedJsonClient, HttpError, HttpResponse
from rateyourdj.data_pipeline.playback import PlaybackResolver, youtube_match
from rateyourdj.data_pipeline.popularity import (apply_buckets, global_percentile,
                                                 iter_recording_rows, reservoir_sample_mbids)
from rateyourdj.data_pipeline.report import catalog_report
from rateyourdj.data_pipeline.seeds import resolve_seed
from rateyourdj.data_pipeline.sources import (_flatten_dicts, is_studio_recording, is_variant_title,
                                             split_title, title_parts)
from rateyourdj.data_pipeline.user_context import (load_user_context, new_user_context,
                                                   save_user_context)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        status, payload, hdrs = self.responses.pop(0)
        return HttpResponse(status, hdrs, json.dumps(payload).encode())


class CachedClientTest(unittest.TestCase):
    def test_caches_and_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            sleeps = []
            t = FakeTransport([(429, {}, {"retry-after": "3"}), (200, {"ok": 1}, {})])
            c = CachedJsonClient(tmp, transport=t, sleep=sleeps.append)
            self.assertEqual(c.get("https://x.org/a"), {"ok": 1})
            self.assertIn(3.5, sleeps)
            self.assertEqual(c.get("https://x.org/a"), {"ok": 1})  # cache hit, no new call
            self.assertEqual(len(t.calls), 2)
            self.assertEqual(c.stats["cache_hits"], 1)

    def test_4xx_raises_and_404_can_be_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = FakeTransport([(400, {"e": 1}, {}), (404, {}, {})])
            c = CachedJsonClient(tmp, transport=t, sleep=lambda s: None)
            with self.assertRaises(HttpError):
                c.get("https://x.org/bad")
            self.assertEqual(c.get("https://x.org/none", allow_404=True), {"_not_found": True})

    def test_post_body_is_part_of_cache_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = FakeTransport([(200, [1], {}), (200, [2], {})])
            c = CachedJsonClient(tmp, transport=t, sleep=lambda s: None)
            self.assertEqual(c.post("https://x.org/p", {"a": 1}), [1])
            self.assertEqual(c.post("https://x.org/p", {"a": 2}), [2])


class SourcesTest(unittest.TestCase):
    def test_flatten_accepts_nested_shapes(self):
        self.assertEqual(len(_flatten_dicts([[{"a": 1}], {"b": 2}])), 2)
        self.assertEqual(_flatten_dicts({"payload": [{"a": 1}]}), [{"a": 1}])
        self.assertEqual(_flatten_dicts({"_not_found": True}), [])

    def test_variant_detection_ignores_base_title(self):
        self.assertFalse(is_variant_title("Live Forever"))
        self.assertFalse(is_variant_title("Live Forever - Remastered"))
        self.assertFalse(is_variant_title("Time - 2011 Remaster"))
        self.assertTrue(is_variant_title("Live Forever (Live at Maine Road)"))
        self.assertTrue(is_variant_title("Wonderwall - Demo"))
        self.assertTrue(is_variant_title("Slide Away", "BBC session"))
        self.assertTrue(is_variant_title("Round Are Way (White Room 26/1/96)"))
        self.assertTrue(is_variant_title("Pigs on the Wing (8-track version)"))
        self.assertTrue(is_variant_title("Live Forever (Monnow Valley version)"))
        self.assertEqual(split_title("Shine On You Crazy Diamond (Parts I–V)"),
                         ("Shine On You Crazy Diamond", "Parts I–V"))
        self.assertEqual(split_title("Shine On You Crazy Diamond, Parts I–V")[0],
                         "Shine On You Crazy Diamond")
        self.assertEqual(title_parts("Shine On You Crazy Diamond, Parts I–V"), set(range(1, 6)))
        self.assertEqual(title_parts("Shine On You Crazy Diamond (Pts. 1-5)"), set(range(1, 6)))
        self.assertEqual(title_parts("Shine On You Crazy Diamond (Parts 1–5, 7)"), {1, 2, 3, 4, 5, 7})
        self.assertEqual(title_parts("Shine On You Crazy Diamond (Part IX)"), {9})
        self.assertEqual(title_parts("Time"), set())

    def test_studio_heuristic(self):
        live = {"title": "Time (live)", "disambiguation": "", "releases": []}
        album = {"title": "Time", "disambiguation": "",
                 "releases": [{"status": "Official", "secondary_types": []}]}
        comp = {"title": "Time", "disambiguation": "",
                "releases": [{"status": "Official", "secondary_types": ["compilation"]}]}
        self.assertFalse(is_studio_recording(live))
        self.assertTrue(is_studio_recording(album))
        self.assertFalse(is_studio_recording(comp))


def _mb_hit(mbid, title, artist="Pink Floyd", amb="pf"):
    return {"mbid": mbid, "title": title, "score": 100, "disambiguation": "", "video": False,
            "artist_credit": artist, "artists": [{"name": artist, "mbid": amb}], "isrcs": [],
            "releases": [{"status": "Official", "primary_type": "Album", "secondary_types": [],
                          "date": "1975-09-12"}]}


class FakeMB:
    def __init__(self, hits=None, tag_artists=None):
        self.hits = hits or {}
        self.tag_artists = tag_artists or {}
        self.http = SimpleNamespace(stats={})

    def search_recordings(self, title, artist, limit=25):
        return self.hits.get(title, [])

    def lookup_recording(self, mbid):
        return {"title": "t-" + mbid, "artist_credit": "a", "isrcs": ["ISRC" + mbid],
                "first_release_date": "1975"}

    def search_artists_by_tag(self, tag, limit=100, offset=0):
        return self.tag_artists.get(tag, [])


class FakeLB:
    def __init__(self, similar=None, top=None):
        self.similar = similar or {}
        self.top = top or {}
        self.http = SimpleNamespace(stats={})

    def similar_artists(self, mbid, algorithm=None):
        return self.similar.get(mbid, [])

    def top_recordings_for_artist(self, mbid):
        return self.top.get(mbid, [])

    def recording_popularity(self, mbids):
        return {m: {"listen_count": 10, "listener_count": 5} for m in mbids}

    def recording_metadata(self, mbids):
        return {m: {"title": None, "artists": [], "release": {"year": 1999},
                    "recording_tags": [("britpop", 4)], "artist_tags": [("rock", 9)],
                    "release_group_tags": []} for m in mbids}


def _top(mbid, title, listeners, artist="Pink Floyd", amb="pf"):
    return {"recording_mbid": mbid, "title": title, "artist_credit": artist, "artist_mbids": [amb],
            "release_mbid": None, "release_title": None, "length_ms": 1000,
            "listen_count": listeners * 3, "listener_count": listeners}


class SeedResolutionTest(unittest.TestCase):
    def test_prefers_listenbrainz_most_listened_matching_title(self):
        mb = FakeMB({"Time": [_mb_hit("mb1", "Time")]})
        lb = FakeLB(top={"pf": [_top("lb-live", "Time (live)", 900), _top("lb1", "Time", 500),
                                _top("lb2", "Time - 2011 Remaster", 800)]})
        r = resolve_seed(mb, lb, "Pink Floyd", {"title": "Time"})
        self.assertEqual(r["recording_mbid"], "lb2")
        self.assertEqual(r["method"], "listenbrainz_top_recording")
        self.assertEqual(r["song_id"], make_song_id(recording_mbid="lb2")[0])

    def test_shine_on_picks_parts_i_to_v(self):
        # real ListenBrainz titles for Pink Floyd (2026-09): the unsplit remaster version has
        # the most listeners, but the seed is the album's Parts I–V
        seed = {"title": "Shine On You Crazy Diamond", "parts": [1, 5]}
        # MusicBrainz search titles carry no part numbers (as observed live)
        mb = FakeMB({seed["title"]: [_mb_hit("mb1", "Shine On You Crazy Diamond")]})
        lb = FakeLB(top={"pf": [_top("full", "Shine On You Crazy Diamond", 60891),
                                _top("a", "Shine On You Crazy Diamond, Parts I–V", 29728),
                                _top("b", "Shine On You Crazy Diamond, Parts VI–IX", 25872),
                                _top("c", "Shine On You Crazy Diamond (Pts. 1-5)", 1918),
                                _top("d", "Shine On You Crazy Diamond (Parts 1–5, 7)", 80)]})
        self.assertEqual(resolve_seed(mb, lb, "Pink Floyd", seed)["recording_mbid"], "a")

    def test_pinned_recording_wins(self):
        seed = {"title": "Time", "recording_mbid": "pinned-1"}
        r = resolve_seed(FakeMB({"Time": [_mb_hit("mb1", "Time")]}), FakeLB(), "Pink Floyd", seed)
        self.assertEqual((r["recording_mbid"], r["method"]), ("pinned-1", "pinned"))

    def test_falls_back_to_musicbrainz_and_reports_unresolved(self):
        mb = FakeMB({"Time": [_mb_hit("mb1", "Time")]})
        self.assertEqual(resolve_seed(mb, FakeLB(), "Pink Floyd", {"title": "Time"})["method"],
                         "musicbrainz_search")
        r = resolve_seed(FakeMB(), FakeLB(), "Pink Floyd", {"title": "Nope"})
        self.assertEqual(r["status"], "unresolved")


class UserContextTest(unittest.TestCase):
    def test_save_load_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = new_user_context("participant_001")
            save_user_context(ctx, Path(tmp) / "users")
            self.assertEqual(load_user_context("participant_001", Path(tmp) / "users", None)["user_id"],
                             "participant_001")
            legacy = Path(tmp) / "legacy"
            legacy.mkdir()
            (legacy / "demo-user.json").write_text(json.dumps(
                {"user_id": "demo-user", "version": 1, "collection_song_ids": []}))
            self.assertEqual(load_user_context("demo-user", Path(tmp) / "users", legacy)["user_id"],
                             "demo-user")


class CatalogBuildTest(unittest.TestCase):
    def test_build_expands_dedupes_and_keeps_seeds(self):
        seed_mbid = "seed-rec"
        context = new_user_context("participant_001")
        context["seed_branches"] = [{"branch_id": "oasis", "label": "", "seed_artists_mbid": ["oa"],
                                     "seed_song_ids": [make_song_id(recording_mbid=seed_mbid)[0]],
                                     "seed_recording_mbids": [seed_mbid]}]
        config = {"expansion": {"similar_hop1": 2, "similar_hop2": 1, "max_artists_per_branch": 3,
                                "top_recordings_per_artist": 2, "deep_recordings_per_artist": 0},
                  "branches": [{"branch_id": "oasis", "expansion_tags": ["britpop"]}]}
        lb = FakeLB(
            similar={"oa": [{"mbid": "blur", "name": "Blur", "score": 2.0},
                            {"mbid": "pulp", "name": "Pulp", "score": 1.0}],
                     "blur": [{"mbid": "gene", "name": "Gene", "score": 1.0}]},
            top={"oa": [_top("o1", "Live Forever", 900, "Oasis", "oa"),
                        _top("o2", "Live Forever - Remastered", 950, "Oasis", "oa"),
                        _top("o3", "Live Forever (Live at Maine Road)", 99, "Oasis", "oa"),
                        _top("o4", "Slide Away", 400, "Oasis", "oa")],
                 "blur": [_top("b1", "Tender", 300, "Blur", "blur")]})
        mb = FakeMB(tag_artists={"britpop": [{"mbid": "tiny", "name": "Tiny Band"}]})
        with tempfile.TemporaryDirectory() as tmp:
            manifest = build_catalog(context, config, lb=lb, mb=mb, catalog_root=tmp, log=lambda *a: None)
            songs = read_jsonl(Path(tmp) / "processed" / "songs.jsonl")
            artists = read_jsonl(Path(tmp) / "processed" / "artists.jsonl")
        self.assertEqual(len(artists), 3)  # cap: oa + top-2 by affinity
        self.assertNotIn("tiny", {a["mbid"] for a in artists})  # low affinity tag artist cut by cap
        by_mbid = {s["external_ids"]["musicbrainz_recording"]: s for s in songs}
        self.assertIn("o2", by_mbid)                      # remaster with more listeners wins
        self.assertNotIn("o1", by_mbid)
        self.assertIn("o1", by_mbid["o2"]["external_ids"]["musicbrainz_recording_merged"])
        self.assertNotIn("o3", by_mbid)                   # live version dropped
        self.assertIn(seed_mbid, by_mbid)                 # seed added explicitly
        self.assertTrue(by_mbid[seed_mbid]["is_seed"])
        self.assertEqual(manifest["counts"]["seeds_added_explicitly"], 1)
        for s in songs:
            validate_record("song_profile", s)
            self.assertEqual(s["popularity"]["bucket"], "unknown")
        self.assertEqual(by_mbid["b1"]["branch_affinity"]["oasis"], 0.8)


class SamplingTest(unittest.TestCase):
    def test_stratified_pick_reaches_deep_cuts(self):
        from rateyourdj.data_pipeline.catalog import stratified_pick
        rows = [{"rank": i} for i in range(100)]
        picked = [r["rank"] for r in stratified_pick(rows, 10, 5)]
        self.assertEqual(picked[:10], list(range(10)))
        self.assertEqual(picked[10:], [10, 28, 46, 64, 82])

    def test_bootleg_releases_and_parts_dedupe(self):
        from rateyourdj.data_pipeline.catalog import _dedupe_key
        from rateyourdj.data_pipeline.sources import is_bootleg_release
        self.assertTrue(is_bootleg_release("1975-04-26: Dogs and Sheeps: L.A. Sports Arena"))
        self.assertTrue(is_bootleg_release("Best of North American Tour 1977"))
        self.assertTrue(is_bootleg_release("Nassau Coliseum 1975 2nd Night: Cassette Master"))
        self.assertFalse(is_bootleg_release("Wish You Were Here"))
        self.assertFalse(is_bootleg_release("The Dark Side of the Moon"))
        self.assertNotEqual(_dedupe_key("Pink Floyd", "Shine On You Crazy Diamond, Parts I–V"),
                            _dedupe_key("Pink Floyd", "Shine On You Crazy Diamond, Parts VI–IX"))
        self.assertEqual(_dedupe_key("Oasis", "Live Forever"),
                         _dedupe_key("Oasis", "Live Forever - Remastered"))
        self.assertEqual(_dedupe_key("The Red Hot Chili Peppers", "Otherside", ["rhcp"]),
                         _dedupe_key("Red Hot Chili Peppers", "Otherside", ["rhcp"]))
        self.assertTrue(is_bootleg_release("Wish You Were Here: Trance Remixes Limited Edition"))


class PopularityTest(unittest.TestCase):
    def _dump(self, tmp, rows):
        data = "".join("\t".join(r) + "\n" for r in rows).encode()
        path = Path(tmp) / "mbdump.tar.bz2"
        with tarfile.open(path, "w:bz2") as tar:
            for name, payload in (("mbdump/artist", b"x\n"), ("mbdump/recording", data)):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        return path

    def test_streams_recording_table_and_skips_videos(self):
        rows = [[str(i), f"gid{i}", "n", "1", "", "", "0", "", "t" if i % 5 == 0 else "f"]
                for i in range(1, 101)]
        with tempfile.TemporaryDirectory() as tmp:
            sample, seen = reservoir_sample_mbids(iter_recording_rows(self._dump(tmp, rows)), 10,
                                                  log=lambda *a: None)
            again, _ = reservoir_sample_mbids(iter_recording_rows(self._dump(tmp, rows)), 10,
                                              log=lambda *a: None)
        self.assertEqual(seen, 80)
        self.assertEqual(len(sample), 10)
        self.assertEqual(sample, again)  # deterministic seed
        self.assertFalse({f"gid{i}" for i in range(5, 101, 5)} & set(sample))

    def test_percentile_and_buckets(self):
        counts = sorted([1] * 50 + [2] * 40 + [10] * 9 + [1000] * 1)
        self.assertEqual(global_percentile(None, counts), None)
        self.assertEqual(global_percentile(0, counts), 0.0)
        self.assertAlmostEqual(global_percentile(1, counts), 0.25)
        self.assertAlmostEqual(global_percentile(5000, counts), 1.0)
        # bucket-v2: head = top 1%, mid = top 1–10%
        songs = [{"genres": [], "popularity": {"listener_count": c}} for c in (2, 10, 5000, None)]
        dist = apply_buckets(songs, {"sorted_listener_counts": counts, "bucket_version": "bucket-v1",
                                     "dump": "t", "population_sample_size": 100})
        self.assertEqual([s["popularity"]["bucket"] for s in songs], ["tail", "mid", "head", "unknown"])
        self.assertEqual(dist, {"tail": 1, "mid": 1, "head": 1, "unknown": 1})


class FakeYouTube:
    api_key = "k"

    def __init__(self, results, verified=True):
        self.results, self.verified, self.searches = results, verified, 0

    def search(self, title, artist):
        self.searches += 1
        return self.results

    def verify(self, vid):
        return {"title": self.results[0]["title"], "channel": self.results[0]["channel"]} if self.verified else None


def _song(bucket, title="Obscure Song", artist="Tiny Band"):
    return {"song_id": make_song_id(artist=artist, title=title)[0], "title": title,
            "artist_credit": artist, "artists": [], "popularity": {"bucket": bucket},
            "external_ids": {"musicbrainz_recording": "m1", "isrc": []}}


class PlaybackTest(unittest.TestCase):
    NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)

    def test_tail_uses_verified_youtube_and_caches(self):
        yt = FakeYouTube([{"video_id": "v1", "title": "Tiny Band - Obscure Song", "channel": "x"}])
        with tempfile.TemporaryDirectory() as tmp:
            r = PlaybackResolver(Path(tmp) / "c.json", youtube=yt, now=lambda: self.NOW)
            out = r.resolve(_song("tail"))
            self.assertEqual((out["source"], out["youtube_video"]), ("youtube", "v1"))
            r.resolve(_song("tail"))
            self.assertEqual(yt.searches, 1)  # second call served from cache

    def test_prefers_official_topic_channel_over_fan_upload(self):
        yt = FakeYouTube([
            {"video_id": "fan", "title": "Tiny Band - Obscure Song (lyrics)", "channel": "Fan123"},
            {"video_id": "topic", "title": "Obscure Song", "channel": "Tiny Band - Topic"},
        ])
        yt.verify = lambda vid: {"title": "Obscure Song", "channel": "Tiny Band - Topic"}
        with tempfile.TemporaryDirectory() as tmp:
            out = PlaybackResolver(Path(tmp) / "c.json", youtube=yt, now=lambda: self.NOW).resolve(_song("tail"))
        self.assertEqual((out["youtube_video"], out["official"]), ("topic", True))

    def test_unverified_or_mismatched_video_is_rejected(self):
        yt = FakeYouTube([{"video_id": "v1", "title": "Something Else", "channel": "x"}])
        with tempfile.TemporaryDirectory() as tmp:
            out = PlaybackResolver(Path(tmp) / "c.json", youtube=yt, now=lambda: self.NOW).resolve(_song("tail"))
        self.assertEqual((out["source"], out["reason"]), ("none", "no_verified_match"))

    def test_daily_quota_stops_searching(self):
        yt = FakeYouTube([])
        with tempfile.TemporaryDirectory() as tmp:
            r = PlaybackResolver(Path(tmp) / "c.json", youtube=yt, youtube_daily_search_budget=2,
                                 now=lambda: self.NOW)
            outs = [r.resolve(_song("tail", title=f"S{i}")) for i in range(3)]
        self.assertEqual(yt.searches, 2)
        self.assertEqual(outs[-1]["reason"], "youtube_quota")

    def test_head_uses_spotify_isrc(self):
        queries = []

        def spotify(q):
            queries.append(q)
            return [{"spotify_track_id": "sp1", "title": "Obscure Song", "artists": ["Tiny Band"]}]

        with tempfile.TemporaryDirectory() as tmp:
            out = PlaybackResolver(Path(tmp) / "c.json", mb=FakeMB(), spotify_search=spotify,
                                   now=lambda: self.NOW).resolve(_song("head"))
        self.assertEqual(out["verification_method"], "spotify_isrc")
        self.assertEqual(queries, ["isrc:ISRCm1"])

    def test_parts_titles_match_across_numbering(self):
        from rateyourdj.data_pipeline.playback import same_song_title
        title = "Shine On You Crazy Diamond, Parts I–V"
        self.assertTrue(same_song_title("Shine On You Crazy Diamond (Pts. 1-5)", title))
        self.assertTrue(same_song_title("Shine On You Crazy Diamond (Parts 1–5) - 2011 Remaster", title))
        self.assertFalse(same_song_title("Shine On You Crazy Diamond (Pts. 6-9)", title))
        self.assertTrue(youtube_match({"title": "Pink Floyd - Shine On You Crazy Diamond (Pts. 1-5)",
                                       "channel": "Pink Floyd"}, title, "Pink Floyd"))
        self.assertFalse(youtube_match({"title": "Pink Floyd - Shine On You Crazy Diamond (Pts. 6-9)",
                                        "channel": "Pink Floyd"}, title, "Pink Floyd"))

    def test_topic_channel_counts_as_artist(self):
        self.assertTrue(youtube_match({"title": "Obscure Song", "channel": "Tiny Band - Topic"},
                                      "Obscure Song", "Tiny Band"))


class ReportTest(unittest.TestCase):
    def test_report_counts(self):
        sid = make_song_id(recording_mbid="x")[0]
        songs = [{"song_id": sid, "title": "A", "artist_credit": "B", "artists": [{"mbid": "b"}],
                  "release": {"year": 1990}, "tags": [{"name": "rock"}], "genres": ["rock"],
                  "popularity": {"bucket": "tail", "listener_count": 3},
                  "branch_affinity": {"oasis": 0.8}, "playback": {"source": "none"}}]
        ctx = {"seed_branches": [{"seed_song_ids": [sid, "s_missing"]}]}
        report = catalog_report(songs, ctx)
        self.assertEqual(report["seed_match"], {"seeds": 2, "in_catalog": 1})
        self.assertEqual(report["per_branch"]["oasis"]["buckets"], {"tail": 1})


if __name__ == "__main__":
    unittest.main()

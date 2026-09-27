import json
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np  # noqa: F401
except ImportError:  # pragma: no cover
    np = None

from rateyourdj.contracts import make_song_id, validate_record

if np is not None:
    from rateyourdj.rag.documents import song_document
    from rateyourdj.rag.encoder import HashingEncoder
    from rateyourdj.rag.evaluate import Judges, run_evaluation
    from rateyourdj.rag.index import build_index, load_index
    from rateyourdj.rag.retrieval import Retriever, quotas

PSY = ["psychedelic rock", "progressive rock", "space rock"]
BRIT = ["britpop", "alternative rock", "jangle pop"]


def _song(title, artist, tags, bucket, listeners, pct):
    sid = make_song_id(artist=artist, title=title)[0]
    return {"schema_version": "song-profile/v2", "song_id": sid, "id_basis": "name", "title": title,
            "artist_credit": artist, "artists": [{"name": artist, "mbid": "mb-" + artist}],
            "release": {"title": f"{title} LP", "year": 1990}, "external_ids": {},
            "tags": [{"name": t, "weight": 1.0 - i * 0.1} for i, t in enumerate(tags)],
            "genres": tags[:3],
            "popularity": {"listener_count": listeners, "global_percentile": pct, "bucket": bucket,
                           "bucket_version": "bucket-v2"},
            "playback": {"source": "none", "verified": False}, "provenance": []}


def _catalog():
    songs = [_song("Time", "Floyd", PSY, "head", 190000, 0.9999),
             _song("Live Forever", "Oasis", BRIT, "head", 110000, 0.9999)]
    for i in range(40):
        songs.append(_song(f"Nebula {i}", f"Space Band {i % 8}", PSY, "tail" if i % 2 else "mid",
                           50 + i, 0.5 + i / 100))
        songs.append(_song(f"Guitar Anthem {i}", f"Brit Band {i % 8}", BRIT, "tail" if i % 2 else "head",
                           60 + i, 0.5 + i / 100 if i % 2 else 0.995))
    context = {"user_id": "participant_001", "exploration_level": 0.5,
               "exclusions": {"song_ids": [], "artists_mbid": [], "tags": []},
               "heard_song_ids": [], "recommended_song_ids": [],
               "seed_branches": [{"branch_id": "pink_floyd", "seed_song_ids": [songs[0]["song_id"]]},
                                 {"branch_id": "oasis", "seed_song_ids": [songs[1]["song_id"]]}]}
    return songs, context


@unittest.skipIf(np is None, "numpy not installed")
class DocumentAndIndexTest(unittest.TestCase):
    def test_document_is_fact_only(self):
        songs, _ = _catalog()
        doc = song_document(songs[0])
        self.assertEqual(doc, "A 1990s song by Floyd. Style: psychedelic rock, progressive rock, space rock.")
        self.assertNotIn("Time", doc)     # doc-v2: no titles (they caused word-collision matches)
        self.assertNotIn("190000", doc)   # popularity never enters the text

    def test_build_load_and_resume(self):
        songs, _ = _catalog()
        with tempfile.TemporaryDirectory() as tmp:
            idx = build_index(songs, HashingEncoder(64), catalog_version="c1", root=tmp, log=lambda *a: None)
            loaded = load_index(tmp)
            self.assertEqual(loaded.ids, idx.ids)
            self.assertEqual(loaded.matrix.shape, (len(songs), 64))
            self.assertAlmostEqual(float(np.linalg.norm(loaded.matrix[0])), 1.0, places=5)
            calls = []

            class Counting(HashingEncoder):
                def encode(self, texts, batch_size=32):
                    calls.append(len(texts))
                    return super().encode(texts, batch_size)

            enc = Counting(64)
            enc.name = HashingEncoder(64).name
            build_index(songs, enc, catalog_version="c1", root=tmp, log=lambda *a: None)
            self.assertEqual(calls, [])  # cached parts reused
            songs[0]["tags"].append({"name": "new tag", "weight": 0.1})
            build_index(songs, enc, catalog_version="c1", root=tmp, log=lambda *a: None)
            self.assertEqual(calls, [len(songs)])  # docs changed -> re-encoded


@unittest.skipIf(np is None, "numpy not installed")
class RetrieverTest(unittest.TestCase):
    def setUp(self):
        self.songs, self.context = _catalog()
        self.enc = HashingEncoder(1024)
        self.tmp = tempfile.TemporaryDirectory()
        self.index = build_index(self.songs, self.enc, catalog_version="c1", root=self.tmp.name,
                                 log=lambda *a: None)
        self.r = Retriever(self.songs, self.context, index=self.index, encoder=self.enc,
                           catalog_version="c1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_quotas_scale_with_exploration(self):
        low, high = quotas(30, 0.1, True), quotas(30, 0.9, True)
        self.assertEqual(sum(low.values()), 30)
        self.assertEqual(sum(high.values()), 30)
        self.assertGreater(high["tail"], low["tail"])
        self.assertGreater(high["explore"], low["explore"])
        self.assertEqual(quotas(30, 0.5, False)["semantic"], 0)

    def test_branch_hint_separates_branches_and_candidates_are_valid(self):
        pf = self.r.retrieve("", branch_hint="pink_floyd", limit=12)
        oa = self.r.retrieve("", branch_hint="oasis", limit=12)
        self.assertEqual(len(pf["candidates"]), 12)
        pf_titles = [c["title"] for c in pf["candidates"]]
        # the hashing stand-in is crude (boilerplate words dominate); require a clear majority
        self.assertGreaterEqual(sum(t.startswith("Nebula") for t in pf_titles), 8)
        self.assertGreaterEqual(sum(c["title"].startswith("Guitar") for c in oa["candidates"]), 8)
        for c in pf["candidates"]:
            validate_record("retrieval_candidate", c)
            self.assertTrue(c["evidence"])
            self.assertEqual(c["candidate_set_id"], pf["candidate_set_id"])

    def test_seeds_and_exclusions_never_returned(self):
        banned = self.songs[2]["song_id"]
        res = self.r.retrieve("", branch_hint="pink_floyd", limit=20, exclude_song_ids=[banned])
        ids = {c["song_id"] for c in res["candidates"]}
        self.assertNotIn(self.songs[0]["song_id"], ids)
        self.assertNotIn(banned, ids)

    def test_artist_cap_and_tail_min_relevance(self):
        res = self.r.retrieve("", branch_hint="pink_floyd", limit=20)
        per_artist = {}
        for c in res["candidates"]:
            per_artist[c["artist_credit"]] = per_artist.get(c["artist_credit"], 0) + 1
            if c["channels"][0]["channel"] == "tail":
                self.assertEqual(c["bucket"], "tail")
                self.assertGreaterEqual(c["relevance"], 0.85)
        self.assertLessEqual(max(per_artist.values()), 3)

    def test_request_text_steers_branch_weights(self):
        res = self.r.retrieve("psychedelic space rock nebula", limit=10)
        self.assertGreater(res["query"]["branch_weights"]["pink_floyd"], 0.5)
        self.assertIn("space rock", res["query"]["request_tags"])

    def test_named_seed_is_resolved_and_stripped(self):
        res = self.r.retrieve("来点像 Time 那样的歌", limit=10)
        q = res["query"]
        self.assertEqual(q["referenced_seeds"], [self.songs[0]["song_id"]])
        self.assertNotIn("Time", q["encoded_text"])
        self.assertEqual(q["branch_weights"]["pink_floyd"], 1.0)
        # a title word inside another word must not count as a reference
        self.assertEqual(self.r.retrieve("Timeless", limit=5)["query"]["referenced_seeds"], [])

    def test_seed_artist_is_resolved(self):
        res = self.r.retrieve("something like Oasis", limit=5)
        self.assertEqual(res["query"]["branch_weights"]["oasis"], 1.0)

    def test_relevance_is_hybrid_percentile(self):
        res = self.r.retrieve("", branch_hint="pink_floyd", limit=10)
        for c in res["candidates"]:
            self.assertGreaterEqual(c["relevance"], 0.0)
            self.assertLessEqual(c["relevance"], 1.0)
        self.assertGreater(sum(c["relevance"] for c in res["candidates"]) / 10, 0.7)

    def test_zh_request_maps_to_tags(self):
        res = self.r.retrieve("迷幻一点的", limit=5)
        self.assertIn("psychedelic rock", res["query"]["request_tags"])

    def test_rule_only_fallback_without_index(self):
        r = Retriever(self.songs, self.context, index=None, catalog_version="c1")
        res = r.retrieve("britpop", limit=10)
        self.assertEqual(res["fallback"], "rule_only")
        self.assertIsNone(res["index_version"])
        self.assertEqual(len(res["candidates"]), 10)
        self.assertNotIn("semantic", res["channel_counts"])

    def test_unknown_branch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.r.retrieve("", branch_hint="radiohead")

    def test_same_request_same_candidate_set(self):
        a = self.r.retrieve("britpop", limit=10)
        b = self.r.retrieve("britpop", limit=10)
        self.assertEqual(a["candidate_set_id"], b["candidate_set_id"])
        self.assertEqual([c["song_id"] for c in a["candidates"]], [c["song_id"] for c in b["candidates"]])

    def test_evaluation_runs_end_to_end(self):
        queries = [{"id": "pf-hint", "text": "", "branch_hint": "pink_floyd", "exploration": 0.5,
                    "expect": "pink_floyd"},
                   {"id": "oasis-hint", "text": "", "branch_hint": "oasis", "exploration": 0.5,
                    "expect": "oasis"}]
        rule = Retriever(self.songs, self.context, index=None, catalog_version="c1")
        seed_ids = {s for b in self.context["seed_branches"] for s in b["seed_song_ids"]}
        report = run_evaluation(queries, {"rule_only": rule, "rag": self.r}, Judges(self.r),
                                {s["song_id"]: s for s in self.songs}, seed_ids)
        for name in ("rule_only", "rag"):
            system = report["systems"][name]
            self.assertTrue(system["all_traceable"])
            self.assertTrue(system["all_evidence_valid"])
            self.assertLessEqual(system["branch_overlap_jaccard"], 0.25)
            self.assertIn("branch_accuracy_vec", system["summary"])


if __name__ == "__main__":
    unittest.main()

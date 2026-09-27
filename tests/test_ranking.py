import unittest

from rateyourdj.ranking import rank_candidates, slot_plan, validate_selection


def _cand(i, artist, bucket, rel, tail, channel="semantic"):
    return {"song_id": f"s_{i:016x}", "title": f"Song {i}", "artist_credit": artist, "bucket": bucket,
            "relevance": rel, "tail_score": tail,
            "channels": [{"channel": channel, "rank": i, "raw_score": rel}],
            "evidence": [{"type": "seed_similarity", "detail": f"像种子 {i}", "ref": "s_seed"},
                         {"type": "popularity", "detail": "听众 10", "ref": "listenbrainz:x"}]}


def _set():
    cands = []
    for i in range(12):
        cands.append(_cand(i, f"Head {i % 6}", "head", 0.99 - i * 0.01, 0.01))
    for i in range(12, 24):
        cands.append(_cand(i, f"Tail {i % 6}", "tail", 0.90 - (i - 12) * 0.01, 0.8, "tail"))
    for i in range(24, 28):
        cands.append(_cand(i, f"Mid {i}", "mid", 0.70, 0.3, "explore"))
    return {"candidate_set_id": "cs_test", "query": {"exploration_level": 0.5}, "candidates": cands}


CTX = {"exploration_level": 0.5, "heard_song_ids": [], "seed_branches": [{"seed_song_ids": []}]}


class SlotPlanTest(unittest.TestCase):
    def test_default_mix_and_bounds(self):
        self.assertEqual(slot_plan(10, 0.5), {"relevant": 6, "tail": 3, "explore": 1})
        self.assertEqual(slot_plan(10, 0.0), {"relevant": 9, "tail": 1, "explore": 0})
        self.assertEqual(slot_plan(10, 1.0), {"relevant": 3, "tail": 5, "explore": 2})
        for e in (0.0, 0.3, 0.7, 1.0):
            plan = slot_plan(10, e)
            self.assertEqual(sum(plan.values()), 10)
            self.assertGreaterEqual(plan["tail"], 1)
            self.assertLessEqual(plan["tail"], 5)
            self.assertGreaterEqual(plan["relevant"], 3)


class RankerTest(unittest.TestCase):
    def test_relevance_only_takes_most_relevant(self):
        out = rank_candidates(_set(), CTX, strategy="rag-rel-v1", count=10)
        self.assertEqual(len(out["ranked"]), 10)
        self.assertTrue(all(r["bucket"] == "head" for r in out["ranked"]))
        rels = [r["score_breakdown"]["relevance"] for r in out["ranked"]]
        self.assertEqual(rels, sorted(rels, reverse=True))

    def test_tailmix_fills_slots_and_is_deterministic(self):
        a = rank_candidates(_set(), CTX, strategy="rag-tailmix-v1", count=10)
        b = rank_candidates(_set(), CTX, strategy="rag-tailmix-v1", count=10)
        self.assertEqual([r["song_id"] for r in a["ranked"]], [r["song_id"] for r in b["ranked"]])
        slots = [r["score_breakdown"]["slot"] for r in a["ranked"]]
        self.assertEqual((slots.count("relevant"), slots.count("tail"), slots.count("explore")), (6, 3, 1))
        self.assertGreaterEqual(sum(r["bucket"] == "tail" for r in a["ranked"]), 3)
        for r in a["ranked"]:
            bd = r["score_breakdown"]
            for key in ("relevance", "tail_score", "tail_relevance", "novelty", "diversity",
                        "exploration_fit", "penalties", "weights_version", "strategy"):
                self.assertIn(key, bd)

    def test_artist_cap(self):
        out = rank_candidates(_set(), CTX, strategy="rag-rel-v1", count=10, max_per_artist=1)
        artists = [r["artist_credit"] for r in out["ranked"]]
        self.assertEqual(len(artists), len(set(artists)))

    def test_heard_song_is_penalised(self):
        s = _set()
        top = s["candidates"][0]["song_id"]
        out = rank_candidates(s, {**CTX, "heard_song_ids": [top]}, strategy="rag-rel-v1", count=5)
        self.assertNotIn(top, [r["song_id"] for r in out["ranked"]])

    def test_near_duplicates_are_penalised(self):
        s = _set()
        first, second = s["candidates"][0]["song_id"], s["candidates"][1]["song_id"]
        sim = lambda a, b: 0.99 if {a, b} == {first, second} else 0.1  # noqa: E731
        out = rank_candidates(s, CTX, strategy="rag-tailmix-v1", count=10, similarity=sim)
        ids = [r["song_id"] for r in out["ranked"]]
        self.assertFalse(first in ids and second in ids)

    def test_ranker_output_passes_validator(self):
        s = _set()
        for strategy in ("rag-rel-v1", "rag-tailmix-v1"):
            out = rank_candidates(s, CTX, strategy=strategy, count=10)
            picks = [{"song_id": r["song_id"], "reason": r["reason"], "evidence_refs": r["evidence_refs"]}
                     for r in out["ranked"]]
            report = validate_selection(picks, s, count=10,
                                        min_tail=out["plan"]["tail"])
            self.assertTrue(report["ok"], report["errors"])


class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.s = _set()
        self.good = [{"song_id": c["song_id"], "reason": "r", "evidence_refs": [0]}
                     for c in self.s["candidates"][:10]]

    def test_out_of_set_song_is_rejected(self):
        bad = self.good[:9] + [{"song_id": "s_made_up", "reason": "r", "evidence_refs": [0]}]
        report = validate_selection(bad, self.s, count=10)
        self.assertFalse(report["ok"])
        self.assertEqual(report["out_of_set"], 1)

    def test_count_duplicates_evidence_and_reason(self):
        self.assertFalse(validate_selection(self.good[:9], self.s, count=10)["ok"])
        dup = self.good[:9] + [self.good[0]]
        self.assertFalse(validate_selection(dup, self.s, count=10)["ok"])
        bad_ref = self.good[:9] + [{**self.good[9], "evidence_refs": [7]}]
        self.assertFalse(validate_selection(bad_ref, self.s, count=10)["ok"])
        no_reason = self.good[:9] + [{**self.good[9], "reason": " "}]
        self.assertFalse(validate_selection(no_reason, self.s, count=10)["ok"])
        self.assertFalse(validate_selection("not a list", self.s, count=10)["ok"])

    def test_artist_cap_min_tail_and_exclusions(self):
        report = validate_selection(self.good, self.s, count=10, max_per_artist=1, min_tail=1,
                                    exclude_song_ids={self.good[0]["song_id"]})
        joined = " ".join(report["errors"])
        self.assertIn("max 1", joined)
        self.assertIn("tail", joined)
        self.assertIn("excluded", joined)

    def test_valid_selection_passes(self):
        self.assertTrue(validate_selection(self.good, self.s, count=10)["ok"])


if __name__ == "__main__":
    unittest.main()

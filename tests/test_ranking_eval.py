import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from rateyourdj.ranking import metrics as M
from rateyourdj.ranking.positives import build_weak_positives, positives_for

try:
    import numpy  # noqa: F401
    from test_rag import _catalog
    from rateyourdj.rag.encoder import HashingEncoder
    from rateyourdj.rag.index import build_index
    from rateyourdj.rag.retrieval import Retriever
    from rateyourdj.agent.factory import vector_similarity
    from rateyourdj.agent.llm import LLMError, ScriptedChat
    from rateyourdj.ranking.evaluate import evaluate, frozen_context
except ImportError:  # pragma: no cover
    numpy = None


def _songs(buckets):
    return {f"s{i}": {"song_id": f"s{i}", "artist_credit": f"A{i % 3}",
                      "popularity": {"bucket": b, "global_percentile": {"head": 0.995, "mid": 0.95, "tail": 0.5}[b]}}
            for i, b in enumerate(buckets)}


class MetricsTest(unittest.TestCase):
    def test_recall_and_ndcg(self):
        recs = ["a", "b", "c", "d"]
        self.assertEqual(M.hits_at_k(recs, {"a", "c", "z"}, 4), 2)
        self.assertAlmostEqual(M.recall_at_k(recs, {"a", "c", "z"}, 4), 2 / 3)
        self.assertAlmostEqual(M.recall_at_k(recs, {"a"}, 4), 1.0)          # capped by |positives|
        ideal = 1 + 1 / math.log2(3)
        self.assertAlmostEqual(M.ndcg_at_k(recs, {"a", "c"}, 4), (1 + 1 / math.log2(4)) / ideal)
        self.assertIsNone(M.recall_at_k(recs, set(), 4))
        self.assertEqual(M.ndcg_at_k(["x"], {"a"}, 1), 0.0)

    def test_tail_novelty_coverage(self):
        songs = _songs(["head", "tail", "tail", "mid"])
        self.assertEqual(M.tail_share(["s0", "s1"], songs), 0.5)
        self.assertAlmostEqual(M.novelty(["s0", "s1"], songs), (0.005 + 0.5) / 2)
        cov = M.coverage([["s1"], ["s1", "s2"]], songs)
        self.assertEqual(cov["tail_coverage"], 1.0)
        self.assertEqual(cov["unique_songs"], 2)

    def test_diversity_serendipity_hallucination(self):
        sim = lambda a, b: 1.0 if a[0] == b[0] else 0.0  # noqa: E731
        self.assertEqual(M.intra_list_diversity(["a1", "a2"], sim), 0.0)
        self.assertEqual(M.intra_list_diversity(["a1", "b1"], sim), 1.0)
        self.assertEqual(M.serendipity(["x", "y", "z"], relevant={"x", "y"}, expected={"x"}), 1 / 3)
        self.assertEqual(M.hallucination_rate(["x", "ghost"], {"x"}), 0.5)

    def test_evidence_accuracy_catches_invented_numbers(self):
        cands = {"s": {"evidence": [{"detail": "与种子《Time》的向量相似度 0.81"}, {"detail": "听众 95"}]}}
        good = [{"song_id": "s", "reason": "向量相似度 0.81，听众 95", "evidence_refs": [0, 1]}]
        invented = [{"song_id": "s", "reason": "1973 年的经典，听众 95", "evidence_refs": [1]}]
        bad_ref = [{"song_id": "s", "reason": "x", "evidence_refs": [5]}]
        self.assertEqual(M.evidence_accuracy(good, cands), 1.0)
        self.assertEqual(M.evidence_accuracy(invented, cands), 0.0)
        self.assertEqual(M.evidence_accuracy(bad_ref, cands), 0.0)


class WeakPositivesTest(unittest.TestCase):
    def test_maps_similar_recordings_into_catalog(self):
        songs = [
            {"song_id": "seed", "external_ids": {"musicbrainz_recording": "m-seed"}, "popularity": {"bucket": "head"}},
            {"song_id": "a", "external_ids": {"musicbrainz_recording": "m-a"}, "popularity": {"bucket": "tail"}},
            {"song_id": "b", "external_ids": {"musicbrainz_recording": "m-b0",
                                             "musicbrainz_recording_merged": ["m-b"]}, "popularity": {"bucket": "mid"}},
        ]
        ctx = {"seed_branches": [{"branch_id": "x", "seed_song_ids": ["seed"], "seed_recording_mbids": ["m-seed"]}]}
        lb = SimpleNamespace(similar_recordings=lambda mbid: [
            {"recording_mbid": "m-a", "score": 3}, {"recording_mbid": "m-b", "score": 2},
            {"recording_mbid": "m-unknown", "score": 1}, {"recording_mbid": "m-seed", "score": 1}])
        out = build_weak_positives(ctx, songs, lb)
        b = out["branches"]["x"]
        self.assertEqual(b["song_ids"], ["a", "b"])            # merged MBIDs resolve, seeds excluded
        self.assertEqual((b["returned"], b["in_catalog"]), (4, 2))
        self.assertEqual(positives_for(out, "x"), {"a", "b"})
        self.assertEqual(positives_for(out, "both"), {"a", "b"})


@unittest.skipIf(numpy is None, "numpy not installed")
class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.songs, ctx = _catalog()
        self.ctx = frozen_context({**ctx, "recommended_song_ids": ["x"]})
        enc = HashingEncoder(1024)
        self.tmp = tempfile.TemporaryDirectory()
        index = build_index(self.songs, enc, catalog_version="c1", root=self.tmp.name, log=lambda *a: None)
        self.retriever = Retriever(self.songs, self.ctx, index=index, encoder=enc, catalog_version="c1")
        tails = [s["song_id"] for s in self.songs if s["title"].startswith("Nebula") and s["popularity"]["bucket"] == "tail"]
        self.positives = {"version": "weak-pos-v1", "branches": {
            "pink_floyd": {"song_ids": tails[:8]}, "oasis": {"song_ids": []}}}
        self.queries = [{"id": "pf", "text": "", "branch_hint": "pink_floyd", "exploration": 0.5, "expect": "pink_floyd"},
                        {"id": "explore-0.9", "text": "", "branch_hint": "pink_floyd", "exploration": 0.9,
                         "expect": "pink_floyd"}]

    def tearDown(self):
        self.tmp.cleanup()

    def test_frozen_context_clears_history(self):
        self.assertEqual(self.ctx["recommended_song_ids"], [])

    def test_two_strategies_and_acceptance(self):
        rep = evaluate(self.queries, songs=self.songs, context=self.ctx, retriever=self.retriever,
                       positives=self.positives, similarity=vector_similarity(self.retriever), log=lambda *a: None)
        rel, mix = rep["summary"]["rag-rel-v1"], rep["summary"]["rag-tailmix-v1"]
        self.assertEqual(rel["hallucination_rate"], 0.0)
        self.assertEqual(mix["constraint_pass"], 1.0)
        self.assertGreaterEqual(mix["tail_exposure"], rel["tail_exposure"])
        self.assertIn("passed", rep["acceptance"])
        self.assertEqual(len(rep["per_query"]), 4)
        self.assertIn("0.9", rep["strata"]["rag-tailmix-v1"]["by_exploration"])

    def test_agent_system_falls_back_cleanly(self):
        llm = ScriptedChat([LLMError("down"), LLMError("down")])
        rep = evaluate(self.queries, songs=self.songs, context=self.ctx, retriever=self.retriever,
                       positives=self.positives, similarity=vector_similarity(self.retriever), llm=llm,
                       log=lambda *a: None)
        agent = rep["summary"]["agent-react-v1"]
        self.assertEqual(agent["fallback_rate"], 1.0)
        self.assertEqual(agent["hallucination_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()

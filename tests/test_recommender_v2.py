import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import numpy  # noqa: F401
    from test_rag import _catalog
    from rateyourdj.rag.encoder import HashingEncoder
    from rateyourdj.rag.index import build_index
    from rateyourdj.rag.retrieval import Retriever
except ImportError:  # pragma: no cover
    numpy = None

from rateyourdj.agent.llm import LLMError, ScriptedChat
from rateyourdj.agent.service import RecommenderV2
from rateyourdj.agent.factory import vector_similarity
from rateyourdj.contracts import ContractError
from rateyourdj.data_pipeline.user_context import load_user_context, new_user_context, save_user_context


def make_service(tmp: Path, llm=None):
    songs, ctx = _catalog()
    (tmp / "catalog" / "processed").mkdir(parents=True)
    with (tmp / "catalog" / "processed" / "songs.jsonl").open("w") as f:
        for s in songs:
            f.write(json.dumps(s) + "\n")
    context = new_user_context("participant_001")
    context["seed_branches"] = ctx["seed_branches"]
    save_user_context(context, tmp / "users")
    enc = HashingEncoder(1024)
    index = build_index(songs, enc, catalog_version="c1", root=tmp / "index", log=lambda *a: None)
    factory = lambda s, c: Retriever(s, c, index=index, encoder=enc, catalog_version="c1")  # noqa: E731
    return RecommenderV2(catalog_root=tmp / "catalog", users_root=tmp / "users",
                         candidates_root=tmp / "candidates", retriever_factory=factory, llm=llm,
                         similarity_factory=vector_similarity)


@unittest.skipIf(numpy is None, "numpy not installed")
class RecommenderV2Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name)
        self.svc = make_service(self.tmp)

    def tearDown(self):
        self.dir.cleanup()

    def test_pipeline_records_impressions_runs_and_context(self):
        out = self.svc.recommend("participant_001", "space rock", count=5, mode="pipeline")
        self.assertEqual(out["strategy_version"], "rag-tailmix-v1")
        self.assertEqual(len(out["recommendations"]), 5)
        r = out["recommendations"][0]
        for key in ("impression_id", "song_id", "bucket", "channel", "source_label", "reason",
                    "evidence_items", "playback", "track"):
            self.assertIn(key, r)
        lines = (self.tmp / "users" / "participant_001" / "impressions.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertTrue((self.tmp / "users" / "participant_001" / "runs" / f"{out['run_id']}.json").is_file())
        self.assertTrue((self.tmp / "candidates" / f"{out['candidate_set_id']}.json").is_file())
        ctx = load_user_context("participant_001", self.tmp / "users", None)
        self.assertEqual(len(ctx["recommended_song_ids"]), 5)

    def test_second_request_does_not_repeat_songs(self):
        a = self.svc.recommend("participant_001", "space rock", count=5, mode="pipeline")
        b = self.svc.recommend("participant_001", "space rock", count=5, mode="pipeline")
        self.assertFalse({r["song_id"] for r in a["recommendations"]} & {r["song_id"] for r in b["recommendations"]})

    def test_no_record_mode_writes_nothing(self):
        self.svc.recommend("participant_001", "space rock", count=5, mode="pipeline", record=False)
        self.assertFalse((self.tmp / "users" / "participant_001" / "impressions.jsonl").exists())

    def test_agent_mode_falls_back_when_llm_fails(self):
        svc = make_service(self.tmp / "b", llm=ScriptedChat([LLMError("down")]))
        out = svc.recommend("participant_001", "space rock", count=5, mode="agent")
        self.assertEqual(out["strategy_version"], "rag-tailmix-v1")
        self.assertIn("llm_error", out["fallback_reason"])
        self.assertEqual(len(out["recommendations"]), 5)

    def test_agent_mode_without_llm_reports_it(self):
        out = self.svc.recommend("participant_001", "space rock", count=5, mode="agent")
        self.assertEqual(out["fallback_reason"], "no LLM configured")

    def test_feedback_requires_known_impression(self):
        with self.assertRaises(LookupError):
            self.svc.record_feedback("participant_001", "imp_nope", event="liked")

    def test_feedback_updates_context(self):
        out = self.svc.recommend("participant_001", "space rock", count=5, mode="pipeline")
        imp = out["recommendations"][0]["impression_id"]
        sid = out["recommendations"][0]["song_id"]
        rec = self.svc.record_feedback("participant_001", imp, event="play_progress", seconds=42,
                                       survey={"heard_before": "yes", "relevance": 4})
        self.assertEqual(rec["song_id"], sid)
        self.assertEqual(rec["strategy_version"], "rag-tailmix-v1")
        self.svc.record_feedback("participant_001", out["recommendations"][1]["impression_id"], event="hide")
        ctx = load_user_context("participant_001", self.tmp / "users", None)
        self.assertIn(sid, ctx["heard_song_ids"])
        self.assertIn(out["recommendations"][1]["song_id"], ctx["exclusions"]["song_ids"])
        self.assertEqual(len(ctx["recent_feedback_ids"]), 2)

    def test_feedback_validation(self):
        out = self.svc.recommend("participant_001", "space rock", count=5, mode="pipeline")
        imp = out["recommendations"][0]["impression_id"]
        with self.assertRaises(ValueError):
            self.svc.record_feedback("participant_001", imp, event="loved_it")
        with self.assertRaises(ValueError):
            self.svc.record_feedback("participant_001", imp)
        with self.assertRaises(ContractError):
            self.svc.record_feedback("participant_001", imp, survey={"reject_reason": "meh"})


if __name__ == "__main__":
    unittest.main()

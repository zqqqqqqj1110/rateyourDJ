"""Stage 6: interleaving, study phase, playback lookup, saved list and data deletion."""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rateyourdj.agent.interleave import assign_labels, credit, team_draft

try:
    import numpy  # noqa: F401
    from test_recommender_v2 import make_service
except ImportError:  # pragma: no cover
    numpy = None

try:
    import flask  # noqa: F401
    from rateyourdj.web.app import create_app
except ImportError:  # pragma: no cover
    flask = None


class TeamDraftTest(unittest.TestCase):
    def test_no_duplicates_and_balanced_credit(self):
        rng = random.Random(0)
        a = ["s1", "s2", "s3", "s4", "s5"]
        b = ["s2", "s6", "s1", "s7", "s8"]
        for _ in range(200):
            shown = team_draft({"A": a, "B": b}, 6, rng)
            ids = [s for s, _ in shown]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(len(ids), 6)
            counts = {"A": 0, "B": 0}
            for _, arm in shown:
                counts[arm] += 1
            self.assertLessEqual(abs(counts["A"] - counts["B"]), 1)
            # each arm's songs appear in that arm's own order
            for arm, ranked in (("A", a), ("B", b)):
                mine = [s for s, x in shown if x == arm]
                self.assertEqual(mine, sorted(mine, key=ranked.index))

    def test_first_slot_is_random(self):
        rng = random.Random(1)
        firsts = {team_draft({"A": ["x1", "x2"], "B": ["y1", "y2"]}, 2, rng)[0][1] for _ in range(50)}
        self.assertEqual(firsts, {"A", "B"})

    def test_identical_lists_give_no_preference_under_random_clicks(self):
        # the same list for both arms: expected credit must be equal (unbiased)
        rng = random.Random(2)
        lst = [f"s{i}" for i in range(10)]
        totals = {"A": 0.0, "B": 0.0}
        for _ in range(4000):
            shown = team_draft({"A": lst, "B": lst}, 10, rng)
            clicked = {lst[0]: 1.0}  # user always likes the top song
            for arm, value in credit(shown, clicked).items():
                totals[arm] += value
        self.assertAlmostEqual(totals["A"] / 4000, 0.5, delta=0.05)

    def test_exhausted_arm(self):
        shown = team_draft({"A": ["a"], "B": ["b1", "b2", "b3"]}, 4, random.Random(3))
        self.assertEqual([s for s, _ in shown].count("a"), 1)
        self.assertEqual(len(shown), 4)

    def test_labels(self):
        seen = {tuple(sorted(assign_labels(["x", "y"], random.Random(i)).items())) for i in range(20)}
        self.assertEqual(len(seen), 2)
        with self.assertRaises(ValueError):
            assign_labels(["x", "x"], random.Random(0))


class _FakePlayback:
    def __init__(self):
        self.calls = 0
        self.saved = 0

    def resolve(self, song):
        self.calls += 1
        return {"source": "youtube", "url": "https://www.youtube.com/watch?v=abcdefghijk",
                "youtube_video": "abcdefghijk", "verified": True,
                "verification_method": "youtube_videos_list", "checked_at": "2026-09-29T00:00:00+00:00"}

    def save(self):
        self.saved += 1


@unittest.skipIf(numpy is None, "numpy not installed")
class StudyServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name)
        self.svc = make_service(self.tmp)
        self.user_dir = self.tmp / "users" / "participant_001"

    def tearDown(self):
        self.dir.cleanup()

    def _lines(self, name):
        path = self.user_dir / name
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def test_interleaved_request_is_blind_and_attributed(self):
        out = self.svc.recommend("participant_001", "space rock", count=6,
                                 interleave=["rag-rel-v1", "rag-tailmix-v1"])
        recs = out["recommendations"]
        self.assertEqual(len(recs), 6)
        self.assertEqual(len({r["song_id"] for r in recs}), 6)
        for r in recs:  # the response must not reveal which strategy produced which song
            self.assertNotIn("strategy_version", r)
        self.assertIsNone(out["strategy_version"])
        imps = self._lines("impressions.jsonl")
        self.assertEqual(len(imps), 6)
        arms = imps[0]["interleaving"]["arms"]
        self.assertEqual(set(arms.values()), {"rag-rel-v1", "rag-tailmix-v1"})
        for imp in imps:
            self.assertEqual(imp["strategy_version"], arms[imp["interleaving"]["arm"]])
            self.assertEqual(imp["phase"], "dev")
        log = self._lines("interleavings.jsonl")
        self.assertEqual(len(log), 1)
        self.assertEqual([x["song_id"] for x in log[0]["shown"]], [r["song_id"] for r in recs])
        # both arms' full runs are kept for offline analysis
        for run_id in log[0]["run_ids"].values():
            self.assertTrue((self.user_dir / "runs" / f"{run_id}.json").is_file())
        # feedback inherits the attribution
        fb = self.svc.record_feedback("participant_001", recs[0]["impression_id"], event="liked")
        self.assertEqual(fb["interleaving"]["arm"], imps[0]["interleaving"]["arm"])
        # only shown songs count as recommended (not the arms' unshown picks)
        ctx = json.loads((self.user_dir / "context.json").read_text())
        self.assertEqual(sorted(ctx["recommended_song_ids"]), sorted(r["song_id"] for r in recs))

    def test_unknown_strategy_rejected(self):
        with self.assertRaises(ValueError):
            self.svc.recommend("participant_001", "x", count=3, strategy="grpo-v1")

    def test_custom_arm_runner(self):
        from rateyourdj.agent.loop import run_pipeline

        def arm(toolbox, **kw):
            run = run_pipeline(toolbox, strategy="rag-rel-v1", **kw)
            run["strategy_version"] = "custom-v1"
            return run
        svc = make_service(self.tmp / "c")
        svc.arm_runners["custom-v1"] = arm
        self.assertIn("custom-v1", svc.available_strategies())
        out = svc.recommend("participant_001", "space rock", count=4, interleave=["custom-v1", "rag-tailmix-v1"])
        self.assertEqual(len(out["recommendations"]), 4)

    def test_final_phase_is_stamped(self):
        svc = make_service(self.tmp / "f")
        svc.feedback_phase = "final"
        out = svc.recommend("participant_001", "space rock", count=3, mode="pipeline")
        fb = svc.record_feedback("participant_001", out["recommendations"][0]["impression_id"],
                                 survey={"relevance": 4, "too_unfamiliar": False})
        self.assertEqual(fb["phase"], "final")
        self.assertEqual(out["phase"], "final")

    def test_survey_validation(self):
        out = self.svc.recommend("participant_001", "space rock", count=3, mode="pipeline")
        imp = out["recommendations"][0]["impression_id"]
        for bad in ({"relevance": 6}, {"relevance": True}, {"would_save": "yes"}, {"mood": 1},
                    {"reject_reason": "boring"}):
            with self.assertRaises(ValueError):
                self.svc.record_feedback("participant_001", imp, survey=bad)
        ok = self.svc.record_feedback("participant_001", imp, event="hide",
                                      survey={"reject_reason": "not_in_mood_to_explore"})
        self.assertEqual(ok["survey"]["reject_reason"], "not_in_mood_to_explore")

    def test_playback_lookup_and_search_links(self):
        out = self.svc.recommend("participant_001", "space rock", count=3, mode="pipeline")
        pb = out["recommendations"][0]["playback"]
        self.assertEqual(pb["source"], "none")
        self.assertIn("youtube.com/results", pb["search_links"]["youtube"])
        svc = make_service(self.tmp / "p")
        svc.playback = _FakePlayback()
        out = svc.recommend("participant_001", "space rock", count=3, mode="pipeline")
        pb = out["recommendations"][0]["playback"]
        self.assertEqual(pb["source"], "youtube")
        self.assertTrue(pb["verified"])
        self.assertIsNone(pb["search_links"])
        self.assertEqual(svc.playback.saved, 1)

    def test_playback_errors_do_not_break_recommendations(self):
        class Broken:
            def resolve(self, song):
                raise OSError("offline")

            def save(self):
                pass
        svc = make_service(self.tmp / "b")
        svc.playback = Broken()
        out = svc.recommend("participant_001", "space rock", count=3, mode="pipeline")
        self.assertEqual(len(out["recommendations"]), 3)
        self.assertEqual(out["recommendations"][0]["playback"]["source"], "none")

    def test_saved_songs(self):
        out = self.svc.recommend("participant_001", "space rock", count=3, mode="pipeline")
        recs = out["recommendations"]
        self.svc.record_feedback("participant_001", recs[0]["impression_id"], event="saved")
        self.svc.record_feedback("participant_001", recs[1]["impression_id"], event="liked")
        saved = self.svc.saved_songs("participant_001")
        self.assertEqual([s["song_id"] for s in saved], [recs[0]["song_id"]])

    def test_delete_interactions_keeps_seeds(self):
        out = self.svc.recommend("participant_001", "space rock", count=3, mode="pipeline")
        self.svc.record_feedback("participant_001", out["recommendations"][0]["impression_id"], event="hide")
        cs_file = self.tmp / "candidates" / f"{out['candidate_set_id']}.json"
        self.assertTrue(cs_file.exists())
        res = self.svc.delete_user_data("participant_001", "interactions")
        self.assertEqual(res["removed"]["impressions.jsonl"], 3)
        self.assertFalse((self.user_dir / "feedback.jsonl").exists())
        self.assertFalse((self.user_dir / "runs").exists())
        self.assertFalse(cs_file.exists())
        ctx = json.loads((self.user_dir / "context.json").read_text())
        self.assertEqual(ctx["recommended_song_ids"], [])
        self.assertEqual(ctx["exclusions"]["song_ids"], [])
        self.assertTrue(ctx["seed_branches"])
        # still usable afterwards
        self.assertEqual(len(self.svc.recommend("participant_001", "space rock", count=3,
                                                mode="pipeline")["recommendations"]), 3)

    def test_delete_all(self):
        self.svc.recommend("participant_001", "space rock", count=3, mode="pipeline")
        self.svc.delete_user_data("participant_001", "all")
        self.assertFalse(self.user_dir.exists())
        self.assertFalse(self.svc.has_user("participant_001"))
        with self.assertRaises(ValueError):
            self.svc.delete_user_data("../etc", "all")


@unittest.skipIf(flask is None or numpy is None, "flask or numpy not installed")
class StudyWebTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        tmp = Path(self.dir.name)
        self.app = create_app(profile_dir=tmp / "p", song_dir=tmp / "s", trajectory_dir=tmp / "t",
                              session_dir=tmp / "se", auto_configure_music_provider=False,
                              recommender_v2=make_service(tmp / "v2"))
        self.client = self.app.test_client()

    def tearDown(self):
        self.dir.cleanup()

    def test_index_defaults_to_participant(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('value="participant_001"', html)

    def test_status(self):
        body = self.client.get("/api/v2/status?user_id=participant_001").get_json()
        self.assertTrue(body["known_user"])
        self.assertEqual(body["phase"], "dev")
        self.assertIn("rag-tailmix-v1", body["strategies"])
        self.assertEqual({b["branch_id"] for b in body["branches"]}, {"pink_floyd", "oasis"})

    def test_interleave_endpoint(self):
        res = self.client.post("/api/v1/agent/recommend", json={
            "user_id": "participant_001", "message": "space rock", "constraints": {"limit": 4},
            "interleave": ["rag-rel-v1", "rag-tailmix-v1"]})
        self.assertEqual(res.status_code, 201, res.get_json())
        self.assertIsNotNone(res.get_json()["interleaving"])
        for bad in (["rag-rel-v1"], ["rag-rel-v1", "rag-rel-v1"], ["rag-rel-v1", "nope"]):
            res = self.client.post("/api/v1/agent/recommend", json={
                "user_id": "participant_001", "message": "x", "interleave": bad})
            self.assertEqual(res.status_code, 400)

    def test_saved_and_delete_endpoints(self):
        body = self.client.post("/api/v1/agent/recommend", json={
            "user_id": "participant_001", "message": "space rock", "mode": "rules",
            "constraints": {"limit": 3}}).get_json()
        imp = body["recommendations"][0]["impression_id"]
        self.client.post("/api/v1/agent/feedback", json={"user_id": "participant_001",
                                                         "impression_id": imp, "event": "saved"})
        saved = self.client.get("/api/v2/users/participant_001/saved").get_json()
        self.assertEqual(saved["total"], 1)
        refused = self.client.delete("/api/v2/users/participant_001/data", json={"scope": "interactions"})
        self.assertEqual(refused.status_code, 400)
        ok = self.client.delete("/api/v2/users/participant_001/data",
                                json={"scope": "interactions", "confirm": "participant_001"})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        self.assertEqual(self.client.get("/api/v2/users/participant_001/saved").get_json()["total"], 0)
        self.assertEqual(self.client.get("/api/v2/users/nobody/saved").status_code, 404)


if __name__ == "__main__":
    unittest.main()

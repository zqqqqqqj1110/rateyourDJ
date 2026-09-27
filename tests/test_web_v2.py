import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import flask  # noqa: F401
    import numpy  # noqa: F401
    from test_recommender_v2 import make_service
    from rateyourdj.web.app import create_app
except ImportError:  # pragma: no cover - Flask / numpy not installed
    flask = None


@unittest.skipIf(flask is None, "flask or numpy not installed")
class WebV2Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        tmp = Path(self.dir.name)
        self.app = create_app(profile_dir=tmp / "p", song_dir=tmp / "s", trajectory_dir=tmp / "t",
                              session_dir=tmp / "se",
                              auto_configure_music_provider=False,
                              recommender_v2=make_service(tmp / "v2"))
        self.client = self.app.test_client()

    def tearDown(self):
        self.dir.cleanup()

    def test_v2_recommend_and_feedback_roundtrip(self):
        res = self.client.post("/api/v1/agent/recommend", json={
            "user_id": "participant_001", "message": "space rock", "mode": "rules",
            "constraints": {"limit": 5}, "exploration_level": 0.5, "include_trace": True})
        self.assertEqual(res.status_code, 201, res.get_json())
        body = res.get_json()
        self.assertEqual(len(body["recommendations"]), 5)
        self.assertIsNotNone(body["trace"])
        imp = body["recommendations"][0]["impression_id"]
        fb = self.client.post("/api/v1/agent/feedback", json={
            "user_id": "participant_001", "impression_id": imp, "event": "liked",
            "survey": {"discovery_value": 5}})
        self.assertEqual(fb.status_code, 201, fb.get_json())
        missing = self.client.post("/api/v1/agent/feedback", json={
            "user_id": "participant_001", "impression_id": "imp_nope", "event": "liked"})
        self.assertEqual(missing.status_code, 404)

    def test_v2_input_validation(self):
        bad = self.client.post("/api/v1/agent/recommend", json={
            "user_id": "participant_001", "message": "x", "exploration_level": 3})
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()

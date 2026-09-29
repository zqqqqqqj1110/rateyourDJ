"""Stage 6: the ReAct conversation layer on top of RecommenderV2."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rateyourdj.agent.llm import LLMError, ScriptedChat

try:
    import numpy  # noqa: F401
    from test_recommender_v2 import make_service
    from rateyourdj.agent.converse import ConversationAgent
except ImportError:  # pragma: no cover
    numpy = None

try:
    import flask  # noqa: F401
    from rateyourdj.web.app import create_app
except ImportError:  # pragma: no cover
    flask = None


def call(name, **args):
    return {"content": "", "tool_calls": [{"id": f"c_{name}", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"content": text, "tool_calls": []}


def observations(llm, index=-1):
    """Tool observations the LLM saw in its ``index``-th call."""
    return [json.loads(m["content"]) for m in llm.calls[index] if m["role"] == "tool"]


@unittest.skipIf(numpy is None, "numpy not installed")
class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name)
        self.svc = make_service(self.tmp)
        self.user_dir = self.tmp / "users" / "participant_001"

    def tearDown(self):
        self.dir.cleanup()

    def agent(self, script):
        llm = ScriptedChat(script)
        return ConversationAgent(self.svc, llm), llm

    def test_recommend_then_explain_without_new_songs(self):
        agent, llm = self.agent([
            call("recommend_songs", summary="要新歌", request="space rock"),
            say("这批偏太空摇滚。"),
            call("get_recent_recommendations", summary="查上一轮", rounds=1),
            say("第二首和你的种子歌很像。"),
        ])
        first = agent.chat("participant_001", "来点太空摇滚", count=4)
        self.assertEqual(first["action"], "recommend")
        self.assertEqual(len(first["recommendations"]), 4)
        self.assertEqual(first["message"], "这批偏太空摇滚。")
        second = agent.chat("participant_001", "为什么推荐第二首？", session_id=first["session_id"])
        self.assertEqual(second["action"], "answer")
        self.assertEqual(second["recommendations"], [])
        self.assertEqual(second["message"], "第二首和你的种子歌很像。")
        obs = observations(llm)[0]
        rank2 = next(s for s in obs["rounds"][-1]["songs"] if s["rank"] == 2)
        self.assertEqual(rank2["title"], first["recommendations"][1]["track"]["title"])
        self.assertEqual(rank2["reason"], first["recommendations"][1]["reason"])
        # the history the LLM saw lists the previous round's titles
        history = "\n".join(m["content"] or "" for m in llm.calls[-1] if m["role"] == "assistant")
        self.assertIn(first["recommendations"][0]["track"]["title"], history)
        # only one round of impressions: answering did not show new songs
        self.assertEqual(len((self.user_dir / "impressions.jsonl").read_text().splitlines()), 4)

    def test_feedback_is_visible_to_the_agent(self):
        agent, llm = self.agent([call("recommend_songs", summary="s", request="space rock"), say("好"),
                                 call("get_recent_recommendations", summary="s"), say("嗯")])
        first = agent.chat("participant_001", "太空摇滚", count=3)
        self.svc.record_feedback("participant_001", first["recommendations"][0]["impression_id"],
                                 event="liked", survey={"heard_before": "no"})
        agent.chat("participant_001", "我喜欢的那首是什么", session_id=first["session_id"])
        song = observations(llm)[0]["rounds"][-1]["songs"][0]
        self.assertEqual(song["user_feedback"]["heard_before"], "no")
        self.assertIn("liked", song["user_feedback"]["events"])

    def test_small_talk_does_not_recommend(self):
        agent, _ = self.agent([say("不客气！")])
        out = agent.chat("participant_001", "谢谢")
        self.assertEqual(out["action"], "answer")
        self.assertFalse((self.user_dir / "impressions.jsonl").exists())

    def test_only_one_recommendation_per_turn(self):
        agent, llm = self.agent([call("recommend_songs", summary="s", request="space rock"),
                                 call("recommend_songs", summary="s", request="britpop"),
                                 say("好了")])
        out = agent.chat("participant_001", "来点歌", count=3)
        self.assertEqual(observations(llm)[-1]["status"], "error")
        self.assertEqual(len(out["recommendations"]), 3)
        self.assertEqual(len((self.user_dir / "impressions.jsonl").read_text().splitlines()), 3)

    def test_explicit_overrides_are_used_and_recorded(self):
        agent, _ = self.agent([call("recommend_songs", summary="s", request="space rock",
                                    exploration_level=0.9, count=2), say("更冷门的来了")])
        out = agent.chat("participant_001", "来两首更冷门的", count=5, exploration_level=0.3)
        self.assertEqual(len(out["recommendations"]), 2)
        run = json.loads((self.user_dir / "runs" / f"{out['run_id']}.json").read_text())
        self.assertEqual(run["request"]["exploration_level"], 0.9)
        self.assertEqual(run["request"]["text"], "space rock")

    def test_bad_tool_arguments_are_reported_not_raised(self):
        agent, llm = self.agent([call("recommend_songs", summary="s", request="x", exploration_level=3),
                                 call("nope", summary="s"), say("抱歉")])
        out = agent.chat("participant_001", "来点歌")
        self.assertEqual(out["action"], "answer")
        self.assertEqual([o["status"] for o in observations(llm)], ["error", "error"])

    def test_lookup_song(self):
        agent, llm = self.agent([call("lookup_song", summary="s", query="Time"), say("找到了")])
        agent.chat("participant_001", "Time 这首歌是什么风格")
        matches = observations(llm)[0]["matches"]
        self.assertEqual(matches[0]["title"], "Time")
        self.assertIn("psychedelic rock", matches[0]["tags"])
        self.assertIn(matches[0]["popularity"], {"热门", "中等热度", "冷门", "热度未知"})

    def test_llm_failure_falls_back_to_recommending(self):
        agent, _ = self.agent([LLMError("down")])
        out = agent.chat("participant_001", "space rock", count=3)
        self.assertEqual(out["action"], "recommend")
        self.assertIn("llm_error", out["conversation"]["fallback_reason"])
        self.assertEqual(len(out["recommendations"]), 3)

    def test_no_llm_more_reuses_previous_request(self):
        agent = ConversationAgent(self.svc, None)
        first = agent.chat("participant_001", "space rock", count=3)
        agent.chat("participant_001", "换一批", session_id=first["session_id"], count=3)
        session = agent.load_session("participant_001", first["session_id"])
        self.assertEqual([t["request"] for t in session["turns"]], ["space rock", "space rock"])

    def test_interleaving_passes_through_blind(self):
        agent, _ = self.agent([call("recommend_songs", summary="s", request="space rock"), say("好")])
        out = agent.chat("participant_001", "太空摇滚", count=4, interleave=["rag-rel-v1", "rag-tailmix-v1"])
        self.assertIsNotNone(out["interleaving"])
        for r in out["recommendations"]:
            self.assertNotIn("strategy_version", r)

    def test_session_is_saved_and_deleted_with_interactions(self):
        agent, _ = self.agent([say("你好")])
        out = agent.chat("participant_001", "你好")
        self.assertTrue((self.user_dir / "sessions" / f"{out['session_id']}.json").is_file())
        with self.assertRaises(ValueError):
            agent.load_session("participant_001", "../context")
        self.svc.delete_user_data("participant_001", "interactions")
        self.assertFalse((self.user_dir / "sessions").exists())


@unittest.skipIf(flask is None or numpy is None, "flask or numpy not installed")
class ConversationWebTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        tmp = Path(self.dir.name)
        svc = make_service(tmp / "v2")
        self.llm = ScriptedChat([call("recommend_songs", summary="s", request="space rock"), say("来了"),
                                 say("不客气")])
        self.app = create_app(profile_dir=tmp / "p", song_dir=tmp / "s", trajectory_dir=tmp / "t",
                              session_dir=tmp / "se", auto_configure_music_provider=False,
                              recommender_v2=svc, conversation_agent=ConversationAgent(svc, self.llm))
        self.client = self.app.test_client()

    def tearDown(self):
        self.dir.cleanup()

    def test_chat_and_restore(self):
        res = self.client.post("/api/v2/chat", json={"user_id": "participant_001", "message": "太空摇滚",
                                                     "count": 3, "exploration_level": 0.5})
        self.assertEqual(res.status_code, 201, res.get_json())
        body = res.get_json()
        self.assertEqual(len(body["recommendations"]), 3)
        self.assertNotIn("steps", body["conversation"])
        res = self.client.post("/api/v2/chat", json={"user_id": "participant_001", "message": "谢谢",
                                                     "session_id": body["session_id"]})
        self.assertEqual(res.get_json()["action"], "answer")
        restored = self.client.get(f"/api/v2/users/participant_001/sessions/{body['session_id']}").get_json()
        self.assertEqual([t["action"] for t in restored["turns"]], ["recommend", "answer"])
        self.assertEqual(len(restored["turns"][0]["recommendations"]), 3)
        self.assertEqual(self.client.get("/api/v2/users/participant_001/sessions/conv_000000000000")
                         .status_code, 404)

    def test_validation(self):
        for bad in ({"user_id": "participant_001"}, {"user_id": "nobody", "message": "x"},
                    {"user_id": "participant_001", "message": "x", "exploration_level": 2},
                    {"user_id": "participant_001", "message": "x", "interleave": ["rag-rel-v1"]}):
            res = self.client.post("/api/v2/chat", json=bad)
            self.assertIn(res.status_code, (400, 404), bad)


if __name__ == "__main__":
    unittest.main()

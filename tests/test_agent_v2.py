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

from rateyourdj.agent.llm import LLMError, ScriptedChat, tool_call_arguments
from rateyourdj.agent.loop import AGENT_STRATEGY, FALLBACK_STRATEGY, repair_hints, run_agent, run_pipeline
from rateyourdj.ranking.validator import validate_selection
from rateyourdj.agent.tools import Toolbox


def call(name, args, cid="c1", as_string=True):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args) if as_string else args}}


def by_artist_count(cs, song_id, picks):
    by_id = {c["song_id"]: c for c in cs["candidates"]}
    artist = by_id[song_id]["artist_credit"].strip().lower()
    return sum(1 for p in picks if p["song_id"] in by_id and by_id[p["song_id"]]["artist_credit"].strip().lower() == artist)


def msg(*calls, content=""):
    return {"content": content, "tool_calls": list(calls)}


@unittest.skipIf(numpy is None, "numpy not installed")
class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.songs, self.context = _catalog()
        enc = HashingEncoder(1024)
        self.tmp = tempfile.TemporaryDirectory()
        index = build_index(self.songs, enc, catalog_version="c1", root=self.tmp.name, log=lambda *a: None)
        self.retriever = Retriever(self.songs, self.context, index=index, encoder=enc, catalog_version="c1")
        self.toolbox = Toolbox({s["song_id"]: s for s in self.songs}, self.context, self.retriever, count=5)
        self.cs = self.retriever.retrieve("space rock", exploration_level=0.5)

    def tearDown(self):
        self.tmp.cleanup()

    def good_picks(self, n=5):
        picks, per_artist, tails = [], {}, 0
        cands = sorted(self.cs["candidates"], key=lambda c: c["bucket"] != "tail")  # tails first
        for c in cands:
            if per_artist.get(c["artist_credit"], 0) >= 2:
                continue
            picks.append({"song_id": c["song_id"], "reason": "证据显示风格接近", "evidence_refs": [0]})
            per_artist[c["artist_credit"]] = per_artist.get(c["artist_credit"], 0) + 1
            if len(picks) == n:
                break
        return picks

    def retrieve_call(self):
        return call("retrieve_candidates", {"summary": "先检索", "query": "space rock",
                                            "exploration_level": 0.5}, "r1")

    def submit_call(self, picks, cid="s1", as_string=True):
        return call("submit_recommendations", {"summary": "提交", "candidate_set_id": self.cs["candidate_set_id"],
                                               "message": "为你挑了这些", "picks": picks}, cid, as_string)

    def test_happy_path_uses_agent_strategy(self):
        llm = ScriptedChat([msg(self.retrieve_call()), msg(self.submit_call(self.good_picks()))])
        out = run_agent(llm, self.toolbox, request_text="space rock", count=5, exploration_level=0.5)
        self.assertEqual(out["strategy_version"], AGENT_STRATEGY)
        self.assertIsNone(out["fallback_reason"])
        self.assertTrue(out["validation"]["ok"])
        self.assertEqual([s["tool"] for s in out["steps"]], ["retrieve_candidates", "submit_recommendations"])
        self.assertEqual(out["steps"][0]["summary"], "先检索")
        # every tool call got a tool response before the next model turn
        second_turn = llm.calls[1]
        self.assertEqual(second_turn[-1]["role"], "tool")
        self.assertEqual(second_turn[-1]["tool_call_id"], "r1")

    def test_dict_arguments_are_accepted(self):
        self.assertEqual(tool_call_arguments(call("x", {"a": 1}, as_string=False)), {"a": 1})
        llm = ScriptedChat([msg(self.retrieve_call()),
                            msg(self.submit_call(self.good_picks(), as_string=False))])
        out = run_agent(llm, self.toolbox, request_text="space rock", count=5, exploration_level=0.5)
        self.assertEqual(out["strategy_version"], AGENT_STRATEGY)

    def test_one_repair_round_then_success(self):
        bad = self.good_picks()[:4]
        llm = ScriptedChat([msg(self.retrieve_call()), msg(self.submit_call(bad)),
                            msg(self.submit_call(self.good_picks(), "s2"))])
        out = run_agent(llm, self.toolbox, request_text="space rock", count=5, exploration_level=0.5)
        self.assertEqual(out["strategy_version"], AGENT_STRATEGY)
        feedback = llm.calls[2][-1]
        self.assertEqual(feedback["role"], "tool")
        self.assertIn("校验", feedback["content"])

    def test_out_of_set_song_falls_back_to_valid_deterministic_selection(self):
        hallucinated = self.good_picks()[:4] + [{"song_id": "s_made_up_song", "reason": "x", "evidence_refs": [0]}]
        # max_repairs=2: the third invalid submission triggers the fallback
        llm = ScriptedChat([msg(self.retrieve_call()), msg(self.submit_call(hallucinated)),
                            msg(self.submit_call(hallucinated, "s2")), msg(self.submit_call(hallucinated, "s3"))])
        out = run_agent(llm, self.toolbox, request_text="space rock", count=5, exploration_level=0.5)
        self.assertEqual(out["strategy_version"], FALLBACK_STRATEGY)
        self.assertIn("invalid selection", out["fallback_reason"])
        self.assertIn("s_made_up_song 不在候选集中", llm.calls[2][-1]["content"])
        self.assertTrue(out["validation"]["ok"])
        ids = {c["song_id"] for c in out["candidate_set"]["candidates"]}
        self.assertTrue(all(p["song_id"] in ids for p in out["picks"]))

    def test_second_repair_round_can_succeed(self):
        bad = self.good_picks()[:4]
        llm = ScriptedChat([msg(self.retrieve_call()), msg(self.submit_call(bad)),
                            msg(self.submit_call(bad, "s2")), msg(self.submit_call(self.good_picks(), "s3"))])
        out = run_agent(llm, self.toolbox, request_text="space rock", count=5, exploration_level=0.5)
        self.assertEqual(out["strategy_version"], AGENT_STRATEGY)
        self.assertIsNone(out["fallback_reason"])
        self.assertEqual(out["repairs"], 2)

    def test_artist_cap_hint_names_songs_and_alternatives(self):
        by_artist = {}
        for c in self.cs["candidates"]:
            by_artist.setdefault(c["artist_credit"], []).append(c)
        artist, songs = max(by_artist.items(), key=lambda kv: len(kv[1]))
        if len(songs) < 3:
            self.skipTest("fixture has no artist with 3 candidates")
        over = [{"song_id": c["song_id"], "reason": "r", "evidence_refs": [0]} for c in songs[:3]]
        picks = (over + [p for p in self.good_picks(8) if p["song_id"] not in {o["song_id"] for o in over}])[:5]
        report = validate_selection(picks, self.cs, count=5, max_per_artist=2)
        cap = [v for v in report["violations"] if v["type"] == "artist_cap"]
        self.assertEqual(len(cap), 1)
        self.assertEqual(cap[0]["remove"], 1)
        hints = repair_hints(report, picks, self.cs, 2)
        text = "\n".join(hints)
        self.assertIn("去掉 1 首", text)
        for o in over:
            self.assertIn(o["song_id"], text)
        chosen = {p["song_id"] for p in picks}
        alt_part = text.split("可选：", 1)[1]
        suggested = [c["song_id"] for c in self.cs["candidates"] if c["song_id"] in alt_part]
        self.assertTrue(suggested)
        self.assertFalse(chosen & set(suggested))
        self.assertTrue(all(by_artist_count(self.cs, sid, picks) < 2 for sid in suggested))

    def test_invalid_candidate_set_hint(self):
        self.assertIn("candidate_set_id", repair_hints({"violations": []}, [], None, 2)[0])

    def test_llm_error_falls_back(self):
        out = run_agent(ScriptedChat([LLMError("timeout")]), self.toolbox, request_text="space rock", count=5)
        self.assertEqual(out["strategy_version"], FALLBACK_STRATEGY)
        self.assertIn("llm_error", out["fallback_reason"])
        self.assertTrue(out["validation"]["ok"])

    def test_no_tool_call_is_nudged_once_then_falls_back(self):
        llm = ScriptedChat([msg(content="我觉得可以推荐 Wonderwall"), msg(content="还是 Wonderwall")])
        out = run_agent(llm, self.toolbox, request_text="space rock", count=5)
        self.assertEqual(out["strategy_version"], FALLBACK_STRATEGY)
        self.assertEqual(out["fallback_reason"], "model stopped without submitting")

    def test_unknown_tool_and_bad_branch_become_error_observations(self):
        llm = ScriptedChat([msg(call("delete_everything", {"summary": "?"}, "u1")),
                            msg(call("retrieve_candidates", {"summary": "x", "query": "q",
                                                             "branch_hint": "radiohead"}, "r0")),
                            msg(self.retrieve_call()), msg(self.submit_call(self.good_picks()))])
        out = run_agent(llm, self.toolbox, request_text="space rock", count=5, exploration_level=0.5)
        self.assertEqual(out["strategy_version"], AGENT_STRATEGY)
        self.assertEqual([s["status"] for s in out["steps"][:2]], ["error", "error"])

    def test_step_budget(self):
        llm = ScriptedChat([msg(self.retrieve_call()) for _ in range(3)])
        out = run_agent(llm, self.toolbox, request_text="space rock", count=5, max_steps=3)
        self.assertEqual(out["strategy_version"], FALLBACK_STRATEGY)
        self.assertIn("step budget", out["fallback_reason"])

    def test_pipeline_modes(self):
        for strategy in ("rag-rel-v1", "rag-tailmix-v1"):
            out = run_pipeline(self.toolbox, request_text="space rock", count=5, strategy=strategy)
            self.assertEqual(out["strategy_version"], strategy)
            self.assertTrue(out["validation"]["ok"], out["validation"]["errors"])

    def test_track_facts_only_for_retrieved_songs(self):
        self.toolbox.execute("retrieve_candidates", {"query": "space rock"})
        sid = next(iter(self.toolbox.candidate_sets.values()))["candidates"][0]["song_id"]
        ok = self.toolbox.execute("get_track_facts", {"song_ids": [sid, "s_not_retrieved"]})
        self.assertEqual(ok["status"], "partial")
        self.assertEqual(len(ok["data"]), 1)

    def test_system_prompt_states_constraints(self):
        llm = ScriptedChat([LLMError("x")])
        run_agent(llm, self.toolbox, request_text="q", count=5, exploration_level=0.5)
        system = llm.calls[0][0]["content"]
        self.assertIn("恰好 5 首", system)
        self.assertIn("至少 2 首", system)  # slot_plan(5, 0.5)["tail"]
        self.assertIn("目标数量是 2–3 首", system)  # tail_target(5, 0.5)

    def test_tail_target_ranges(self):
        from rateyourdj.agent.loop import tail_target
        self.assertEqual(tail_target(10, 0.1), (1, 2))
        self.assertEqual(tail_target(10, 0.5), (3, 5))
        self.assertEqual(tail_target(10, 0.9), (5, 8))


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(numpy is None, "numpy not installed")
class BranchGuardTest(unittest.TestCase):
    def setUp(self):
        self.songs, self.context = _catalog()
        enc = HashingEncoder(1024)
        self.tmp = tempfile.TemporaryDirectory()
        index = build_index(self.songs, enc, catalog_version="c1", root=self.tmp.name, log=lambda *a: None)
        self.retriever = Retriever(self.songs, self.context, index=index, encoder=enc, catalog_version="c1")
        self.tb = Toolbox({s["song_id"]: s for s in self.songs}, self.context, self.retriever, count=5)

    def tearDown(self):
        self.tmp.cleanup()

    def branch_of(self, obs):
        cs = self.tb.candidate_sets[obs["data"]["candidate_set_id"]]
        return cs["query"]["branch_hint"]

    def test_named_seed_overrides_conflicting_branch(self):
        obs = self.tb.execute("retrieve_candidates", {"summary": "s", "query": "像 Live Forever 那样宏大的歌",
                                                      "branch_hint": "pink_floyd"})
        self.assertEqual(self.branch_of(obs), "oasis")
        self.assertTrue(any("已更正" in d for d in obs["diagnostics"]))

    def test_seeds_of_both_branches_clear_the_branch(self):
        obs = self.tb.execute("retrieve_candidates", {"summary": "s", "query": "介于 Time 和 Live Forever 之间",
                                                      "branch_hint": "oasis"})
        self.assertIsNone(self.branch_of(obs))

    def test_consistent_or_unnamed_branch_is_kept(self):
        for query, branch in (("像 Time 那样", "pink_floyd"), ("space rock", "oasis"), ("", "oasis")):
            obs = self.tb.execute("retrieve_candidates", {"summary": "s", "query": query, "branch_hint": branch})
            self.assertEqual(self.branch_of(obs), branch)
            self.assertEqual(obs["diagnostics"], [])


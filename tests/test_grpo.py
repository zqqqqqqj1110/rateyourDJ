import copy
import json
import random
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


def completion(args):
    return "<tool_call>\n" + json.dumps({"name": "submit_recommendations", "arguments": args},
                                        ensure_ascii=False) + "\n</tool_call>"


@unittest.skipIf(numpy is None, "numpy not installed")
class GRPORewardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rateyourdj.agent.factory import vector_similarity
        from rateyourdj.training.grpo_data import convert
        from rateyourdj.training.sft_data import SFTDataGenerator
        cls.songs, cls.context = _catalog()
        enc = HashingEncoder(256)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.index = build_index(cls.songs, enc, catalog_version="c1", root=cls.tmp.name, log=lambda *a: None)
        retriever = Retriever(cls.songs, cls.context, index=cls.index, encoder=enc, catalog_version="c1",
                              config={"artist_cap_total": 3})
        gen = SFTDataGenerator(cls.songs, cls.context, retriever, similarity=vector_similarity(retriever), seed=5,
                               heldout_artist_rate=0.0, log=lambda *a: None)
        res = gen.generate(40)
        cls.sft = [s for rows in res["samples"].values() for s in rows]
        hidden = {"version": "hidden-pos-test", "branches": {
            "pink_floyd": {"song_ids": [s["song_id"] for s in cls.songs[2::10]]},
            "oasis": {"song_ids": [s["song_id"] for s in cls.songs[3::10]]}}}
        cls.grpo = [convert(s, split="train", index=cls.index, seed_artists=gen.seed_artists, hidden=hidden)
                    for s in cls.sft]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def oracle_args(self, i):
        return json.loads(self.sft[i]["messages"][-1]["tool_calls"][0]["function"]["arguments"])

    def score(self, args, i):
        from rateyourdj.training.grpo_reward import score
        return score(completion(args), self.grpo[i])

    # ---------------------------------------------------------------- data
    def test_prompt_ends_at_the_retrieval_and_hides_the_answer(self):
        for g, s in zip(self.grpo, self.sft):
            self.assertEqual(g["prompt"][-1]["role"], "tool")
            self.assertEqual(g["prompt"][-1]["name"], "retrieve_candidates")
            text = json.dumps(g["prompt"], ensure_ascii=False)
            self.assertNotIn("submit_recommendations\", \"arguments", text)
            self.assertFalse(any(m.get("weight") == 0 for m in g["prompt"]))
            self.assertEqual(len(g["sim"]), len(g["candidates"]))

    def test_training_view_drops_eval_only(self):
        from rateyourdj.training.grpo_data import training_view
        for g in self.grpo:
            view = training_view(g)
            self.assertNotIn("eval_only", view)
            self.assertNotIn("hidden_positives", json.dumps(view))
            self.assertIn("eval_only", g)   # the original is untouched

    def test_reward_never_reads_eval_only(self):
        for i in range(len(self.grpo)):
            args = self.oracle_args(i)
            base = self.score(args, i)["reward"]
            poisoned = copy.deepcopy(self.grpo[i])
            poisoned["eval_only"] = {"hidden_positives": [], "oracle_picks": [], "weak_positives": []}
            from rateyourdj.training.grpo_reward import score
            self.assertEqual(score(completion(args), poisoned)["reward"], base)

    # ---------------------------------------------------------------- reward
    def test_oracle_is_valid_and_scores_well(self):
        rewards = [self.score(self.oracle_args(i), i) for i in range(len(self.grpo))]
        self.assertTrue(all(r["valid"] for r in rewards), [r.get("error") for r in rewards])
        self.assertGreater(sum(r["reward"] for r in rewards) / len(rewards), 0.6)
        self.assertTrue(all(r["length_penalty"] == 0 for r in rewards))

    def test_format_and_constraint_failures(self):
        from rateyourdj.training.grpo_reward import score
        g, args = self.grpo[0], self.oracle_args(0)
        self.assertEqual(score("我推荐这些歌", g)["reward"], -1.0)
        self.assertEqual(score(completion({**args, "candidate_set_id": "cs_other"}), g)["reward"], -1.0)
        wrong_tool = "<tool_call>" + json.dumps({"name": "rank_candidates", "arguments": args}) + "</tool_call>"
        self.assertEqual(score(wrong_tool, g)["reward"], -1.0)
        dup = copy.deepcopy(args)
        dup["picks"][-1] = dup["picks"][0]
        self.assertEqual(score(completion(dup), g)["reward"], -0.5)
        short = copy.deepcopy(args)
        short["picks"].pop()
        self.assertEqual(score(completion(short), g)["reward"], -0.5)
        outside = copy.deepcopy(args)
        outside["picks"][0]["song_id"] = "s_not_a_candidate"
        self.assertEqual(score(completion(outside), g)["reward"], -0.5)

    def test_fabricated_numbers_lose_evidence_reward(self):
        for i in range(5):
            args = self.oracle_args(i)
            fake = copy.deepcopy(args)
            for p in fake["picks"]:
                p["reason"] = "全网只有 3 个人听过，相似度 0.99"
            self.assertLess(self.score(fake, i)["reward"], self.score(args, i)["reward"])

    def test_padding_the_output_is_penalised(self):
        args = self.oracle_args(0)
        long = copy.deepcopy(args)
        for p in long["picks"]:
            p["reason"] = p["reason"] + "，" + "这首歌非常好听" * 40
        r = self.score(long, 0)
        self.assertGreater(r["length_penalty"], 0)
        self.assertLess(r["reward"], self.score(args, 0)["reward"])

    def test_stacking_tail_songs_beyond_the_band_does_not_pay(self):
        from rateyourdj.training.grpo_reward import tail_band
        checked = 0
        for i, g in enumerate(self.grpo):
            cons = g["constraints"]
            n = cons["count"]
            lo, hi = tail_band(n, cons["exploration_level"], cons["min_tail"])
            tails = sorted((c for c in g["candidates"] if c["bucket"] == "tail"), key=lambda c: -c["tail_score"])
            per_artist, picks = {}, []
            for c in tails:
                if per_artist.get(c["artist"], 0) < 2:
                    picks.append({"song_id": c["song_id"], "reason": "冷门", "evidence_refs": [0]})
                    per_artist[c["artist"]] = per_artist.get(c["artist"], 0) + 1
                if len(picks) == n:
                    break
            if len(picks) < n or n <= hi:
                continue
            args = {**self.oracle_args(i), "picks": picks}
            r = self.score(args, i)
            self.assertLess(r["components"]["tail_band"], 1.0)
            self.assertLess(r["reward"], self.score(self.oracle_args(i), i)["reward"])
            checked += 1
        self.assertGreater(checked, 0)

    def test_wrong_tail_count_in_message(self):
        args = self.oracle_args(0)
        lying = {**args, "message": "这批里 99 首冷门"}
        self.assertLess(self.score(lying, 0)["components"]["evidence"], self.score(args, 0)["components"]["evidence"])

    def test_random_valid_selections_score_below_the_oracle_on_average(self):
        from rateyourdj.ranking import validate_selection
        from rateyourdj.training.grpo_reward import score
        rng = random.Random(0)
        oracle, rand = [], []
        for i, g in enumerate(self.grpo):
            cons = g["constraints"]
            cs = {"candidates": [{"song_id": c["song_id"], "artist_credit": c["artist"], "bucket": c["bucket"],
                                  "evidence": [{"detail": d} for d in c["evidence"]]} for c in g["candidates"]]}
            for _ in range(30):
                pool = rng.sample(g["candidates"], cons["count"])
                picks = [{"song_id": c["song_id"], "reason": c["evidence"][0], "evidence_refs": [0]} for c in pool]
                if validate_selection(picks, cs, count=cons["count"], max_per_artist=2, min_tail=cons["min_tail"])["ok"]:
                    rand.append(score(completion({**self.oracle_args(i), "picks": picks}), g)["reward"])
                    oracle.append(self.score(self.oracle_args(i), i)["reward"])
                    break
        self.assertGreater(len(rand), 5)
        self.assertGreater(sum(oracle) / len(oracle), sum(rand) / len(rand))

    def test_selection_eval_compares_systems(self):
        from rateyourdj.training.grpo_eval import evaluate
        oracle_text = {g["sample_id"]: completion(self.oracle_args(i)) for i, g in enumerate(self.grpo)}
        res = evaluate(self.grpo, {"fixed_mix": None, "copy": lambda s: oracle_text[s["sample_id"]],
                                   "broken": lambda s: "no"}, log=lambda *a: None)
        rep = res["report"]["systems"]
        self.assertEqual(rep["fixed_mix"]["valid"], 1.0)
        self.assertEqual(rep["copy"]["composite"], rep["fixed_mix"]["composite"])
        self.assertEqual(rep["copy"]["reward"], rep["fixed_mix"]["reward"])
        self.assertEqual(rep["broken"]["valid"], 0.0)
        self.assertLess(rep["broken"]["composite"], rep["fixed_mix"]["composite"])
        self.assertGreater(rep["fixed_mix"]["samples_with_hp"], 0)

    def test_trl_reward_function(self):
        from rateyourdj.training.grpo_reward import reward_fn_factory
        fn = reward_fn_factory({g["sample_id"]: g for g in self.grpo})
        out = fn(prompts=["p", "p"], completions=[completion(self.oracle_args(0)), "garbage"],
                 sample_id=[self.grpo[0]["sample_id"]] * 2)
        self.assertGreater(out[0], 0)
        self.assertEqual(out[1], -1.0)


if __name__ == "__main__":
    unittest.main()

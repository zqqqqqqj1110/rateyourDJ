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

from rateyourdj.agent.loop import build_messages
from rateyourdj.agent.tools import Toolbox
from rateyourdj.ranking import validate_selection


@unittest.skipIf(numpy is None, "numpy not installed")
class SFTDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rateyourdj.agent.factory import vector_similarity
        from rateyourdj.training.sft_data import SFTDataGenerator
        cls.songs, cls.context = _catalog()
        enc = HashingEncoder(256)
        cls.tmp = tempfile.TemporaryDirectory()
        index = build_index(cls.songs, enc, catalog_version="c1", root=cls.tmp.name, log=lambda *a: None)
        cls.retriever = Retriever(cls.songs, cls.context, index=index, encoder=enc, catalog_version="c1")
        cls.fixed = [{"id": "fx", "text": "像 Time 那样迷幻", "branch_hint": None, "exploration": 0.5}]

        def make(seed=7):
            return SFTDataGenerator(cls.songs, cls.context, cls.retriever,
                                    similarity=vector_similarity(cls.retriever), seed=seed,
                                    heldout_artist_rate=0.25, log=lambda *a: None)
        cls.make = staticmethod(make)
        cls.gen = make()
        cls.result = cls.gen.generate(60, fixed_queries=cls.fixed)
        cls.rows = [r for split in ("train", "val", "test") for r in cls.result["samples"].get(split, [])]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def final_submit(self, row):
        call = row["messages"][-1]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "submit_recommendations")
        return json.loads(call["function"]["arguments"])

    def test_generates_all_splits_with_fixed_queries_in_test(self):
        counts = {s: len(v) for s, v in self.result["samples"].items()}
        self.assertGreater(counts.get("train", 0), 30)
        self.assertIn("fx", {r["meta"]["fixed_query_id"] for r in self.result["samples"]["test"]})
        for r in self.rows:
            self.assertEqual(r["schema_version"], "sft-sample/v2")
            self.assertTrue(all("thought" not in m for m in r["messages"]))

    def test_deterministic(self):
        again = self.make().generate(60, fixed_queries=self.fixed)
        ids = lambda res: [r["sample_id"] for s in ("train", "val", "test") for r in res["samples"].get(s, [])]  # noqa: E731
        self.assertEqual(ids(again), ids(self.result))

    def test_prompt_is_identical_to_inference(self):
        for r in self.rows[:10]:
            m = r["meta"]
            ctx = copy.deepcopy(self.context)
            tb = Toolbox({s["song_id"]: s for s in self.songs}, ctx, self.retriever, count=m["count"])
            expected = build_messages(tb, request_text=m["text"], count=m["count"],
                                      exploration_level=m["exploration_level"], branch_hint=m["ui_branch_hint"])
            self.assertEqual(r["messages"][:2], expected)
            self.assertEqual(r["tools"], tb.schemas())

    def test_final_selection_valid_and_reasons_grounded(self):
        from rateyourdj.training.sft_data import reason_numbers_ok
        for r in self.rows:
            obs = next(json.loads(m["content"]) for m in r["messages"] if m.get("name") == "retrieve_candidates")
            args = self.final_submit(r)
            self.assertEqual(args["candidate_set_id"], obs["data"]["candidate_set_id"])
            ids = {c["song_id"] for c in obs["data"]["candidates"]}
            self.assertTrue(all(p["song_id"] in ids for p in args["picks"]))
            self.assertEqual(len(args["picks"]), r["meta"]["count"])
            self.assertGreaterEqual(r["meta"]["tail_picks"], r["meta"]["min_tail"])

    def test_split_isolation(self):
        from rateyourdj.training.sft_scenarios import TEMPLATES
        heldout_templates = {t.id for t in TEMPLATES if t.heldout}
        for r in self.result["samples"]["train"]:
            self.assertNotIn(r["meta"]["template_id"], heldout_templates)
            self.assertEqual(r["meta"]["heldout_artist_picks"], 0)
            obs = next(json.loads(m["content"]) for m in r["messages"] if m.get("name") == "retrieve_candidates")
            artists = {c["artist"].strip().lower() for c in obs["data"]["candidates"]}
            self.assertFalse(artists & self.gen.heldout_artists)   # never even seen in train
        for split in ("val", "test"):
            for r in self.result["samples"].get(split, []):
                self.assertIn(r["meta"]["template_id"], heldout_templates | {"FIXED"})
        train_sets = {r["meta"]["candidate_set_id"] for r in self.result["samples"]["train"]}
        other = {r["meta"]["candidate_set_id"] for s in ("val", "test") for r in self.result["samples"].get(s, [])}
        self.assertFalse(train_sets & other)

    def test_repair_samples_mask_the_bad_turn(self):
        repairs = [r for r in self.rows if r["meta"]["kind"] == "repair"]
        self.assertTrue(repairs)
        for r in repairs:
            zero = [i for i, m in enumerate(r["messages"]) if m.get("weight") == 0]
            self.assertEqual(len(zero), 1)
            obs = json.loads(r["messages"][zero[0] + 1]["content"])
            self.assertEqual(obs["status"], "error")
            self.assertGreater(len(obs["diagnostics"]), 1)
        for r in self.rows:
            if r["meta"]["kind"] != "repair":
                self.assertFalse(any(m.get("weight") == 0 for m in r["messages"]))

    def test_exclusions_go_to_arguments_not_query(self):
        rows = [r for r in self.rows if r["meta"]["exclude_artists"]]
        self.assertTrue(rows)
        for r in rows:
            call = next(c for m in r["messages"] for c in m.get("tool_calls") or []
                        if c["function"]["name"] == "retrieve_candidates")
            args = json.loads(call["function"]["arguments"])
            self.assertEqual(args["exclude_artists"], r["meta"]["exclude_artists"])
            banned = {a.lower() for a in args["exclude_artists"]}
            obs = next(json.loads(m["content"]) for m in r["messages"] if m.get("name") == "retrieve_candidates")
            self.assertFalse({c["artist"].lower() for c in obs["data"]["candidates"]} & banned)

    def test_empty_request_reads_context_first(self):
        for r in self.rows:
            names = [c["function"]["name"] for m in r["messages"] for c in m.get("tool_calls") or []]
            self.assertEqual(names[0] == "get_user_context", r["meta"]["query"] == "")

    def test_inject_error_is_always_detected(self):
        from rateyourdj.training.sft_data import REPAIR_TYPES, inject_error, make_reason
        from rateyourdj.ranking import slot_plan
        ctx = copy.deepcopy(self.context)
        self.retriever.context = ctx
        tb = Toolbox({s["song_id"]: s for s in self.songs}, ctx, self.retriever, count=10)
        cs = self.retriever.retrieve("space rock britpop", exploration_level=0.5)
        min_tail = slot_plan(10, 0.5)["tail"]
        picks = []
        for r in tb.rank(cs, strategy="rag-tailmix-v1", count=10)["ranked"]:
            reason, refs = make_reason(r["candidate"], 0)
            picks.append({"song_id": r["song_id"], "reason": reason, "evidence_refs": refs})
        self.assertTrue(validate_selection(picks, cs, count=10, max_per_artist=2, min_tail=min_tail)["ok"])
        used_kinds = set()
        for kind in REPAIR_TYPES:
            bad, what, used = inject_error(kind, picks, cs, self.songs, random.Random(1), min_tail=min_tail)
            used_kinds.add(used)
            rep = validate_selection(bad, cs, count=10, max_per_artist=2, min_tail=min_tail)
            self.assertFalse(rep["ok"], (kind, used))
        self.assertGreaterEqual(len(used_kinds), 4)

    def test_reason_number_check(self):
        from rateyourdj.training.sft_data import reason_numbers_ok
        cand = {"evidence": [{"detail": "与种子《Time》的向量相似度 0.84"}, {"detail": "ListenBrainz 听众 24"}]}
        self.assertTrue(reason_numbers_ok("听众仅 24，相似度 0.84", cand, [0, 1]))
        self.assertFalse(reason_numbers_ok("听众仅 25", cand, [0, 1]))
        self.assertFalse(reason_numbers_ok("相似度 0.84", cand, [1]))   # number from an uncited item

    def test_toolbox_exclude_artists_and_candidate_set_id_depends_on_context(self):
        ctx = copy.deepcopy(self.context)
        self.retriever.context = ctx
        tb = Toolbox({s["song_id"]: s for s in self.songs}, ctx, self.retriever, count=5)
        obs = tb.execute("retrieve_candidates", {"summary": "s", "query": "space rock",
                                                 "exclude_artists": ["space band 1"]})
        self.assertNotIn("Space Band 1", {c["artist"] for c in obs["data"]["candidates"]})
        plain = self.retriever.retrieve("space rock")
        ctx["recommended_song_ids"] = [plain["candidates"][0]["song_id"]]
        after = self.retriever.retrieve("space rock")
        self.assertNotEqual(plain["candidate_set_id"], after["candidate_set_id"])
        ctx["recommended_song_ids"] = []
        self.assertEqual(self.retriever.retrieve("space rock")["candidate_set_id"], plain["candidate_set_id"])

    def test_paraphrases_keep_placeholders(self):
        from rateyourdj.training.sft_data import paraphrase_templates

        def post(payload):
            text = payload["messages"][0]["content"]
            if "{seed}" in text:
                content = "1. 有《{seed}》那种感觉的歌吗\n2. 类似的歌\n- 来点《{seed}》风格的\n{seed} {artist}"
            else:
                content = "随便说说"
            return {"choices": [{"message": {"content": content}}]}
        out = paraphrase_templates(post, "fake", n=5, log=lambda *a: None)["templates"]
        self.assertEqual(out["S01"], ["有《{seed}》那种感觉的歌吗", "来点《{seed}》风格的"])

    def test_write_dataset(self):
        from rateyourdj.training.sft_data import write_dataset
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_dataset(self.result, tmp, config={"n": 60})
            self.assertEqual(manifest["counts"]["train"], len(self.result["samples"]["train"]))
            lines = (Path(tmp) / "train.jsonl").read_text("utf-8").splitlines()
            self.assertEqual(len(lines), manifest["counts"]["train"])
            self.assertIn("tail_share", manifest["stats"]["train"])


if __name__ == "__main__":
    unittest.main()


class FakeChatTokenizer:
    """Character-level stand-in for a HF tokenizer with a prefix-consistent chat template."""
    pad_token_id = 0

    def apply_chat_template(self, messages, tools=None, tokenize=False, add_generation_prompt=False):
        text = "<tools>" + json.dumps(tools or [], ensure_ascii=False) + "</tools>\n"
        for m in messages:
            body = m.get("content") or ""
            if m.get("tool_calls"):
                body += "".join("<call>" + c["function"]["name"] + c["function"]["arguments"] + "</call>"
                                for c in m["tool_calls"])
            text += f"<|{m['role']}|>\n{body}<|end|>\n"
        if add_generation_prompt:
            text += "<|assistant|>\n"
        return text

    def __call__(self, text, add_special_tokens=False, return_offsets_mapping=False):
        return {"input_ids": [ord(ch) % 5000 + 1 for ch in text],
                "offset_mapping": [(i, i + 1) for i in range(len(text))]}


@unittest.skipIf(numpy is None, "numpy not installed")
class SFTTrainingPrepTest(unittest.TestCase):
    def sample(self):
        tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
        return {"sample_id": "x", "tools": tools, "messages": [
            {"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"},
            {"role": "assistant", "content": "", "weight": 0,
             "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{\"a\": 1}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "OBS"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "2", "type": "function", "function": {"name": "f", "arguments": "{\"a\": 2}"}}]}]}

    def test_only_weighted_assistant_turns_are_supervised(self):
        from rateyourdj.training.sft_lora import assistant_char_spans, tokenize_sample
        tok = FakeChatTokenizer()
        text, spans = assistant_char_spans(tok, self.sample())
        self.assertEqual([w for _, _, w in spans], [0.0, 1.0])
        out = tokenize_sample(tok, self.sample())
        supervised = "".join(text[i] for i, l in enumerate(out["labels"]) if l != -100)
        self.assertEqual(supervised, '<call>f{"a": 2}</call><|end|>\n')   # the weight-0 turn is context only
        self.assertIsNone(tokenize_sample(tok, self.sample(), max_len=10))  # too long -> dropped, not truncated

    def test_token_stats(self):
        from rateyourdj.training.sft_lora import token_stats
        stats = token_stats(FakeChatTokenizer(), [self.sample(), self.sample()])
        self.assertEqual(stats["n"], 2)
        self.assertGreater(stats["supervised_tokens"]["share"], 0)


@unittest.skipIf(numpy is None, "numpy not installed")
class SFTEvalReplayTest(unittest.TestCase):
    def test_replaying_the_oracle_reproduces_the_candidate_set(self):
        from rateyourdj.agent.factory import vector_similarity
        from rateyourdj.agent.llm import ScriptedChat
        from rateyourdj.training.sft_data import SFTDataGenerator
        from rateyourdj.training.sft_eval import aggregate, evaluate_sample
        songs, context = _catalog()
        enc = HashingEncoder(256)
        with tempfile.TemporaryDirectory() as tmp:
            index = build_index(songs, enc, catalog_version="c1", root=tmp, log=lambda *a: None)
            retriever = Retriever(songs, context, index=index, encoder=enc, catalog_version="c1")
            gen = SFTDataGenerator(songs, context, retriever, similarity=vector_similarity(retriever), seed=3,
                                   heldout_artist_rate=0.25, log=lambda *a: None)
            result = gen.generate(30)
            rows = []
            for sample in result["samples"]["train"][:12]:
                script = [{"content": "", "tool_calls": m["tool_calls"]} for m in sample["messages"]
                          if m["role"] == "assistant" and m.get("weight", 1.0) != 0]
                row = evaluate_sample(ScriptedChat(script), sample, songs_by_id={s["song_id"]: s for s in songs},
                                      base_context=context, retriever=retriever,
                                      similarity=vector_similarity(retriever), seed_artists=gen.seed_artists,
                                      heldout_song_ids=gen.heldout_song_ids)
                self.assertTrue(row["completed"], row)
                self.assertEqual(row["pick_overlap"], 1.0)
                self.assertTrue(row["args_branch_ok"] and row["args_exclude_ok"] and row["context_first_ok"])
                self.assertEqual(row["out_of_set"], 0)
                rows.append(row)
            report = aggregate(rows)
            self.assertEqual(report["overall"]["completed"], 1.0)
            self.assertEqual(report["overall"]["evidence_ok"], 1.0)

"""SFT data generation for the rateyourDJ agent (stage 4, ``sft-data-v1``).

Each sample is one complete, *executed* agent trajectory in OpenAI chat/tool-call format:

    system + user            built by agent.loop.build_messages (identical to inference)
    [get_user_context]       when the request has no content
    retrieve_candidates      real Retriever call; the observation is the real tool output
    [rank_candidates]        ~20% of samples look at the deterministic ranking first
    [bad submit -> repair]   ~15%: an injected invalid submission (loss weight 0) and the
                             real validator feedback (agent.loop.repair_observation)
    submit_recommendations   the oracle answer (rag-tailmix-v1) with evidence-only reasons

The correct songs come only from the deterministic oracle; an LLM may paraphrase request
templates (``paraphrase`` command) but never chooses songs. Every sample is re-validated
(check_sample) and dropped on any problem.

Split (split-v1): train / val / test isolated by
  * phrasing   held-out templates (and their paraphrases) only in val/test
  * artists    ~15% of non-seed artists are held out: removed from every *train*
               retrieval, so the model never selects them in training
  * queries    the 13 fixed stage-3 queries are test-only
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from rateyourdj.agent.loop import build_messages, repair_alternatives, repair_observation
from rateyourdj.agent.tools import Toolbox
from rateyourdj.contracts import validate_record
from rateyourdj.contracts.v2 import SCHEMA_VERSIONS
from rateyourdj.data_pipeline.sources import split_title
from rateyourdj.ranking import slot_plan, validate_selection

from .sft_scenarios import (EXCLUDE_CLAUSE, GENERIC_TAGS, HIGH_EXPLORE, LOW_EXPLORE, MORE_PREFIX, TEMPLATES, BranchInfo,
                            Scenario, Template, draw_values, fill, style_words)

DATA_VERSION = "sft-data-v1"
SPLIT_VERSION = "split-v1"
ORACLE_VERSION = "oracle-v1"          # rag-tailmix-v1 picks + evidence-template reasons
ORACLE_STRATEGY = "rag-tailmix-v1"
MAX_PER_ARTIST = 2
EXPLORATION_LEVELS = (0.1, 0.3, 0.5, 0.7, 0.9)
COUNTS = ((5, 0.20), (10, 0.55), (15, 0.25))
KINDS = (("direct", 0.60), ("ranked", 0.20), ("repair", 0.20))
SPLIT_WEIGHTS = (("train", 0.8), ("val", 0.1), ("test", 0.1))
HISTORY_SIZES = ((0, 0.35), (10, 0.2), (20, 0.2), (40, 0.15), (60, 0.1))
REPAIR_TYPES = ("artist_cap", "count", "out_of_set", "min_tail", "bad_evidence_refs", "duplicate")
# smoke eval (sft-v1-smoke): the fine-tuned model failed on counts and tail minimums; artist-cap
# violations are no longer possible (candidate sets hold at most 2 songs per artist) but stay as a fallback
REPAIR_WEIGHTS = (("count", 0.35), ("min_tail", 0.30), ("duplicate", 0.15), ("out_of_set", 0.10),
                  ("bad_evidence_refs", 0.10))
FIXED_TEMPLATE = Template("FIXED", "fixed", "zh", "", "")


class Reject(Exception):
    pass


def _h(*parts: Any) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _weighted(rng: random.Random, pairs) -> Any:
    values, weights = zip(*pairs)
    return rng.choices(values, weights=weights, k=1)[0]


def _lang(text: str) -> str:
    has_zh = bool(re.search(r"[一-鿿]", text))
    has_en = bool(re.search(r"[A-Za-z]{3,}", text))
    return "mix" if has_zh and has_en else ("zh" if has_zh or not has_en else "en")


# ---------------------------------------------------------------- evidence -> reasons
def parse_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    """Pull the literal values out of the evidence strings (numbers kept as printed)."""
    out: dict[str, Any] = {}
    for i, ev in enumerate(candidate["evidence"]):
        d = ev["detail"]
        if ev["type"] == "seed_similarity":
            m = re.search(r"《(.+)》的向量相似度 ([0-9.]+)", d)
            if m:
                out["seed"] = (i, m.group(1), m.group(2))
        elif ev["type"] == "shared_tag":
            out["tags"] = (i, d.split("：", 1)[1].split("、"))
        elif ev["type"] == "request_match":
            out["request"] = (i, d.split("：", 1)[1].split("、"))
        elif ev["type"] == "popularity":
            m = re.search(r"听众 (\d+)", d)
            if m:
                out["pop"] = (i, m.group(1))
    return out


def make_reason(candidate: dict[str, Any], variant: int) -> tuple[str, list[int]]:
    ev = parse_evidence(candidate)
    seed, tags, req, pop = ev.get("seed"), ev.get("tags"), ev.get("request"), ev.get("pop")
    if tags:   # prefer specific styles over generic ones ("rock") when the evidence has both
        specific = [t for t in tags[1] if t not in GENERIC_TAGS]
        tags = (tags[0], specific or tags[1])
    if req:
        specific = [t for t in req[1] if t not in GENERIC_TAGS]
        req = (req[0], specific) if specific else None
    tag_text = "、".join(tags[1][:2]) if tags else None
    tail = candidate["bucket"] == "tail"
    options: list[tuple[str, list[int]]] = []
    if req and seed:
        options.append((f"符合你要的 {'、'.join(req[1][:2])}，与《{seed[1]}》的相似度 {seed[2]}", [req[0], seed[0]]))
    if tail and pop and tag_text:
        options += [(f"冷门曲目（ListenBrainz 听众 {pop[1]}），风格是 {tag_text}", [pop[0], tags[0]]),
                    (f"{tag_text} 风格的冷门歌，听众只有 {pop[1]}", [tags[0], pop[0]])]
    if tail and pop and seed:
        options.append((f"听众仅 {pop[1]} 的冷门歌，与《{seed[1]}》相似度 {seed[2]}", [pop[0], seed[0]]))
    if seed and tag_text:
        options += [(f"与《{seed[1]}》的相似度 {seed[2]}，同属 {tag_text}", [seed[0], tags[0]]),
                    (f"{tag_text} 风格，和《{seed[1]}》的相似度 {seed[2]}", [tags[0], seed[0]])]
    if not options and seed:
        options.append((f"与种子《{seed[1]}》的相似度 {seed[2]}", [seed[0]]))
    if not options and tag_text:
        options.append((f"风格标签：{tag_text}", [tags[0]]))
    if not options and pop:
        options.append((f"ListenBrainz 听众 {pop[1]}", [pop[0]]))
    if not options:
        raise Reject("candidate without usable evidence")
    return options[variant % len(options)]


NUM = re.compile(r"\d+(?:\.\d+)?")


def reason_numbers_ok(reason: str, candidate: dict[str, Any], refs: list[int]) -> bool:
    cited = " ".join(candidate["evidence"][i]["detail"] for i in refs if 0 <= i < len(candidate["evidence"]))
    return set(NUM.findall(reason)) <= set(NUM.findall(cited))


# ---------------------------------------------------------------- generator
class SFTDataGenerator:
    def __init__(self, songs: list[dict[str, Any]], context: dict[str, Any], retriever: Any, *,
                 similarity: Callable[[str, str], float] | None = None, seed: int = 20260927,
                 heldout_artist_rate: float = 0.15, paraphrases: dict[str, list[str]] | None = None,
                 encoder_name: str = "unknown", log: Callable[..., None] = print) -> None:
        self.songs = songs
        self.by_id = {s["song_id"]: s for s in songs}
        self.base_context = copy.deepcopy(context)
        for k in ("recommended_song_ids", "heard_song_ids", "recent_feedback_ids"):
            self.base_context[k] = []
        self.retriever = retriever
        self.similarity = similarity
        self.seed = seed
        by_template = {t.id: t.text for t in TEMPLATES}
        self.paraphrases = {tid: [v for v in variants if tid in by_template and usable_paraphrase(v, by_template[tid])]
                            for tid, variants in (paraphrases or {}).items()}
        self.encoder_name = encoder_name
        self.log = log
        self.index_version = retriever.index.version if getattr(retriever, "index", None) else None
        self.seed_artists = {self.by_id[s].get("artist_credit") or "" for b in context["seed_branches"]
                             for s in b["seed_song_ids"] if s in self.by_id}
        styles = style_words(retriever.branch_tags, retriever.vocab)
        self.branches: dict[str, BranchInfo] = {}
        for b in context["seed_branches"]:
            seeds = [self.by_id[s] for s in b["seed_song_ids"] if s in self.by_id]
            self.branches[b["branch_id"]] = BranchInfo(
                b["branch_id"], [split_title(s.get("title") or "")[0].strip() for s in seeds],
                sorted({s.get("artist_credit") for s in seeds if s.get("artist_credit")}),
                styles[b["branch_id"]]["en"], styles[b["branch_id"]]["zh"])
        seed_keys = {a.strip().lower() for a in self.seed_artists}
        artists = sorted({(s.get("artist_credit") or "").strip().lower() for s in songs} - seed_keys - {""})
        self.heldout_artists = {a for a in artists if _h("artist", seed, a) % 10000 < heldout_artist_rate * 10000}
        self.heldout_song_ids = sorted(s["song_id"] for s in songs
                                       if (s.get("artist_credit") or "").strip().lower() in self.heldout_artists)

    # ------------------------------------------------------------ scenarios
    def branch_of_text(self, text: str) -> list[str]:
        """Branches a free-text request is about: named seeds/artists first, then style words."""
        _, _, refs = self.retriever._resolve_references(text)
        if refs:
            return sorted(refs)
        tags = set(self.retriever._request_tags(text))
        return sorted(b for b, info in self.branches.items() if tags & set(info.style_en))

    def scenario(self, idx: int, split: str, rng: random.Random) -> Scenario:
        heldout = split in ("val", "test")
        pool = [t for t in TEMPLATES if t.heldout == heldout]
        t = rng.choice(pool)
        values, branches = draw_values(t, rng, self.branches)
        variants = self.paraphrases.get(t.id) or []
        paraphrased = bool(variants) and rng.random() < 0.6
        base = rng.choice(variants) if paraphrased else t.text
        text = fill(base, values)
        query = fill(t.query, values)
        e = rng.choice(EXPLORATION_LEVELS)
        count = _weighted(rng, COUNTS)
        lang = t.lang if t.lang != "mix" else "zh"
        ui_hint = None
        if t.mode == "none" and rng.random() < 0.3:
            ui_hint = rng.choice(sorted(self.branches))
            branches = [ui_hint]
        if text:
            if e >= 0.7 and rng.random() < 0.5:
                text += rng.choice(HIGH_EXPLORE[lang])
            elif e <= 0.3 and rng.random() < 0.5:
                text += rng.choice(LOW_EXPLORE[lang])
        sc = Scenario(scenario_id=f"sc_{self.seed}_{idx:06d}", template=t, split=split, text=text, query=query,
                      branch_hint=(branches[0] if t.mode == "single" else ui_hint), branches=branches,
                      exploration=e, count=count, lang=t.lang,
                      kind=_weighted(rng, KINDS), paraphrased=paraphrased)
        sc.ui_hint = ui_hint
        if t.excludes_artist:
            sc.exclude_artists, sc.exclude_from = [values["artist"]], "template"
        elif t.mode != "none" and rng.random() < 0.15:
            sc.exclude_from = "clause"      # artist chosen after a probe retrieval
        # Session history: in real use every recommended song is excluded afterwards, so the same
        # request yields different candidates over time. Simulating that (0-60 earlier songs) keeps
        # the data from repeating the same "hub" songs in most samples.
        sc.history = _weighted(rng, HISTORY_SIZES)
        if sc.history and rng.random() < 0.35:
            sc.asked_more = True
            text_prefix = rng.choice(MORE_PREFIX[lang])
            sc.text = text_prefix + (sc.text or ("按我的口味" if lang != "en" else "based on my taste"))
        if sc.kind == "repair":
            sc.repair_type = _weighted(rng, REPAIR_WEIGHTS)
        return sc

    def fixed_scenarios(self, queries: list[dict[str, Any]], rng: random.Random) -> list[Scenario]:
        out = []
        for q in queries:
            branches = [q["branch_hint"]] if q.get("branch_hint") else self.branch_of_text(q["text"])
            out.append(Scenario(
                scenario_id=f"fixed_{q['id']}", template=FIXED_TEMPLATE, split="test", text=q["text"],
                query=q["text"], branch_hint=branches[0] if len(branches) == 1 else None, branches=branches,
                exploration=float(q["exploration"]), count=10, lang=_lang(q["text"]) if q["text"] else "zh",
                kind="direct", fixed_query_id=q["id"]))
            out[-1].ui_hint = q.get("branch_hint")
        return out

    # ------------------------------------------------------------ building
    def context_for(self, sc: Scenario) -> dict[str, Any]:
        ctx = copy.deepcopy(self.base_context)
        ctx["exploration_level"] = sc.exploration
        if sc.split == "train":
            ctx.setdefault("exclusions", {}).setdefault("song_ids", [])
            ctx["exclusions"]["song_ids"] = sorted(set(ctx["exclusions"]["song_ids"]) | set(self.heldout_song_ids))
        return ctx

    def toolbox(self, ctx: dict[str, Any], count: int) -> Toolbox:
        self.retriever.context = ctx
        return Toolbox(self.by_id, ctx, self.retriever, similarity=self.similarity,
                       seed_artists=self.seed_artists, count=count, max_per_artist=MAX_PER_ARTIST)

    def build(self, sc: Scenario, rng: random.Random) -> dict[str, Any]:
        ctx = self.context_for(sc)
        tb = self.toolbox(ctx, sc.count)
        limit = 40 if sc.count > 10 else 30
        if sc.history:
            # what earlier turns of the session would have shown: the most relevant candidates, in rounds
            for _ in range(0, sc.history, 20):
                shown = self.retriever.retrieve(sc.query, branch_hint=sc.branch_hint,
                                                exploration_level=sc.exploration, limit=20)
                ctx["recommended_song_ids"] += [c["song_id"] for c in shown["candidates"]]
            ctx["recommended_song_ids"] = ctx["recommended_song_ids"][: sc.history]
        if sc.exclude_from == "clause":
            probe = self.retriever.retrieve(sc.query, branch_hint=sc.branch_hint, exploration_level=sc.exploration,
                                            limit=limit)
            if sc.exclude_from == "clause":
                counts = Counter(c["artist_credit"] for c in probe["candidates"]
                                 if c.get("artist_credit") and c["artist_credit"] not in self.seed_artists)
                options = sorted(a for a, n in counts.items() if n >= 2) or sorted(counts)
                if not options:
                    raise Reject("no artist to exclude")
                sc.exclude_artists = [rng.choice(options)]
                clause = rng.choice(EXCLUDE_CLAUSE["en" if sc.lang == "en" else "zh"])  # mix -> zh clause
                sc.text += clause.format(x=sc.exclude_artists[0])
        ui_hint = sc.ui_hint
        messages = build_messages(tb, request_text=sc.text, count=sc.count, exploration_level=sc.exploration,
                                  branch_hint=ui_hint)
        min_tail = slot_plan(sc.count, sc.exploration)["tail"]
        calls: list[tuple[str, dict[str, Any], float]] = []   # (tool, args, weight)
        k = 0

        def emit(name: str, args: dict[str, Any], *, weight: float = 1.0, observe: bool = True,
                 observation: dict[str, Any] | None = None) -> dict[str, Any] | None:
            nonlocal k
            k += 1
            cid = f"call_{k}"
            msg: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": [{
                "id": cid, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
            if weight != 1.0:
                msg["weight"] = weight
            messages.append(msg)
            calls.append((name, args, weight))
            if not observe:
                return None
            obs = observation if observation is not None else tb.execute(name, args)
            messages.append({"role": "tool", "tool_call_id": cid, "name": name,
                             "content": json.dumps(obs, ensure_ascii=False)})
            return obs

        variant = _h(sc.scenario_id)
        if not sc.query:
            emit("get_user_context", {"summary": SUMMARY_CONTEXT[variant % len(SUMMARY_CONTEXT)]})
        r_args: dict[str, Any] = {"summary": self.retrieve_summary(sc, variant), "query": sc.query,
                                  "branch_hint": sc.branch_hint, "exploration_level": sc.exploration}
        if limit != 30:
            r_args["limit"] = limit
        if sc.exclude_artists:
            r_args["exclude_artists"] = list(sc.exclude_artists)
        obs = emit("retrieve_candidates", r_args)
        if obs is None or obs["status"] == "error":
            raise Reject(f"retrieve failed: {obs and obs.get('diagnostics')}")
        cs = tb.candidate_sets[obs["data"]["candidate_set_id"]]
        if sc.kind == "ranked":
            emit("rank_candidates", {"summary": SUMMARY_RANK[variant % len(SUMMARY_RANK)],
                                     "candidate_set_id": cs["candidate_set_id"], "strategy": ORACLE_STRATEGY,
                                     "count": sc.count})
        ranked = tb.rank(cs, strategy=ORACLE_STRATEGY, count=sc.count)["ranked"]
        by_cid = {c["song_id"]: c for c in cs["candidates"]}
        picks = []
        for i, r in enumerate(ranked):
            reason, refs = make_reason(by_cid[r["song_id"]], variant + i)
            picks.append({"song_id": r["song_id"], "reason": reason, "evidence_refs": refs})
        report = validate_selection(picks, cs, count=sc.count, max_per_artist=MAX_PER_ARTIST, min_tail=min_tail)
        if not report["ok"]:
            raise Reject("oracle invalid: " + "; ".join(report["errors"][:2]))
        tail = sum(by_cid[p["song_id"]]["bucket"] == "tail" for p in picks)
        message = self.user_message(sc, len(picks), tail, variant)
        repair_followed = None
        if sc.kind == "repair":
            repair_followed = False
            bad, what, sc.repair_type = inject_error(sc.repair_type or "count", picks, cs, self.songs, rng,
                                                         min_tail=min_tail)
            bad_report = validate_selection(bad, cs, count=sc.count, max_per_artist=MAX_PER_ARTIST, min_tail=min_tail)
            if bad_report["ok"]:
                raise Reject("injected error not detected")
            emit("submit_recommendations", {"summary": self.submit_summary(sc, len(bad), None, variant),
                                            "candidate_set_id": cs["candidate_set_id"], "message": message,
                                            "picks": bad}, weight=0.0,
                 observation=repair_observation(bad_report, bad, cs, MAX_PER_ARTIST))
            submit_summary = REPAIR_SUMMARY[sc.repair_type or "count"].format(**what)
            fixed = follow_hints(bad, bad_report, cs, variant)
            if fixed is not None and validate_selection(fixed, cs, count=sc.count, max_per_artist=MAX_PER_ARTIST,
                                                        min_tail=min_tail)["ok"]:
                picks = fixed          # the repair applies exactly what the validator suggested
                repair_followed = True
                tail = sum(by_cid[p["song_id"]]["bucket"] == "tail" for p in picks)
                message = self.user_message(sc, len(picks), tail, variant)
        else:
            submit_summary = self.submit_summary(sc, len(picks), tail, variant)
        emit("submit_recommendations", {"summary": submit_summary, "candidate_set_id": cs["candidate_set_id"],
                                        "message": message, "picks": picks}, observe=False)
        sample = {
            "schema_version": SCHEMA_VERSIONS["sft_sample"],
            "sample_id": "sft_" + hashlib.sha1(f"{sc.scenario_id}|{cs['candidate_set_id']}|{sc.text}".encode()
                                               ).hexdigest()[:12],
            "tools": tb.schemas(),
            "messages": messages,
            "meta": {**sc.meta(), "text": sc.text, "query": sc.query, "ui_branch_hint": ui_hint,
                     "candidate_set_id": cs["candidate_set_id"], "min_tail": min_tail, "tail_picks": tail,
                     "heldout_artist_picks": sum((by_cid[p["song_id"]].get("artist_credit") or "").strip().lower()
                                                 in self.heldout_artists for p in picks),
                     "repair_follows_hint": repair_followed,
                     # enough to rebuild the exact tool environment when evaluating a model on this sample
                     "context": {"recommended_song_ids": list(ctx.get("recommended_song_ids", [])),
                                 "heldout_artists_excluded": sc.split == "train"},
                     "oracle_version": ORACLE_VERSION, "oracle_strategy": ORACLE_STRATEGY,
                     "index_version": self.index_version, "encoder": self.encoder_name,
                     "data_version": DATA_VERSION, "split_version": SPLIT_VERSION},
        }
        validate_record("sft_sample", sample)
        problems = check_sample(sample, cs, exclude_artists=sc.exclude_artists,
                                forbidden_artists=self.heldout_artists if sc.split == "train" else set())
        if problems:
            raise Reject("check failed: " + "; ".join(problems[:3]))
        sample["meta"]["n_chars"] = sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or []))
                                        for m in messages) + len(json.dumps(sample["tools"], ensure_ascii=False))
        return sample

    # ------------------------------------------------------------ texts
    def retrieve_summary(self, sc: Scenario, variant: int) -> str:
        _, ref_seeds, _ = self.retriever._resolve_references(sc.query)
        parts = []
        if ref_seeds:
            titles = [split_title(self.by_id[s].get("title") or "")[0].strip() for s in ref_seeds]
            named = [t for t in titles if re.search(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])",
                                                    sc.query, re.I)]
            parts.append("请求点名了" + "、".join(f"《{t}》" for t in named) if named
                         else "请求提到了种子艺人")
        ui = sc.ui_hint
        if ui:
            parts.append(f"用户在界面上选了 {ui} 分支")
        elif sc.branch_hint:
            parts.append(f"属于 {sc.branch_hint} 分支")
        elif len(sc.branches) > 1:
            parts.append("同时涉及两个分支，不指定分支")
        else:
            parts.append("没有指明方向，不指定分支")
        parts.append(f"探索强度 {sc.exploration}")
        if sc.exclude_artists:
            parts.append("排除 " + "、".join(sc.exclude_artists))
        if sc.asked_more:
            parts.append("之前推荐过的歌会自动避开")
        return "，".join(parts) + ("，召回候选。" if variant % 2 else "，先检索候选。")

    def submit_summary(self, sc: Scenario, n: int, tail: int | None, variant: int) -> str:
        if tail is None:
            return f"从候选中选出 {n} 首提交。"
        forms = [f"选出 {n} 首：{tail} 首 tail、{n - tail} 首 head/mid，每位艺人不超过 {MAX_PER_ARTIST} 首。",
                 f"按探索强度 {sc.exploration} 选 {n} 首，其中 {tail} 首冷门，艺人不重复超过 {MAX_PER_ARTIST} 首。"]
        return forms[variant % len(forms)]

    def user_message(self, sc: Scenario, n: int, tail: int, variant: int) -> str:
        if sc.template.mode == "both" or len(sc.branches) > 1:
            focus = "在两种口味之间取了平衡"
        elif sc.branches:
            info = self.branches[sc.branches[0]]
            focus = f"沿着 {info.artists[0]} 的方向"
        else:
            focus = "按你的整体口味"
        excl = f"，已避开 {'、'.join(sc.exclude_artists)}" if sc.exclude_artists else ""
        forms = [f"这 {n} 首{focus}挑选，其中 {tail} 首是听众较少的冷门歌{excl}。",
                 f"{focus}为你选了 {n} 首：{tail} 首冷门、{n - tail} 首相对熟悉{excl}。"]
        return forms[variant % len(forms)]

    # ------------------------------------------------------------ dataset
    def generate(self, n: int, *, fixed_queries: list[dict[str, Any]] | None = None,
                 max_attempts_factor: int = 3) -> dict[str, Any]:
        rng = random.Random(self.seed)
        samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        rejects: Counter = Counter()
        seen: set[tuple[str, str]] = set()
        cs_split: dict[str, str] = {}
        todo = [_weighted(rng, SPLIT_WEIGHTS) for _ in range(n)]
        # fixed queries first, so a generated val/test sample can never claim their candidate set
        scenarios: list[tuple[str, Scenario | None]] = [("test", sc) for sc in
                                                        self.fixed_scenarios(fixed_queries or [], rng)]
        scenarios += [(split, None) for split in todo]
        idx = attempts = 0
        target = Counter(todo)
        queue = list(scenarios)
        while queue and attempts < n * max_attempts_factor + len(fixed_queries or []):
            split, sc = queue.pop(0)
            attempts += 1
            idx += 1
            sc = sc or self.scenario(idx, split, rng)
            try:
                sample = self.build(sc, rng)
            except Reject as error:
                rejects[str(error).split(":")[0]] += 1
                if sc.fixed_query_id is None:
                    queue.append((split, None))
                continue
            key = (sample["meta"]["candidate_set_id"], sc.text)
            owner = cs_split.get(key[0])
            if key in seen or (owner is not None and owner != split):
                rejects["duplicate"] += 1
                if sc.fixed_query_id is None:
                    queue.append((split, None))
                continue
            seen.add(key)
            cs_split[key[0]] = split
            samples[split].append(sample)
            if attempts % 100 == 0:
                self.log(f"  {attempts} attempts, kept {sum(map(len, samples.values()))}")
        return {"samples": dict(samples), "rejects": dict(rejects), "target": dict(target),
                "attempts": attempts}


SUMMARY_CONTEXT = ["请求没有具体内容，先读取用户的兴趣分支和种子歌。", "用户没给具体方向，先看看用户上下文。"]
SUMMARY_RANK = ["先用 rag-tailmix-v1 排一遍作参考，按探索强度分配长尾名额。", "参考确定性混排结果再决定。"]
REPAIR_SUMMARY = {
    "artist_cap": "按提示把 {artist} 减到 2 首，换成其他艺人的歌后重新提交。",
    "count": "按提示把数量调整为 {expected} 首后重新提交。",
    "out_of_set": "删掉不在候选集里的 {song_id}，换成候选集内的歌后重新提交。",
    "min_tail": "按提示补足 tail 歌曲（至少 {need} 首）后重新提交。",
    "bad_evidence_refs": "修正 {song_id} 的证据下标，只引用存在的证据后重新提交。",
    "duplicate": "去掉重复的 {song_id}，换一首候选后重新提交。",
}


def inject_error(kind: str, picks: list[dict[str, Any]], cs: dict[str, Any], songs: list[dict[str, Any]],
                 rng: random.Random, min_tail: int = 0) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Return an invalid variant of a valid selection, the values the repair summary needs, and the
    error kind actually injected (falls back to ``count`` when the requested kind is impossible)."""
    by_id = {c["song_id"]: c for c in cs["candidates"]}
    bad = copy.deepcopy(picks)
    chosen = {p["song_id"] for p in picks}
    artist = lambda sid: (by_id[sid].get("artist_credit") or "").strip()  # noqa: E731
    tails = [i for i, p in enumerate(bad) if by_id[p["song_id"]]["bucket"] == "tail"]
    non_tail_idx = [i for i, p in enumerate(bad) if by_id[p["song_id"]]["bucket"] != "tail"]
    if kind == "artist_cap":
        counts = Counter(artist(p["song_id"]) for p in picks)
        for a in sorted(counts, key=lambda a: (-counts[a], a)):
            extra = [c for c in cs["candidates"] if artist(c["song_id"]) == a and c["song_id"] not in chosen]
            need = MAX_PER_ARTIST + 1 - counts[a]
            victims = [i for i in (non_tail_idx or range(len(bad))) if artist(bad[i]["song_id"]) != a]
            if len(extra) >= need and len(victims) >= need:
                for i, c in zip(victims[-need:], extra[:need]):
                    reason, refs = make_reason(c, 0)
                    bad[i] = {"song_id": c["song_id"], "reason": reason, "evidence_refs": refs}
                return bad, {"artist": a}, "artist_cap"
        kind = "count"
    if kind == "min_tail" and min_tail > 0:
        tail_now = len(tails)
        drop = tail_now - min_tail + 1
        per = Counter(artist(p["song_id"]) for p in picks)
        others = []
        for c in cs["candidates"]:
            if c["bucket"] != "tail" and c["song_id"] not in chosen and per[artist(c["song_id"])] < MAX_PER_ARTIST:
                others.append(c)
                per[artist(c["song_id"])] += 1
        if 0 < drop <= min(len(tails), len(others)):
            for i, c in zip(tails[:drop], others[:drop]):
                reason, refs = make_reason(c, 0)
                bad[i] = {"song_id": c["song_id"], "reason": reason, "evidence_refs": refs}
            return bad, {"need": min_tail}, "min_tail"
        kind = "count"
    elif kind == "min_tail":
        kind = "count"
    if kind == "out_of_set":
        pool = [s["song_id"] for s in songs if s["song_id"] not in by_id]
        sid = rng.choice(pool)
        i = rng.randrange(len(bad))
        bad[i] = {**bad[i], "song_id": sid}
        return bad, {"song_id": sid}, "out_of_set"
    if kind == "bad_evidence_refs":
        i = rng.randrange(len(bad))
        bad[i]["evidence_refs"] = [len(by_id[bad[i]["song_id"]]["evidence"]) + 1]
        return bad, {"song_id": bad[i]["song_id"]}, "bad_evidence_refs"
    if kind == "duplicate":
        i, j = rng.sample(range(len(bad)), 2)
        bad[j] = copy.deepcopy(bad[i])
        return bad, {"song_id": bad[i]["song_id"]}, "duplicate"
    # count: one too few or one too many
    if rng.random() < 0.5 or len(bad) < 2:
        extra = [c for c in cs["candidates"] if c["song_id"] not in chosen]
        reason, refs = make_reason(extra[0], 0)
        bad.append({"song_id": extra[0]["song_id"], "reason": reason, "evidence_refs": refs})
    else:
        bad.pop()
    return bad, {"expected": len(picks)}, "count"


def follow_hints(bad: list[dict[str, Any]], report: dict[str, Any], cs: dict[str, Any],
                 variant: int) -> list[dict[str, Any]] | None:
    """Minimal edit of an invalid selection that takes the first replacement the repair hint offered
    (same helper as agent.loop.repair_hints), so the demonstrated repair matches the feedback text.
    Returns None when a violation type cannot be fixed this way (caller falls back to the oracle)."""
    by_id = {c["song_id"]: c for c in cs["candidates"]}
    picks = copy.deepcopy(bad)

    def state():
        chosen = {p["song_id"] for p in picks}
        counts: Counter = Counter()
        for sid in chosen:
            if sid in by_id:
                counts[(by_id[sid].get("artist_credit") or "").strip().lower()] += 1
        return chosen, counts

    def fresh(c: dict[str, Any]) -> dict[str, Any]:
        reason, refs = make_reason(c, variant)
        return {"song_id": c["song_id"], "reason": reason, "evidence_refs": refs}

    def first_alt(n: int = 1, tail_only: bool = False) -> dict[str, Any] | None:
        chosen, counts = state()
        options = repair_alternatives(cs, chosen, dict(counts), MAX_PER_ARTIST, n, tail_only)
        return options[0] if options else None

    for v in report.get("violations", []):
        kind = v.get("type")
        if kind == "artist_cap":
            _, counts = state()
            key = v["artist"]
            for sid in reversed(v["song_ids"]):
                if counts[key] <= MAX_PER_ARTIST:
                    break
                idx = [i for i, p in enumerate(picks) if p["song_id"] == sid]
                if not idx:
                    continue
                i = idx[-1]
                picks.pop(i)
                counts[key] -= 1
                alt = first_alt()
                if alt is None:
                    return None
                picks.insert(i, fresh(alt))
        elif kind in ("out_of_set", "duplicate"):
            idx = [i for i, p in enumerate(picks) if p["song_id"] == v["song_id"]]
            if len(idx) < (2 if kind == "duplicate" else 1):
                continue       # already fixed by an earlier edit
            i = idx[-1] if kind == "duplicate" else idx[0]
            picks.pop(i)
            alt = first_alt()
            if alt is None:
                return None
            picks.insert(i, fresh(alt))
        elif kind == "bad_evidence_refs":
            for i, p in enumerate(picks):
                if p["song_id"] == v["song_id"]:
                    picks[i] = fresh(by_id[v["song_id"]])
        elif kind == "count":
            while len(picks) > v["expected"]:
                picks.pop()
            while len(picks) < v["expected"]:
                alt = first_alt(v["expected"] - len(picks))
                if alt is None:
                    return None
                picks.append(fresh(alt))
        elif kind == "min_tail":
            for _ in range(v["need"] - v["have"]):
                non_tail = [i for i, p in enumerate(picks) if by_id[p["song_id"]]["bucket"] != "tail"]
                alt = first_alt(1, tail_only=True)
                if alt is None or not non_tail:
                    return None
                picks[non_tail[-1]] = fresh(alt)
        else:
            return None
    return picks


# ---------------------------------------------------------------- independent checks
def check_sample(sample: dict[str, Any], cs: dict[str, Any], *, exclude_artists: list[str],
                 forbidden_artists: set[str]) -> list[str]:
    problems: list[str] = []
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in sample["tools"]}
    msgs = sample["messages"]
    if [m["role"] for m in msgs[:2]] != ["system", "user"]:
        problems.append("must start with system + user")
    last = msgs[-1]
    final_args = None
    for m in msgs:
        for call in m.get("tool_calls") or []:
            name = call["function"]["name"]
            if name not in schemas:
                problems.append(f"unknown tool {name}")
                continue
            args = json.loads(call["function"]["arguments"])
            spec = schemas[name]
            missing = [r for r in spec.get("required", []) if r not in args]
            unknown = [a for a in args if a not in spec["properties"]]
            if missing or unknown:
                problems.append(f"{name}: missing {missing} unknown {unknown}")
            if not str(args.get("summary") or "").strip():
                problems.append(f"{name}: empty summary")
            if m is last:
                final_args = (name, args)
    if final_args is None or final_args[0] != "submit_recommendations" or last.get("weight", 1.0) == 0:
        return problems + ["last message must be a weighted submit_recommendations call"]
    args = final_args[1]
    if args["candidate_set_id"] != cs["candidate_set_id"]:
        problems.append("submit uses a different candidate set")
    meta = sample["meta"]
    report = validate_selection(args["picks"], cs, count=meta["count"], max_per_artist=MAX_PER_ARTIST,
                                min_tail=meta["min_tail"])
    problems += report["errors"]
    by_id = {c["song_id"]: c for c in cs["candidates"]}
    banned = {a.strip().lower() for a in exclude_artists}
    for p in args["picks"]:
        c = by_id.get(p["song_id"])
        if c is None:
            continue
        a = (c.get("artist_credit") or "").strip().lower()
        if a in banned:
            problems.append(f"excluded artist picked: {a}")
        if a in forbidden_artists:
            problems.append(f"held-out artist in train: {a}")
        if not reason_numbers_ok(p["reason"], c, p["evidence_refs"]):
            problems.append(f"reason cites numbers not in evidence: {p['reason']}")
    tail = sum(by_id[p["song_id"]]["bucket"] == "tail" for p in args["picks"] if p["song_id"] in by_id)
    for text in (args["message"], args["summary"]):
        m = re.search(r"(\d+) 首是听众较少|(\d+) 首冷门|(\d+) 首 tail", text)
        if m and int(next(g for g in m.groups() if g)) != tail:
            problems.append(f"tail count in text does not match picks: {text}")
    weighted0 = [m for m in msgs if m.get("weight") == 0]
    if (meta["kind"] == "repair") != bool(weighted0):
        problems.append("repair samples (and only those) carry one weight-0 turn")
    return problems


# ---------------------------------------------------------------- io + report
def write_dataset(result: dict[str, Any], out_dir: str | Path, *, config: dict[str, Any]) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {}
    for split in ("train", "val", "test"):
        rows = result["samples"].get(split, [])
        with (out / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for r in rows:
                handle.write(json.dumps(r, ensure_ascii=False) + "\n")
        stats[split] = summarize(rows)
    manifest = {"data_version": DATA_VERSION, "split_version": SPLIT_VERSION, "oracle_version": ORACLE_VERSION,
                "config": config, "counts": {s: len(result["samples"].get(s, [])) for s in ("train", "val", "test")},
                "target": result["target"], "attempts": result["attempts"], "rejects": result["rejects"],
                "stats": stats}
    (out / "split_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return manifest


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    m = [r["meta"] for r in rows]
    chars = sorted(x["n_chars"] for x in m)
    return {
        "n": len(rows),
        "mode": dict(Counter(x["mode"] for x in m)), "kind": dict(Counter(x["kind"] for x in m)),
        "repair_type": dict(Counter(x["repair_type"] for x in m if x["repair_type"])),
        "repair_follows_hint": sum(bool(x.get("repair_follows_hint")) for x in m),
        "lang": dict(Counter(x["lang"] for x in m)), "template": dict(sorted(Counter(x["template_id"] for x in m).items())),
        "branch": dict(Counter(x["branch_hint"] or ("both" if len(x["branches"]) > 1 else "none") for x in m)),
        "exploration": dict(sorted(Counter(x["exploration_level"] for x in m).items())),
        "count": dict(sorted(Counter(x["count"] for x in m).items())),
        "with_exclusion": sum(bool(x["exclude_artists"]) for x in m),
        "history": dict(sorted(Counter(int(x["history"]) for x in m).items())),
        "distinct_picked_songs": len({sid for r in rows for sid in _final_ids(r)}),
        "top_song_sample_share": round(max(Counter(sid for r in rows for sid in set(_final_ids(r))).values())
                                       / len(rows), 3),
        "paraphrased": sum(x["paraphrased"] for x in m),
        "tail_share": round(sum(x["tail_picks"] for x in m) / sum(x["count"] for x in m), 3),
        "heldout_artist_picks": sum(x["heldout_artist_picks"] for x in m),
        "unique_candidate_sets": len({x["candidate_set_id"] for x in m}),
        "chars": {"p50": chars[len(chars) // 2], "p95": chars[int(len(chars) * 0.95)], "max": chars[-1]},
    }


def _final_ids(row: dict[str, Any]) -> list[str]:
    args = json.loads(row["messages"][-1]["tool_calls"][0]["function"]["arguments"])
    return [p["song_id"] for p in args["picks"]]


def render_preview(rows: list[dict[str, Any]], k: int = 6) -> str:
    """Readable dump of a few samples for human review (tool outputs truncated)."""
    out = []
    for r in rows[:k]:
        meta = r["meta"]
        out.append(f"## {r['sample_id']}  ({meta['split']} / {meta['template_id']} / {meta['kind']}"
                   f"{' / ' + meta['repair_type'] if meta['repair_type'] else ''})\n")
        for m in r["messages"][1:]:
            if m["role"] == "user":
                out.append(f"**user**: {m['content']}\n")
            elif m["role"] == "tool":
                obs = json.loads(m["content"])
                data = obs.get("data")
                if isinstance(data, dict) and "candidates" in data:
                    lines = [f"  - {c['song_id']} {c['artist']} – {c['title']} [{c['bucket']}] rel {c['relevance']}"
                             for c in data["candidates"][:8]]
                    out.append(f"**tool {m['name']}** → {len(data['candidates'])} 个候选（前 8 个）\n" + "\n".join(lines) + "\n")
                else:
                    out.append(f"**tool {m['name']}** → `{m['content'][:300]}`\n")
            else:
                for call in m.get("tool_calls") or []:
                    args = json.loads(call["function"]["arguments"])
                    w = " (weight 0, 不参与训练)" if m.get("weight") == 0 else ""
                    if call["function"]["name"] == "submit_recommendations":
                        picks = "\n".join(f"  - {p['song_id']}: {p['reason']} {p['evidence_refs']}" for p in args["picks"])
                        out.append(f"**assistant → submit**{w}: {args['summary']}\n  message: {args['message']}\n{picks}\n")
                    else:
                        rest = {a: v for a, v in args.items() if a != "summary"}
                        out.append(f"**assistant → {call['function']['name']}**{w}: {args['summary']}\n  `{json.dumps(rest, ensure_ascii=False)}`\n")
        out.append("---\n")
    return "\n".join(out)


# ---------------------------------------------------------------- paraphrasing (optional, DeepSeek)
BAD_PARAPHRASE = re.compile(r"一首|一个歌|单曲推荐|\b(a|one) (song|track)\b|\d+ ?首", re.I)


def usable_paraphrase(line: str, template_text: str) -> bool:
    """Same placeholders as the template, no stray braces, and no count words ("一首", "a song")
    that would contradict the requested number of songs."""
    want = sorted(set(re.findall(r"\{\w+\}", template_text)))
    if sorted(set(re.findall(r"\{\w+\}", line))) != want or re.search(r"[{}]", re.sub(r"\{\w+\}", "", line)):
        return False
    if BAD_PARAPHRASE.search(line) and not BAD_PARAPHRASE.search(template_text):
        return False
    try:
        line.format(**{p[1:-1]: "x" for p in want})
    except (KeyError, IndexError, ValueError):
        return False
    return True


PARAPHRASE_PROMPT = """把下面这条音乐推荐请求改写成 {n} 种不同的自然说法（{lang}），像真实用户在聊天框里打字。
要求：意思完全不变；花括号占位符（如 {{seed}}、{{artist}}）必须原样保留、每个都出现；不要加数量、年代或新的要求；每行一条，不要编号，不要解释。
原句：{text}"""


def paraphrase_templates(chat_post: Callable[[dict[str, Any]], dict[str, Any]], model: str, *, n: int = 5,
                         log: Callable[..., None] = print) -> dict[str, Any]:
    """Ask an LLM for paraphrases of each template; keep only variants with exactly the same placeholders."""
    out: dict[str, list[str]] = {}
    for t in TEMPLATES:
        if not t.text:
            continue
        lang = {"zh": "中文", "en": "英文", "mix": "中英混杂"}[t.lang]
        payload = {"model": model, "temperature": 0.9, "messages": [
            {"role": "user", "content": PARAPHRASE_PROMPT.format(n=n, lang=lang, text=t.text)}]}
        reply = chat_post(payload)
        content = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        keep = []
        for line in content.splitlines():
            line = re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", line).strip()
            if not line or line == t.text or line in keep or not usable_paraphrase(line, t.text):
                continue
            keep.append(line)
        out[t.id] = keep[:n]
        log(f"  {t.id}: {len(out[t.id])} variants")
    return {"version": "paraphrases-v1", "model": model, "templates": out}


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from rateyourdj.config import load_dotenv
    from rateyourdj.data_pipeline.catalog import read_jsonl
    from rateyourdj.data_pipeline.user_context import load_user_context

    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m rateyourdj.training.sft_data")
    parser.add_argument("--user-id", default="participant_001")
    parser.add_argument("--catalog-root", default="data/catalog")
    parser.add_argument("--users-root", default="data/users")
    parser.add_argument("--index-root", default="data/index")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="generate train/val/test SFT samples")
    b.add_argument("--n", type=int, default=500, help="generated samples (fixed test queries come on top)")
    b.add_argument("--out", default="data/training/sft/sft-v1-smoke")
    b.add_argument("--seed", type=int, default=20260927)
    b.add_argument("--paraphrases", default="data/training/sft/paraphrases-v1.json")
    b.add_argument("--fixed-queries", default="eval/retrieval_queries_v1.jsonl")
    b.add_argument("--encoder", choices=["bge-m3", "stub"], default="bge-m3",
                   help="stub = zero text vectors, only for testing the pipeline without the model")
    p = sub.add_parser("paraphrase", help="LLM paraphrases of the request templates (DeepSeek)")
    p.add_argument("--out", default="data/training/sft/paraphrases-v1.json")
    p.add_argument("--n", type=int, default=5)
    args = parser.parse_args(argv)

    if args.command == "paraphrase":
        from rateyourdj.agent.llm import OpenAICompatibleChat
        llm = OpenAICompatibleChat.from_env()
        if llm is None:
            print("需要 .env 里的 DEEPSEEK_API_KEY", file=sys.stderr)
            return 2
        result = paraphrase_templates(llm._post, llm.model, n=args.n)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
        print(f"wrote {args.out}")
        return 0

    import numpy as np

    from rateyourdj.agent.factory import vector_similarity
    from rateyourdj.rag.index import load_index
    from rateyourdj.rag.retrieval import Retriever

    songs = read_jsonl(Path(args.catalog_root) / "processed" / "songs.jsonl")
    context = load_user_context(args.user_id, args.users_root, legacy_root=None)
    index = load_index(args.index_root)
    if index is None:
        print("需要向量索引（rateyourdj-rag build-index）", file=sys.stderr)
        return 2
    if args.encoder == "stub":
        class _Stub:
            name, dim = "stub-zero", int(index.matrix.shape[1])

            def encode(self, texts, batch_size=32):
                return np.zeros((len(texts), self.dim), dtype=np.float32)
        encoder = _Stub()
    else:
        from rateyourdj.rag.encoder import SentenceTransformerEncoder
        encoder = SentenceTransformerEncoder(index.manifest["model"])
    manifest_path = Path(args.catalog_root) / "manifest.json"
    catalog_version = json.loads(manifest_path.read_text("utf-8"))["catalog_version"] if manifest_path.is_file() else "unknown"
    retriever = Retriever(songs, context, index=index, encoder=encoder, catalog_version=catalog_version)
    para_path = Path(args.paraphrases)
    paraphrases = json.loads(para_path.read_text("utf-8"))["templates"] if para_path.is_file() else {}
    fixed = [json.loads(line) for line in open(args.fixed_queries, encoding="utf-8") if line.strip()] \
        if args.fixed_queries and Path(args.fixed_queries).is_file() else []
    gen = SFTDataGenerator(songs, context, retriever, similarity=vector_similarity(retriever), seed=args.seed,
                           paraphrases=paraphrases, encoder_name=encoder.name)
    print(f"held-out artists: {len(gen.heldout_artists)} ({len(gen.heldout_song_ids)} songs); "
          f"styles: { {b: (i.style_en, i.style_zh) for b, i in gen.branches.items()} }")
    result = gen.generate(args.n, fixed_queries=fixed)
    config = {"n": args.n, "seed": args.seed, "encoder": encoder.name, "index_version": index.version,
              "catalog_version": catalog_version, "paraphrases": str(para_path) if paraphrases else None,
              "fixed_queries": args.fixed_queries, "heldout_artists": len(gen.heldout_artists),
              "heldout_artist_rate": 0.15}
    manifest = write_dataset(result, args.out, config=config)
    (Path(args.out) / "heldout_artists.json").write_text(
        json.dumps(sorted(gen.heldout_artists), ensure_ascii=False, indent=0) + "\n", "utf-8")
    preview = [r for split in ("train", "test") for r in result["samples"].get(split, [])[:4]]
    (Path(args.out) / "preview.md").write_text(render_preview(preview, k=8), "utf-8")
    print(json.dumps({"counts": manifest["counts"], "rejects": manifest["rejects"]}, ensure_ascii=False))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

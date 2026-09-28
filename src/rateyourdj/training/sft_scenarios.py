"""Scenario and request-text generation for SFT data (stage 4).

Everything user-specific (seed titles, seed artists, style words) is derived from the
user context and the catalog at run time; nothing about a particular user is hard-coded.

A *template* produces two strings:
  text   what the user types (may be paraphrased later)
  query  the canonical retrieval query the agent should send: only the wanted content,
         never counts, exploration words or "not X" clauses (those go to other arguments)
Templates marked ``heldout`` are only used for validation/test ("unseen phrasing").
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from rateyourdj.rag.retrieval import ZH_TAG_HINTS

GENERIC_TAGS = {"rock", "pop/rock", "classic rock", "album rock", "british", "britannique", "melodic",
                "contemporary pop/rock", "alternative pop/rock", "brit pop", "prog-rock", "prog rock",
                "alternative/indie rock", "british trad rock", "neo-psychedelia", "concept album"}


@dataclass(frozen=True)
class Template:
    id: str
    mode: str            # single | both | none
    lang: str            # zh | en | mix
    text: str
    query: str
    heldout: bool = False
    excludes_artist: bool = False   # the template itself excludes the branch's seed artist


TEMPLATES: tuple[Template, ...] = (
    # ---- one branch
    Template("S01", "single", "zh", "来点像《{seed}》那样的歌", "像 {seed} 那样的歌"),
    Template("S02", "single", "zh", "想听和 {artist} 风格接近的歌", "和 {artist} 风格接近的歌"),
    Template("S03", "single", "zh", "推荐一些{style_zh}", "{style_zh}"),
    Template("S04", "single", "zh", "有没有类似《{seed}》、带点{style_zh}味道的", "像 {seed} 那样的{style_zh}"),
    Template("S05", "single", "en", "songs like {seed} by {artist}", "songs like {seed} by {artist}"),
    Template("S06", "single", "en", "some {style_en} tracks in the vein of {artist}", "{style_en} in the vein of {artist}"),
    Template("S07", "single", "en", "more {style_en}, please", "{style_en}"),
    Template("S08", "single", "mix", "想要一些 {style_en} 风格的歌，像 {artist} 那种", "{style_en} 风格，像 {artist} 那种"),
    Template("S09", "single", "zh", "最近一直在循环《{seed}》，再来点同样感觉的", "和 {seed} 感觉相同的歌", heldout=True),
    Template("S10", "single", "en", "I've been looping {seed} — give me more with that feel", "songs with the feel of {seed}"),
    Template("S11", "single", "zh", "{artist} 听腻了，想找同类型的其他乐队", "和 {artist} 同类型的歌", excludes_artist=True),
    Template("S12", "single", "en", "bands that sound like {artist}, but not {artist} themselves", "bands that sound like {artist}",
             heldout=True, excludes_artist=True),
    Template("S13", "single", "mix", "《{seed}》这种 vibe 的歌还有吗", "像 {seed} 的歌"),
    Template("S14", "single", "zh", "想要{style_zh}，最好和 {artist} 有点像", "{style_zh}，和 {artist} 有点像", heldout=True),
    # ---- both branches
    Template("B01", "both", "zh", "介于 {artist_a} 和 {artist_b} 之间的歌", "介于 {artist_a} 和 {artist_b} 之间的歌"),
    Template("B02", "both", "zh", "{style_a_zh}一点的{style_b_zh}", "{style_a_zh}一点的{style_b_zh}"),
    Template("B03", "both", "en", "something between {seed_a} and {seed_b}", "something between {seed_a} and {seed_b}"),
    Template("B04", "both", "en", "{style_a_en} meets {style_b_en}", "{style_a_en} meets {style_b_en}", heldout=True),
    Template("B05", "both", "mix", "两边都要一点：{artist_a} 那种和 {artist_b} 那种", "像 {artist_a} 也像 {artist_b} 的歌"),
    # ---- no specific content
    Template("N01", "none", "zh", "", ""),
    Template("N02", "none", "zh", "随便推荐点我可能喜欢的", ""),
    Template("N03", "none", "en", "surprise me with something I'd like", "", heldout=True),
    Template("N04", "none", "zh", "按我的口味来一批", ""),
)

HIGH_EXPLORE = {"zh": ["，越冷门越好", "，要小众一点的", "，别推大热门"], "en": [", deep cuts only", ", the more obscure the better"]}
LOW_EXPLORE = {"zh": ["，熟悉一点的就行", "，来点经典的"], "en": [", keep it familiar", ", well-known ones are fine"]}
EXCLUDE_CLAUSE = {"zh": ["，但不要 {x} 的", "，{x} 就不用了"], "en": [", but no {x}", ", skip {x} though"]}
MORE_PREFIX = {"zh": ["再来一批：", "换一批，"], "en": ["another batch: ", "more please — "]}


def style_words(branch_tags: dict[str, dict[str, float]], vocab: set[str]) -> dict[str, dict[str, list[str]]]:
    """Per branch: English style tags distinctive for it, and Chinese words the retriever understands."""
    zh_of: dict[str, str] = {}
    for word, tags in ZH_TAG_HINTS.items():
        for t in tags:
            zh_of.setdefault(t, word)
    out: dict[str, dict[str, list[str]]] = {}
    for b, profile in branch_tags.items():
        others = [p for o, p in branch_tags.items() if o != b]
        en = [t for t, w in sorted(profile.items(), key=lambda x: -x[1])
              if w >= 0.1 and t not in GENERIC_TAGS and t in vocab
              and all(w >= 2 * o.get(t, 0.0) for o in others)][:5]
        zh = []
        for t in en:
            word = zh_of.get(t)
            if word and word not in [z.split("摇滚")[0] for z in zh]:
                zh.append(word if "摇滚" in word else word + "摇滚")
        out[b] = {"en": en, "zh": zh}
    return out


@dataclass
class BranchInfo:
    branch_id: str
    seeds: list[str]          # seed titles (base title, as the retriever recognises them)
    artists: list[str]
    style_en: list[str]
    style_zh: list[str]


@dataclass
class Scenario:
    scenario_id: str
    template: Template
    split: str
    text: str
    query: str
    branch_hint: str | None          # what the agent should pass
    branches: list[str]              # branches the request is about
    exploration: float
    count: int
    lang: str
    exclude_artists: list[str] = field(default_factory=list)
    exclude_from: str | None = None  # "template" | "clause"
    history: int = 0                 # songs already recommended earlier in the session (excluded)
    asked_more: bool = False         # the text says "another batch"
    kind: str = "direct"             # direct | ranked | repair
    repair_type: str | None = None
    paraphrased: bool = False
    ui_hint: str | None = None       # branch chosen in the UI (sent in the user message)
    fixed_query_id: str | None = None

    def meta(self) -> dict[str, Any]:
        return {"scenario_id": self.scenario_id, "template_id": self.template.id, "mode": self.template.mode,
                "lang": self.lang, "branch_hint": self.branch_hint, "branches": self.branches,
                "exploration_level": self.exploration, "count": self.count,
                "exclude_artists": self.exclude_artists, "exclude_from": self.exclude_from,
                "history": self.history, "asked_more": self.asked_more, "kind": self.kind, "repair_type": self.repair_type,
                "paraphrased": self.paraphrased, "fixed_query_id": self.fixed_query_id,
                "split": self.split}


def fill(template: str, values: dict[str, str]) -> str:
    return template.format(**values) if template else ""


def draw_values(t: Template, rng: random.Random, branches: dict[str, BranchInfo]) -> tuple[dict[str, str], list[str]]:
    ids = sorted(branches)
    if t.mode == "single":
        b = branches[rng.choice(ids)]
        vals = {"seed": rng.choice(b.seeds), "artist": rng.choice(b.artists),
                "style_en": rng.choice(b.style_en) if b.style_en else "rock",
                "style_zh": rng.choice(b.style_zh) if b.style_zh else "摇滚"}
        return vals, [b.branch_id]
    if t.mode == "both":
        a, c = rng.sample(ids, 2)
        A, C = branches[a], branches[c]
        vals = {"seed_a": rng.choice(A.seeds), "seed_b": rng.choice(C.seeds),
                "artist_a": rng.choice(A.artists), "artist_b": rng.choice(C.artists),
                "style_a_en": rng.choice(A.style_en), "style_b_en": rng.choice(C.style_en),
                "style_a_zh": rng.choice(A.style_zh).replace("摇滚", ""), "style_b_zh": rng.choice(C.style_zh)}
        return vals, [a, c]
    return {}, []

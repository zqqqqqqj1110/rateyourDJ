"""V2 agent tools (agent-tools-contract.md V2.2) with the standard observation envelope.

Model-callable: get_user_context, retrieve_candidates, get_track_facts, rank_candidates,
and submit_recommendations (the structured final answer). Every tool call carries a
short, user-visible ``summary`` of the decision instead of hidden reasoning.
"""

from __future__ import annotations

from typing import Any, Callable

from rateyourdj.ranking import STRATEGIES, rank_candidates

SUMMARY = {"type": "string", "description": "一句话说明这一步的决定（可见的决策摘要，不要写长篇推理）"}


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {"summary": SUMMARY, **properties},
                       "required": ["summary", *required], "additionalProperties": False}}}


def envelope(tool: str, status: str, data: Any = None, diagnostics: list[str] | None = None) -> dict[str, Any]:
    return {"tool": tool, "status": status, "data": data, "diagnostics": diagnostics or [],
            "retryable": status == "error"}


class Toolbox:
    def __init__(self, songs_by_id: dict[str, dict[str, Any]], context: dict[str, Any], retriever: Any, *,
                 similarity: Callable[[str, str], float] | None = None,
                 seed_artists: set[str] | None = None, count: int = 10,
                 max_per_artist: int = 2) -> None:
        self.songs = songs_by_id
        self.context = context
        self.retriever = retriever
        self.similarity = similarity
        self.seed_artists = seed_artists or set()
        self.count = count
        self.max_per_artist = max_per_artist
        self.candidate_sets: dict[str, dict[str, Any]] = {}
        self.branch_ids = [b["branch_id"] for b in context["seed_branches"]]

    # ------------------------------------------------------------------ schemas
    def schemas(self) -> list[dict[str, Any]]:
        return [
            _fn("get_user_context", "读取用户的兴趣分支、种子歌和探索强度。", {}, []),
            _fn("retrieve_candidates", "从曲库多路召回候选歌曲（只能从返回的候选里推荐）。", {
                "query": {"type": "string", "description": "检索用的请求文本，可改写用户原话"},
                "branch_hint": {"type": ["string", "null"], "enum": [*self.branch_ids, None],
                                "description": "指定兴趣分支；不确定就填 null"},
                "exploration_level": {"type": "number", "minimum": 0, "maximum": 1,
                                      "description": "0 = 熟悉相关，1 = 尽量探索"},
                "limit": {"type": "integer", "minimum": 10, "maximum": 40},
                "exclude_artists": {"type": "array", "items": {"type": "string"}, "maxItems": 10,
                                    "description": "用户明确不要的艺人名（按曲库艺人名匹配），这些艺人的歌不会进入候选"},
            }, ["query"]),
            _fn("get_track_facts", "查看候选歌曲的曲库事实（标签、年份、听众数）。", {
                "song_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
            }, ["song_ids"]),
            _fn("rank_candidates", "用确定性策略给候选集排序，作为参考。", {
                "candidate_set_id": {"type": "string"},
                "strategy": {"type": "string", "enum": list(STRATEGIES)},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            }, ["candidate_set_id", "strategy"]),
            _fn("submit_recommendations", "提交最终推荐。歌曲必须全部来自同一个候选集。", {
                "candidate_set_id": {"type": "string"},
                "message": {"type": "string", "description": "给用户看的一段简短中文说明"},
                "picks": {"type": "array", "items": {"type": "object", "properties": {
                    "song_id": {"type": "string"},
                    "reason": {"type": "string", "description": "一句理由，只能基于该歌曲的证据"},
                    "evidence_refs": {"type": "array", "items": {"type": "integer"},
                                      "description": "引用的证据下标"}},
                    "required": ["song_id", "reason", "evidence_refs"]}},
            }, ["candidate_set_id", "message", "picks"]),
        ]

    # ------------------------------------------------------------------ execution
    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return envelope(name, "error", diagnostics=[f"unknown tool {name!r}"])
        try:
            return handler(args)
        except (ValueError, KeyError, TypeError) as error:
            return envelope(name, "error", diagnostics=[str(error)])

    def _tool_get_user_context(self, args: dict[str, Any]) -> dict[str, Any]:
        branches = []
        for b in self.context["seed_branches"]:
            seeds = [self.songs[s] for s in b["seed_song_ids"] if s in self.songs]
            branches.append({"branch_id": b["branch_id"], "label": b.get("label", ""),
                             "seed_songs": [f"{s.get('artist_credit')} - {s.get('title')}" for s in seeds]})
        return envelope("get_user_context", "ok", {
            "user_id": self.context["user_id"], "branches": branches,
            "exploration_level": self.context.get("exploration_level", 0.5),
            "already_recommended": len(self.context.get("recommended_song_ids", []))})

    def _tool_retrieve_candidates(self, args: dict[str, Any]) -> dict[str, Any]:
        branch = args.get("branch_hint") or None
        if branch is not None and branch not in self.branch_ids:
            raise ValueError(f"branch_hint must be one of {self.branch_ids} or null")
        e = args.get("exploration_level")
        if e is not None and not 0 <= float(e) <= 1:
            raise ValueError("exploration_level must be within [0, 1]")
        diagnostics: list[str] = []
        branch = self._check_branch(str(args.get("query") or ""), branch, diagnostics)
        limit = int(args.get("limit") or 30)
        excluded_artists = [str(a) for a in (args.get("exclude_artists") or [])][:10]
        excluded_ids = self.artist_song_ids(excluded_artists)
        kwargs = {"exclude_song_ids": excluded_ids} if excluded_ids else {}
        result = self.retriever.retrieve(str(args.get("query") or ""), branch_hint=branch,
                                         exploration_level=None if e is None else float(e),
                                         limit=max(10, min(40, limit)), **kwargs)
        result["excluded_artists"] = excluded_artists
        self.candidate_sets[result["candidate_set_id"]] = result
        if result["fallback"]:
            diagnostics.append(f"fallback: {result['fallback']}")
        return envelope("retrieve_candidates", "ok" if result["fallback"] is None else "partial", {
            "candidate_set_id": result["candidate_set_id"],
            "exploration_level": result["query"]["exploration_level"],
            "candidates": [compact(c) for c in result["candidates"]]}, diagnostics)

    def _check_branch(self, query: str, branch: str | None, diagnostics: list[str]) -> str | None:
        """Guard: seed songs / seed artists named in the query decide the branch.

        If the query names seeds of exactly one branch and the model passed another branch, the
        named branch wins; if it names seeds of several branches, no single branch is forced.
        (Stage 4 eval: "像 Champagne Supernova 那样宏大的歌" was sent with the wrong branch.)"""
        resolve = getattr(self.retriever, "_resolve_references", None)
        if branch is None or resolve is None or not query:
            return branch
        _, _, named = resolve(query)
        if not named or branch in named and len(named) == 1:
            return branch
        corrected = next(iter(named)) if len(named) == 1 else None
        diagnostics.append(f"branch_hint 已更正：请求点名的种子属于 {', '.join(sorted(named))}，"
                           f"branch_hint 由 {branch} 改为 {corrected}")
        return corrected

    def artist_song_ids(self, names: list[str]) -> list[str]:
        """Catalog songs credited to any of ``names`` (case-insensitive; exact credit or listed artist)."""
        wanted = {n.strip().lower() for n in names if n and n.strip()}
        if not wanted:
            return []
        return sorted(sid for sid, s in self.songs.items()
                      if (s.get("artist_credit") or "").strip().lower() in wanted
                      or any((a.get("name") or "").strip().lower() in wanted for a in s.get("artists", [])))

    def _tool_get_track_facts(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {c["song_id"] for s in self.candidate_sets.values() for c in s["candidates"]}
        facts, errors = [], []
        for sid in list(args.get("song_ids") or [])[:10]:
            if sid not in allowed:
                errors.append(f"{sid} is not in a retrieved candidate set")
                continue
            s = self.songs[sid]
            facts.append({"song_id": sid, "title": s.get("title"), "artist": s.get("artist_credit"),
                          "year": (s.get("release") or {}).get("year"),
                          "album": (s.get("release") or {}).get("title"),
                          "tags": [t["name"] for t in s.get("tags", [])[:8]],
                          "listeners": s["popularity"].get("listener_count"),
                          "bucket": s["popularity"]["bucket"]})
        return envelope("get_track_facts", "ok" if facts and not errors else ("partial" if facts else "error"),
                        facts, errors)

    def _tool_rank_candidates(self, args: dict[str, Any]) -> dict[str, Any]:
        cs = self.candidate_sets.get(args.get("candidate_set_id", ""))
        if cs is None:
            raise ValueError("unknown candidate_set_id; call retrieve_candidates first")
        out = self.rank(cs, strategy=args.get("strategy", "rag-tailmix-v1"),
                        count=int(args.get("count") or self.count))
        return envelope("rank_candidates", "ok", {
            "strategy": out["strategy"], "plan": out["plan"],
            "ranked": [{"rank": r["rank"], "song_id": r["song_id"], "slot": r["score_breakdown"]["slot"],
                        "title": r["title"], "artist": r["artist_credit"], "bucket": r["bucket"]}
                       for r in out["ranked"]]})

    def rank(self, candidate_set: dict[str, Any], *, strategy: str, count: int) -> dict[str, Any]:
        return rank_candidates(candidate_set, self.context, strategy=strategy, count=count,
                               max_per_artist=self.max_per_artist, similarity=self.similarity,
                               seed_artists=self.seed_artists)


def compact(c: dict[str, Any]) -> dict[str, Any]:
    """Token-light view of a candidate for the model."""
    return {"song_id": c["song_id"], "title": c.get("title"), "artist": c.get("artist_credit"),
            "bucket": c["bucket"], "relevance": c["relevance"], "tail_score": c["tail_score"],
            "channel": c["channels"][0]["channel"],
            "evidence": [f"{i}: {ev['detail']}" for i, ev in enumerate(c["evidence"])]}

"""Conversation agent (ReAct) on top of RecommenderV2.

Each user message goes to an LLM that decides what the turn needs:

  recommend_songs            new songs wanted -> runs the unchanged recommender
                             (selection agent / pipeline / interleaving), at most once per turn
  get_recent_recommendations questions about songs already shown ("why the 2nd one?")
  lookup_song                facts about a song or artist in the catalog
  (plain reply, no tool)     answer / small talk, or the text that goes with the new cards

The selection step itself is untouched, so the stage 4/5 models, their evaluations
and the interleaving comparisons stay valid. Answers about songs must come from
tool results (stored reasons and evidence, catalog facts), never from the model's
own knowledge.

Sessions live in data/users/<user_id>/sessions/<session_id>.json: every turn keeps
the user text, the reply, the action taken, the rewritten request, the shown cards
and the tool steps, so the page can be restored and every reply traced.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rateyourdj.contracts import normalize_name

from .llm import ChatModel, LLMError, tool_call_arguments
from .service import RecommenderV2, _read_jsonl

CONVERSE_VERSION = "converse-react-v1"
HISTORY_TURNS = 6
BUCKET_WORDS = {"head": "热门", "mid": "中等热度", "tail": "冷门", "unknown": "热度未知"}
MORE_PATTERN = re.compile(r"^(换一批|再来|再来一批|再来几首|还有吗|更多)[。！!？?\s]*$")

SUMMARY = {"type": "string", "description": "一句话说明这一步的决定"}

SYSTEM_PROMPT = """你是 rateyourDJ 的音乐 DJ，在和用户对话。你通过工具获取信息，然后用中文回复用户。

先判断这一轮需要什么：
- 用户想要新歌（包括“换一批”“再来几首更冷门的”“来点像刚才第二首那样的”）：调用 recommend_songs，每轮最多一次。request 要写成完整、独立的需求，把“刚才那首”“第二首”这类指代换成具体歌名或风格（需要时先调用 get_recent_recommendations 查清楚是哪首）。
- 用户在问已经推荐过的歌（为什么推荐、第几首是什么、哪首最冷门、和种子歌有什么关系）：调用 get_recent_recommendations，按其中的 reason 和 evidence 回答，不要推荐新歌。
- 用户问某首歌或某个艺人：调用 lookup_song 查曲库。
- 闲聊、感谢、对推荐的评价：直接回复，不要推荐新歌。

规则：
1. 关于歌曲的事实（年份、专辑、风格、热度、为什么推荐）只能来自工具返回的内容；工具里没有的就直说不知道，不要编造。
2. 不要提策略、模型、奖励、候选集、ID 等内部术语；说“熟悉 / 冷门”而不是 head / tail。
3. 只有用户明确要求时才在 recommend_songs 里设置 exploration_level（例如“更冷门一点”“要熟悉的”）或 count（例如“来 3 首”）；否则不要传，使用页面上的设置。
4. 最后不调用工具、直接回复用户，一到三句话。如果推荐了歌，简单介绍这批歌的整体思路，不要逐首复述，卡片上已经有每首的理由。

页面设置：探索强度 {exploration}（0 熟悉 – 1 探索），每次 {count} 首。
用户的口味分支：{branches}。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {"summary": SUMMARY, **properties},
                       "required": ["summary", *required], "additionalProperties": False}}}


TOOLS = [
    _fn("recommend_songs", "推荐一批新歌（会显示成卡片）。每轮最多调用一次。", {
        "request": {"type": "string", "description": "完整、独立的需求描述，已替换掉指代"},
        "exploration_level": {"type": "number", "minimum": 0, "maximum": 1,
                              "description": "只有用户明确要求更熟悉或更冷门时才设置"},
        "count": {"type": "integer", "minimum": 1, "maximum": 20,
                  "description": "只有用户明确要求数量时才设置"},
    }, ["request"]),
    _fn("get_recent_recommendations", "查看本次对话里最近几轮推荐过的歌：序号、歌名、来源、理由、依据，以及用户给过的反馈。", {
        "rounds": {"type": "integer", "minimum": 1, "maximum": 5, "description": "最近几轮，默认 1"},
    }, []),
    _fn("lookup_song", "按歌名或艺人在曲库里查歌，返回风格标签、年份、专辑、热度，以及是否推荐过。", {
        "query": {"type": "string", "description": "歌名、艺人，或“歌名 艺人”"},
    }, ["query"]),
]


class ConversationAgent:
    def __init__(self, recommender: RecommenderV2, llm: ChatModel | None, *, max_steps: int = 5) -> None:
        self.recommender = recommender
        self.llm = llm
        self.max_steps = max_steps

    # ------------------------------------------------------------------ sessions
    def _session_path(self, user_id: str, session_id: str) -> Path:
        if not re.fullmatch(r"conv_[0-9a-f]{12}", session_id or ""):
            raise ValueError("invalid session_id")
        return self.recommender._user_dir(user_id) / "sessions" / f"{session_id}.json"

    def load_session(self, user_id: str, session_id: str) -> dict[str, Any]:
        path = self._session_path(user_id, session_id)
        if not path.is_file():
            raise LookupError(f"unknown session {session_id!r}")
        return json.loads(path.read_text("utf-8"))

    def _save_session(self, user_id: str, session: dict[str, Any]) -> None:
        path = self._session_path(user_id, session["session_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False, indent=1) + "\n", "utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------ one turn
    def chat(self, user_id: str, message: str, *, session_id: str | None = None, count: int = 10,
             exploration_level: float | None = None, interleave: list[str] | None = None) -> dict[str, Any]:
        if not self.recommender.has_user(user_id):
            raise LookupError(f"unknown V2 user {user_id!r}")
        if session_id:
            session = self.load_session(user_id, session_id)
        else:
            session = {"session_id": "conv_" + uuid.uuid4().hex[:12], "user_id": user_id,
                       "created_at": _now(), "turns": []}
        context = self.recommender.status(user_id)
        e = context.get("exploration_level", 0.5) if exploration_level is None else exploration_level
        turn_state = _Turn(self, user_id, session, count=count, exploration=e, interleave=interleave)
        started = time.perf_counter()
        if self.llm is None:
            reply = turn_state.fallback(message, "no LLM configured")
        else:
            reply = turn_state.run(message, context)
        turn = {"at": _now(), "user": message, "reply": reply, "action": turn_state.action,
                "request": turn_state.request, "run_id": (turn_state.result or {}).get("run_id"),
                "recommendations": (turn_state.result or {}).get("recommendations", []),
                "steps": turn_state.steps, "model": getattr(self.llm, "name", None),
                "fallback_reason": turn_state.fallback_reason, "phase": self.recommender.feedback_phase,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
        session["turns"].append(turn)
        session["updated_at"] = _now()
        self._save_session(user_id, session)
        result = turn_state.result or {"run_id": None, "user_id": user_id, "candidate_set_id": None,
                                       "phase": self.recommender.feedback_phase, "interleaving": None,
                                       "recommendations": []}
        return {**result, "message": reply, "session_id": session["session_id"], "action": turn_state.action,
                "conversation": {"version": CONVERSE_VERSION, "model": turn["model"],
                                 "fallback_reason": turn_state.fallback_reason, "steps": turn_state.steps}}


class _Turn:
    """State of one conversation turn (tool execution + the ReAct loop)."""

    def __init__(self, agent: ConversationAgent, user_id: str, session: dict[str, Any], *, count: int,
                 exploration: float, interleave: list[str] | None) -> None:
        self.agent = agent
        self.rec = agent.recommender
        self.user_id = user_id
        self.session = session
        self.count = count
        self.exploration = exploration
        self.interleave = interleave
        self.result: dict[str, Any] | None = None
        self.request: str | None = None
        self.action = "answer"
        self.steps: list[dict[str, Any]] = []
        self.fallback_reason: str | None = None

    # ---------------------------------------------------------------- the loop
    def run(self, message: str, context: dict[str, Any]) -> str:
        branches = "、".join(f"{b['branch_id']}（{b.get('label') or ''}）" for b in context.get("branches", []))
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT.format(
            exploration=self.exploration, count=self.count, branches=branches or "未设置")}]
        messages += self._history()
        messages.append({"role": "user", "content": message})
        for _ in range(self.agent.max_steps):
            t0 = time.perf_counter()
            try:
                reply = self.agent.llm.chat(messages, TOOLS)
            except LLMError as error:
                return self.fallback(message, f"llm_error: {error}")
            calls = reply.get("tool_calls") or []
            if not calls:
                text = (reply.get("content") or "").strip()
                self.steps.append({"step": len(self.steps) + 1, "tool": None, "status": "reply"})
                return text or self._default_reply()
            messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = (call.get("function") or {}).get("name", "")
                try:
                    args = tool_call_arguments(call)
                    obs = self.execute(name, args)
                except (ValueError, json.JSONDecodeError) as error:
                    args, obs = {}, {"status": "error", "error": f"bad arguments: {error}"}
                self.steps.append({"step": len(self.steps) + 1, "tool": name, "summary": args.get("summary"),
                                   "arguments": {k: v for k, v in args.items() if k != "summary"},
                                   "status": obs.get("status"),
                                   "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": json.dumps(obs, ensure_ascii=False)})
        self.fallback_reason = f"step budget exhausted ({self.agent.max_steps})"
        return self._default_reply()

    def _history(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for turn in self.session["turns"][-HISTORY_TURNS:]:
            out.append({"role": "user", "content": turn["user"]})
            reply = turn.get("reply") or ""
            recs = turn.get("recommendations") or []
            if recs:
                listed = "；".join(f"{r['rank']}. {r['track'].get('title')} - {r['track'].get('artist')}" for r in recs)
                reply = f"{reply}\n（本轮推荐：{listed}）"
            out.append({"role": "assistant", "content": reply})
        return out

    def _default_reply(self) -> str:
        return "为你挑了这批歌。" if self.result else "我没太明白，可以换个说法吗？"

    # ---------------------------------------------------------------- fallback
    def fallback(self, message: str, reason: str) -> str:
        """No usable LLM: every message is treated as a recommendation request."""
        self.fallback_reason = reason
        request = message
        if MORE_PATTERN.match(message.strip()):
            previous = [t.get("request") for t in self.session["turns"] if t.get("request")]
            if previous:
                request = previous[-1]
        if self.result is None:
            self._recommend(request, None, None)
        return self._default_reply()

    # ---------------------------------------------------------------- tools
    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "recommend_songs":
            if self.result is not None:
                return {"status": "error", "error": "本轮已经推荐过一批了，请直接回复用户。"}
            request = str(args.get("request") or "").strip()
            if not request:
                return {"status": "error", "error": "request 不能为空"}
            e = args.get("exploration_level")
            if e is not None and not (isinstance(e, (int, float)) and 0 <= float(e) <= 1):
                return {"status": "error", "error": "exploration_level 必须在 0–1 之间"}
            count = args.get("count")
            if count is not None and not (isinstance(count, int) and 1 <= count <= 20):
                return {"status": "error", "error": "count 必须是 1–20 的整数"}
            self._recommend(request, e, count)
            shown = [{"rank": r["rank"], "title": r["track"].get("title"), "artist": r["track"].get("artist"),
                      "source": r.get("source_label"), "reason": r.get("reason")}
                     for r in self.result["recommendations"]]
            return {"status": "ok", "shown": shown}
        if name == "get_recent_recommendations":
            rounds = args.get("rounds") or 1
            if not isinstance(rounds, int) or not 1 <= rounds <= 5:
                return {"status": "error", "error": "rounds 必须是 1–5 的整数"}
            return self._recent(rounds)
        if name == "lookup_song":
            return self._lookup(str(args.get("query") or ""))
        return {"status": "error", "error": f"unknown tool {name!r}"}

    def _recommend(self, request: str, e: float | None, count: int | None) -> None:
        self.request = request
        self.action = "recommend"
        self.result = self.rec.recommend(self.user_id, request, count=count or self.count,
                                         exploration_level=self.exploration if e is None else float(e),
                                         mode="auto", interleave=self.interleave)

    def _recent(self, rounds: int) -> dict[str, Any]:
        turns = [t for t in self.session["turns"] if t.get("recommendations")]
        if self.result is not None:
            turns = turns + [{"user": "(本轮)", "request": self.request, "recommendations": self.result["recommendations"]}]
        if not turns:
            return {"status": "ok", "rounds": [], "note": "本次对话还没有推荐过歌"}
        feedback = self._feedback_by_impression()
        out = []
        for i, turn in enumerate(turns[-rounds:], 1):
            songs = []
            for r in turn["recommendations"]:
                track = r.get("track") or {}
                songs.append({"rank": r["rank"], "title": track.get("title"), "artist": track.get("artist"),
                              "album": track.get("album"), "year": track.get("release_year"),
                              "source": r.get("source_label"), "reason": r.get("reason"),
                              "evidence": [ev.get("detail") for ev in r.get("evidence_items") or [] if ev.get("detail")],
                              "user_feedback": feedback.get(r.get("impression_id"), {})})
            out.append({"round": len(turns) - min(rounds, len(turns)) + i, "request": turn.get("request"),
                        "songs": songs})
        return {"status": "ok", "rounds": out, "note": "round 越大越新；rank 是卡片上的序号"}

    def _feedback_by_impression(self) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for fb in _read_jsonl(self.rec._user_dir(self.user_id) / "feedback.jsonl"):
            item = merged.setdefault(fb["impression_id"], {})
            item.update({k: v for k, v in (fb.get("survey") or {}).items() if v is not None})
            for ev in fb.get("events", []):
                if ev.get("type") in ("liked", "saved", "hide", "completed", "quick_skip"):
                    item.setdefault("events", []).append(ev["type"])
        return merged

    def _lookup(self, query: str) -> dict[str, Any]:
        q = normalize_name(query)
        if len(q) < 2:
            return {"status": "error", "error": "query 太短"}
        words = [w for w in q.split() if w]
        ctx_path = self.rec._user_dir(self.user_id) / "context.json"
        ctx = json.loads(ctx_path.read_text("utf-8")) if ctx_path.is_file() else {}
        recommended = set(ctx.get("recommended_song_ids", []))
        heard = set(ctx.get("heard_song_ids", []))
        seeds = {s for b in ctx.get("seed_branches", []) for s in b.get("seed_song_ids", [])}
        scored = []
        for song in self.rec.songs:
            title = normalize_name(song.get("title") or "")
            artist = normalize_name(song.get("artist_credit") or "")
            text = f"{title} {artist}"
            if q == title or q == artist:
                score = 3
            elif q in text:
                score = 2
            elif words and all(w in text for w in words):
                score = 1
            else:
                continue
            popularity = song.get("popularity") or {}
            scored.append((-score, -(popularity.get("listener_count") or 0), song))
        scored.sort(key=lambda x: (x[0], x[1]))
        matches = []
        for _, _, song in scored[:5]:
            popularity = song.get("popularity") or {}
            tags = sorted(song.get("tags") or [], key=lambda t: -t.get("weight", 0))
            matches.append({"title": song.get("title"), "artist": song.get("artist_credit"),
                            "album": (song.get("release") or {}).get("title"),
                            "year": (song.get("release") or {}).get("year"),
                            "tags": [t["name"] for t in tags[:6]],
                            "popularity": BUCKET_WORDS.get(popularity.get("bucket") or "unknown", "热度未知"),
                            "listeners": popularity.get("listener_count"),
                            "is_seed": song["song_id"] in seeds,
                            "recommended_before": song["song_id"] in recommended,
                            "user_heard": song["song_id"] in heard})
        return {"status": "ok", "matches": matches,
                "note": "曲库里没有找到" if not matches else f"共 {len(scored)} 条匹配，显示前 {len(matches)} 条"}

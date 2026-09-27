"""RecommenderV2: context -> (ReAct agent | deterministic pipeline) -> validate ->
impressions -> response; plus FeedbackV2 ("no impression, no feedback").

Storage (all under data/users/<user_id>/, append-only where it matters):
  context.json        UserContextV2 (recommended_song_ids / heard / exclusions updated)
  impressions.jsonl   one ImpressionV2 per shown song
  feedback.jsonl      FeedbackV2
  runs/<run_id>.json  full run: request, steps (tool, summary, status), validation
Candidate sets are saved under data/candidates/ so every run can be recomputed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rateyourdj.contracts import validate_record
from rateyourdj.contracts.v2 import SCHEMA_VERSIONS
from rateyourdj.data_pipeline.catalog import read_jsonl
from rateyourdj.data_pipeline.user_context import load_user_context, save_user_context

from .llm import ChatModel
from .loop import run_agent, run_pipeline
from .tools import Toolbox

SOURCE_LABELS = {"tail": "长尾发现", "explore": "探索"}
FEEDBACK_EVENTS = {"play_start", "play_progress", "completed", "quick_skip", "liked", "saved", "hide"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class RecommenderV2:
    def __init__(self, *, catalog_root: str | Path = "data/catalog", users_root: str | Path = "data/users",
                 candidates_root: str | Path = "data/candidates",
                 retriever_factory: Callable[[list[dict[str, Any]], dict[str, Any]], Any],
                 llm: ChatModel | None = None, similarity_factory: Callable[[Any], Any] | None = None,
                 feedback_phase: str = "dev") -> None:
        self.catalog_root = Path(catalog_root)
        self.users_root = Path(users_root)
        self.candidates_root = Path(candidates_root)
        self.retriever_factory = retriever_factory
        self.similarity_factory = similarity_factory
        self.llm = llm
        self.feedback_phase = feedback_phase
        self._songs: list[dict[str, Any]] | None = None

    @property
    def songs(self) -> list[dict[str, Any]]:
        if self._songs is None:
            self._songs = read_jsonl(self.catalog_root / "processed" / "songs.jsonl")
        return self._songs

    def has_user(self, user_id: str) -> bool:
        try:
            return (self.users_root / user_id / "context.json").is_file()
        except (TypeError, ValueError):
            return False

    def _user_dir(self, user_id: str) -> Path:
        return self.users_root / user_id

    # ---------------------------------------------------------------- recommend
    def recommend(self, user_id: str, text: str = "", *, count: int = 10,
                  exploration_level: float | None = None, branch_hint: str | None = None,
                  mode: str = "auto", strategy: str | None = None, record: bool = True) -> dict[str, Any]:
        context = load_user_context(user_id, self.users_root, legacy_root=None)
        retriever = self.retriever_factory(self.songs, context)
        songs_by_id = {s["song_id"]: s for s in self.songs}
        seed_artists = {songs_by_id[s].get("artist_credit") or "" for b in context["seed_branches"]
                        for s in b["seed_song_ids"] if s in songs_by_id}
        toolbox = Toolbox(songs_by_id, context, retriever,
                          similarity=self.similarity_factory(retriever) if self.similarity_factory else None,
                          seed_artists=seed_artists, count=count)
        use_agent = mode == "agent" or (mode == "auto" and self.llm is not None and strategy is None)
        if use_agent and self.llm is not None:
            run = run_agent(self.llm, toolbox, request_text=text, count=count,
                            exploration_level=exploration_level, branch_hint=branch_hint)
        else:
            run = run_pipeline(toolbox, request_text=text, count=count, exploration_level=exploration_level,
                               branch_hint=branch_hint, strategy=strategy or "rag-tailmix-v1")
            if use_agent:
                run["fallback_reason"] = "no LLM configured"
        response = self._response(user_id, run, songs_by_id, record=record)
        if record:
            context["recommended_song_ids"] = list(dict.fromkeys(
                context.get("recommended_song_ids", []) + [p["song_id"] for p in run["picks"]]))
            save_user_context(context, self.users_root)
            self._save_run(user_id, run)
        return response

    def _response(self, user_id: str, run: dict[str, Any], songs_by_id: dict[str, Any], *,
                  record: bool) -> dict[str, Any]:
        cs = run["candidate_set"]
        by_id = {c["song_id"]: c for c in cs["candidates"]}
        shown_at = _now()
        recs = []
        for rank, pick in enumerate(run["picks"], 1):
            cand = by_id[pick["song_id"]]
            song = songs_by_id[pick["song_id"]]
            channel = cand["channels"][0]["channel"]
            impression = {
                "schema_version": SCHEMA_VERSIONS["impression"],
                "impression_id": "imp_" + uuid.uuid4().hex[:16],
                "user_id": user_id, "run_id": run["run_id"], "request_id": cs["request_id"],
                "candidate_set_id": cs["candidate_set_id"], "song_id": pick["song_id"], "rank": rank,
                "bucket": cand["bucket"], "channel": channel,
                "strategy_version": run["strategy_version"], "model_version": run.get("model"),
                "playback_source": song.get("playback", {}).get("source", "none"), "shown_at": shown_at,
            }
            validate_record("impression", impression)
            if record:
                _append(self._user_dir(user_id) / "impressions.jsonl", impression)
            cited = [cand["evidence"][i] for i in pick.get("evidence_refs", []) if 0 <= i < len(cand["evidence"])]
            playback = song.get("playback", {})
            recs.append({
                "rank": rank, "impression_id": impression["impression_id"], "song_id": pick["song_id"],
                "bucket": cand["bucket"], "channel": channel, "strategy_version": run["strategy_version"],
                "model_version": run.get("model"),
                "source_label": SOURCE_LABELS.get(channel) or ("长尾发现" if cand["bucket"] == "tail" else "熟悉相关"),
                "reason": pick["reason"], "evidence_items": cited or cand["evidence"][:1],
                "playback": {"source": playback.get("source", "none"), "url": playback.get("url"),
                             "verified": bool(playback.get("verified"))},
                "track": {"track_id": pick["song_id"], "title": song.get("title"),
                          "artist": song.get("artist_credit"),
                          "album": (song.get("release") or {}).get("title"),
                          "release_year": (song.get("release") or {}).get("year"),
                          "external_urls": {"spotify": playback.get("url") if playback.get("source") == "spotify" else None,
                                            "youtube": playback.get("url") if playback.get("source") == "youtube" else None}},
            })
        return {"run_id": run["run_id"], "user_id": user_id, "message": run["message"],
                "strategy_version": run["strategy_version"], "model_version": run.get("model"),
                "fallback_reason": run.get("fallback_reason"), "candidate_set_id": cs["candidate_set_id"],
                "interleaving": None, "recommendations": recs, "latency_ms": run.get("latency_ms"),
                "trace": {"steps": run["steps"], "validation": run["validation"], "request": run["request"]}}

    def _save_run(self, user_id: str, run: dict[str, Any]) -> None:
        cs = run["candidate_set"]
        self.candidates_root.mkdir(parents=True, exist_ok=True)
        (self.candidates_root / f"{cs['candidate_set_id']}.json").write_text(
            json.dumps(cs, ensure_ascii=False) + "\n", "utf-8")
        runs = self._user_dir(user_id) / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        slim = {k: v for k, v in run.items() if k != "candidate_set"}
        slim["candidate_set_id"] = cs["candidate_set_id"]
        slim["saved_at"] = _now()
        (runs / f"{run['run_id']}.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1) + "\n", "utf-8")

    # ---------------------------------------------------------------- feedback
    def find_impression(self, user_id: str, impression_id: str) -> dict[str, Any] | None:
        path = self._user_dir(user_id) / "impressions.jsonl"
        if not path.is_file():
            return None
        for line in path.read_text("utf-8").splitlines():
            if impression_id in line:
                record = json.loads(line)
                if record.get("impression_id") == impression_id:
                    return record
        return None

    def record_feedback(self, user_id: str, impression_id: str, *, event: str | None = None,
                        seconds: float | None = None, fraction: float | None = None,
                        survey: dict[str, Any] | None = None) -> dict[str, Any]:
        impression = self.find_impression(user_id, impression_id)
        if impression is None:
            raise LookupError(f"unknown impression {impression_id!r} for {user_id!r}: no impression, no feedback")
        events = []
        if event is not None:
            if event not in FEEDBACK_EVENTS:
                raise ValueError(f"event must be one of {sorted(FEEDBACK_EVENTS)}")
            ev: dict[str, Any] = {"type": event, "at": _now()}
            if seconds is not None:
                ev["seconds"] = float(seconds)
            if fraction is not None:
                ev["fraction"] = float(fraction)
            events.append(ev)
        record = {
            "schema_version": SCHEMA_VERSIONS["feedback"],
            "feedback_id": "fb_" + uuid.uuid4().hex[:16], "impression_id": impression_id,
            "user_id": user_id, "song_id": impression["song_id"], "run_id": impression["run_id"],
            "strategy_version": impression["strategy_version"], "channel": impression["channel"],
            "bucket": impression["bucket"], "events": events, "survey": dict(survey or {}),
            "phase": self.feedback_phase, "created_at": _now(),
        }
        if not events and not record["survey"]:
            raise ValueError("feedback needs an event or a survey")
        validate_record("feedback", record)
        _append(self._user_dir(user_id) / "feedback.jsonl", record)
        context = load_user_context(user_id, self.users_root, legacy_root=None)
        if record["survey"].get("heard_before") == "yes":
            context["heard_song_ids"] = list(dict.fromkeys(context.get("heard_song_ids", []) + [record["song_id"]]))
        if event == "hide":
            context.setdefault("exclusions", {}).setdefault("song_ids", [])
            context["exclusions"]["song_ids"] = list(dict.fromkeys(
                context["exclusions"]["song_ids"] + [record["song_id"]]))
        context["recent_feedback_ids"] = (context.get("recent_feedback_ids", []) + [record["feedback_id"]])[-50:]
        save_user_context(context, self.users_root)
        return record

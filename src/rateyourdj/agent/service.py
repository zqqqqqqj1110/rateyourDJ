"""RecommenderV2: context -> (ReAct agent | deterministic pipeline) -> validate ->
impressions -> response; plus FeedbackV2 ("no impression, no feedback").

Storage (all under data/users/<user_id>/, append-only where it matters):
  context.json        UserContextV2 (recommended_song_ids / heard / exclusions updated)
  impressions.jsonl   one ImpressionV2 per shown song
  feedback.jsonl      FeedbackV2
  interleavings.jsonl one record per interleaved request (arms, per-arm runs, shown order)
  runs/<run_id>.json  full run: request, steps (tool, summary, status), validation
  sessions/<id>.json  conversation turns (see converse.py)
Candidate sets are saved under data/candidates/ so every run can be recomputed.

Every impression, run and feedback record carries the study ``phase`` ("dev" or
"final") the server was started with; final-phase records must never be used
for training or tuning.
"""

from __future__ import annotations

import json
import random
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import quote, quote_plus

from rateyourdj.contracts import validate_record
from rateyourdj.contracts.v2 import FEEDBACK_PHASES, SCHEMA_VERSIONS
from rateyourdj.data_pipeline.catalog import read_jsonl
from rateyourdj.data_pipeline.user_context import load_user_context, save_user_context
from rateyourdj.ranking import STRATEGIES as PIPELINE_STRATEGIES

from .interleave import INTERLEAVING_VERSION, assign_labels, team_draft
from .llm import ChatModel
from .loop import run_agent, run_pipeline
from .tools import Toolbox

SOURCE_LABELS = {"tail": "长尾发现", "explore": "探索"}
FEEDBACK_EVENTS = {"play_start", "play_progress", "completed", "quick_skip", "liked", "saved", "hide"}
SURVEY_KEYS = {"heard_before", "relevance", "discovery_value", "too_unfamiliar", "would_save", "reject_reason"}
DELETE_SCOPES = ("interactions", "all")

# An arm runner produces one run dict (same shape as run_pipeline / run_agent) for a request.
ArmRunner = Callable[..., dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _search_links(title: str, artist: str) -> dict[str, str]:
    q = quote_plus(f"{artist} {title}".strip())
    return {"youtube": f"https://www.youtube.com/results?search_query={q}",
            "spotify": "https://open.spotify.com/search/" + quote(f"{artist} {title}".strip())}


class RecommenderV2:
    def __init__(self, *, catalog_root: str | Path = "data/catalog", users_root: str | Path = "data/users",
                 candidates_root: str | Path = "data/candidates",
                 retriever_factory: Callable[[list[dict[str, Any]], dict[str, Any]], Any],
                 llm: ChatModel | None = None, similarity_factory: Callable[[Any], Any] | None = None,
                 feedback_phase: str = "dev", arm_runners: dict[str, ArmRunner] | None = None,
                 playback: Any | None = None, rng: random.Random | None = None) -> None:
        if feedback_phase not in FEEDBACK_PHASES:
            raise ValueError(f"feedback_phase must be one of {FEEDBACK_PHASES}")
        self.catalog_root = Path(catalog_root)
        self.users_root = Path(users_root)
        self.candidates_root = Path(candidates_root)
        self.retriever_factory = retriever_factory
        self.similarity_factory = similarity_factory
        self.llm = llm
        self.feedback_phase = feedback_phase
        self.arm_runners = dict(arm_runners or {})
        self.playback = playback  # PlaybackResolver-like: resolve(song) -> result, save()
        self.rng = rng or random.Random()
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
        if not isinstance(user_id, str) or not user_id or "/" in user_id or user_id.startswith("."):
            raise ValueError("invalid user_id")
        return self.users_root / user_id

    def available_strategies(self) -> list[str]:
        return list(PIPELINE_STRATEGIES) + [s for s in self.arm_runners if s not in PIPELINE_STRATEGIES]

    def status(self, user_id: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"phase": self.feedback_phase, "strategies": self.available_strategies(),
                               "agent_model": getattr(self.llm, "name", None) if self.llm else None,
                               "playback_lookup": self.playback is not None,
                               "interleaving_version": INTERLEAVING_VERSION}
        if user_id is not None:
            out["user_id"] = user_id
            out["known_user"] = self.has_user(user_id)
            if out["known_user"]:
                ctx = load_user_context(user_id, self.users_root, legacy_root=None)
                out["exploration_level"] = ctx.get("exploration_level", 0.5)
                out["branches"] = [{"branch_id": b["branch_id"], "label": b.get("label")}
                                   for b in ctx["seed_branches"]]
                d = self._user_dir(user_id)
                out["counts"] = {"impressions": len(_read_jsonl(d / "impressions.jsonl")),
                                 "feedback": len(_read_jsonl(d / "feedback.jsonl"))}
        return out

    # ---------------------------------------------------------------- recommend
    def _toolbox(self, context: dict[str, Any], count: int) -> tuple[Toolbox, dict[str, Any]]:
        retriever = self.retriever_factory(self.songs, context)
        songs_by_id = {s["song_id"]: s for s in self.songs}
        seed_artists = {songs_by_id[s].get("artist_credit") or "" for b in context["seed_branches"]
                        for s in b["seed_song_ids"] if s in songs_by_id}
        toolbox = Toolbox(songs_by_id, context, retriever,
                          similarity=self.similarity_factory(retriever) if self.similarity_factory else None,
                          seed_artists=seed_artists, count=count)
        return toolbox, songs_by_id

    def _run(self, strategy: str | None, mode: str, context: dict[str, Any], text: str, count: int,
             exploration_level: float | None, branch_hint: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        toolbox, songs_by_id = self._toolbox(context, count)
        kwargs = {"request_text": text, "count": count, "exploration_level": exploration_level,
                  "branch_hint": branch_hint}
        if strategy is not None and strategy in self.arm_runners:
            run = self.arm_runners[strategy](toolbox, **kwargs)
            run.setdefault("arm", strategy)
            return run, songs_by_id
        if strategy is not None and strategy not in PIPELINE_STRATEGIES:
            raise ValueError(f"unknown strategy {strategy!r}; available: {self.available_strategies()}")
        use_agent = mode == "agent" or (mode == "auto" and self.llm is not None and strategy is None)
        if use_agent and self.llm is not None:
            run = run_agent(self.llm, toolbox, **kwargs)
        else:
            run = run_pipeline(toolbox, strategy=strategy or "rag-tailmix-v1", **kwargs)
            if use_agent:
                run["fallback_reason"] = "no LLM configured"
        run.setdefault("arm", strategy or run["strategy_version"])
        return run, songs_by_id

    def recommend(self, user_id: str, text: str = "", *, count: int = 10,
                  exploration_level: float | None = None, branch_hint: str | None = None,
                  mode: str = "auto", strategy: str | None = None, record: bool = True,
                  interleave: Sequence[str] | None = None) -> dict[str, Any]:
        context = load_user_context(user_id, self.users_root, legacy_root=None)
        if interleave:
            return self._recommend_interleaved(user_id, context, text, list(interleave), count=count,
                                               exploration_level=exploration_level,
                                               branch_hint=branch_hint, record=record)
        run, songs_by_id = self._run(strategy, mode, context, text, count, exploration_level, branch_hint)
        cs = run["candidate_set"]
        by_id = {c["song_id"]: c for c in cs["candidates"]}
        shown = [(p, by_id[p["song_id"]], run, None) for p in run["picks"]]
        response = self._response(user_id, run["run_id"], cs, shown, songs_by_id, record=record)
        # the pipeline's own message is technical ("确定性排序"); the participant sees a plain one
        message = "为你挑了这批歌。" if run["strategy_version"] in PIPELINE_STRATEGIES else run["message"]
        response.update({"message": message, "strategy_version": run["strategy_version"],
                         "model_version": run.get("model"), "fallback_reason": run.get("fallback_reason"),
                         "latency_ms": run.get("latency_ms"),
                         "trace": {"steps": run["steps"], "validation": run["validation"],
                                   "request": run["request"]}})
        if record:
            self._remember_shown(context, [p["song_id"] for p in run["picks"]])
            self._save_run(user_id, run)
        return response

    def _recommend_interleaved(self, user_id: str, context: dict[str, Any], text: str, strategies: list[str], *,
                               count: int, exploration_level: float | None, branch_hint: str | None,
                               record: bool) -> dict[str, Any]:
        arms = assign_labels(strategies, self.rng)
        runs: dict[str, dict[str, Any]] = {}
        songs_by_id: dict[str, Any] = {}
        for label, strategy in arms.items():
            runs[label], songs_by_id = self._run(strategy, "pipeline", context, text, count,
                                                 exploration_level, branch_hint)
        shown_pairs = team_draft({label: [p["song_id"] for p in run["picks"]] for label, run in runs.items()},
                                 count, self.rng)
        pair_id = "pair_" + uuid.uuid4().hex[:12]
        shown = []
        for song_id, label in shown_pairs:
            run = runs[label]
            pick = next(p for p in run["picks"] if p["song_id"] == song_id)
            cand = next(c for c in run["candidate_set"]["candidates"] if c["song_id"] == song_id)
            inter = {"pair_id": pair_id, "arm": label, "arms": dict(arms), "method": INTERLEAVING_VERSION}
            shown.append((pick, cand, run, inter))
        # the request (and so the candidate set) is the same for both arms; report arm A's
        first = runs["A"]
        response = self._response(user_id, pair_id, first["candidate_set"], shown, songs_by_id, record=record,
                                  blind=True)
        response.update({"message": "为你挑了这批歌。", "strategy_version": None, "model_version": None,
                         "fallback_reason": None,
                         "latency_ms": round(sum(r.get("latency_ms") or 0 for r in runs.values()), 1),
                         "interleaving": {"pair_id": pair_id, "method": INTERLEAVING_VERSION},
                         "trace": None})
        if record:
            for run in runs.values():
                self._save_run(user_id, run)
            self._remember_shown(context, [s for s, _ in shown_pairs])
            _append(self._user_dir(user_id) / "interleavings.jsonl", {
                "pair_id": pair_id, "user_id": user_id, "method": INTERLEAVING_VERSION, "arms": arms,
                "run_ids": {label: run["run_id"] for label, run in runs.items()},
                "arm_lists": {label: [p["song_id"] for p in run["picks"]] for label, run in runs.items()},
                "shown": [{"song_id": s, "arm": a} for s, a in shown_pairs],
                "request": first["request"], "phase": self.feedback_phase, "created_at": _now()})
        return response

    def _remember_shown(self, context: dict[str, Any], song_ids: list[str]) -> None:
        context["recommended_song_ids"] = list(dict.fromkeys(context.get("recommended_song_ids", []) + song_ids))
        save_user_context(context, self.users_root)

    def _playback(self, song: dict[str, Any]) -> dict[str, Any]:
        playback = dict(song.get("playback") or {})
        if playback.get("source", "none") == "none" and self.playback is not None:
            try:
                from rateyourdj.data_pipeline.playback import apply_playback
                result = self.playback.resolve(song)
                apply_playback(song, result)
                playback = dict(song["playback"])
            except Exception as error:  # network problems must never break a recommendation
                playback = {**playback, "source": "none", "reason": f"lookup_failed: {type(error).__name__}"}
        return playback

    def _response(self, user_id: str, run_id: str, cs: dict[str, Any],
                  shown: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]],
                  songs_by_id: dict[str, Any], *, record: bool, blind: bool = False) -> dict[str, Any]:
        shown_at = _now()
        recs = []
        for rank, (pick, cand, run, inter) in enumerate(shown, 1):
            song = songs_by_id[pick["song_id"]]
            channel = cand["channels"][0]["channel"]
            playback = self._playback(song)
            impression = {
                "schema_version": SCHEMA_VERSIONS["impression"],
                "impression_id": "imp_" + uuid.uuid4().hex[:16],
                "user_id": user_id, "run_id": run["run_id"], "request_id": run["candidate_set"]["request_id"],
                "candidate_set_id": run["candidate_set"]["candidate_set_id"], "song_id": pick["song_id"],
                "rank": rank, "bucket": cand["bucket"], "channel": channel,
                "strategy_version": run["strategy_version"], "model_version": run.get("model"),
                "playback_source": playback.get("source", "none"), "shown_at": shown_at,
                "phase": self.feedback_phase,
            }
            if inter is not None:
                impression["interleaving"] = inter
            validate_record("impression", impression)
            if record:
                _append(self._user_dir(user_id) / "impressions.jsonl", impression)
            cited = [cand["evidence"][i] for i in pick.get("evidence_refs", []) if 0 <= i < len(cand["evidence"])]
            title, artist = song.get("title") or "", song.get("artist_credit") or ""
            source = playback.get("source", "none")
            rec = {
                "rank": rank, "impression_id": impression["impression_id"], "song_id": pick["song_id"],
                "bucket": cand["bucket"], "channel": channel,
                "source_label": SOURCE_LABELS.get(channel) or ("长尾发现" if cand["bucket"] == "tail" else "熟悉相关"),
                "reason": pick["reason"], "evidence_items": cited or cand["evidence"][:1],
                "playback": {"source": source, "url": playback.get("url"),
                             "verified": bool(playback.get("verified")),
                             "search_links": _search_links(title, artist) if source == "none" else None},
                "track": {"track_id": pick["song_id"], "title": title, "artist": artist,
                          "album": (song.get("release") or {}).get("title"),
                          "release_year": (song.get("release") or {}).get("year"),
                          "external_urls": {"spotify": playback.get("url") if source == "spotify" else None,
                                            "youtube": playback.get("url") if source == "youtube" else None}},
            }
            if not blind:  # interleaved lists hide which strategy produced which song
                rec["strategy_version"] = run["strategy_version"]
                rec["model_version"] = run.get("model")
            recs.append(rec)
        if self.playback is not None:
            try:
                self.playback.save()
            except OSError:
                pass
        return {"run_id": run_id, "user_id": user_id, "candidate_set_id": cs["candidate_set_id"],
                "phase": self.feedback_phase, "interleaving": None, "recommendations": recs}

    def _save_run(self, user_id: str, run: dict[str, Any]) -> None:
        cs = run["candidate_set"]
        self.candidates_root.mkdir(parents=True, exist_ok=True)
        (self.candidates_root / f"{cs['candidate_set_id']}.json").write_text(
            json.dumps(cs, ensure_ascii=False) + "\n", "utf-8")
        runs = self._user_dir(user_id) / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        slim = {k: v for k, v in run.items() if k != "candidate_set"}
        slim["candidate_set_id"] = cs["candidate_set_id"]
        slim["phase"] = self.feedback_phase
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
        survey = dict(survey or {})
        unknown = set(survey) - SURVEY_KEYS
        if unknown:
            raise ValueError(f"unknown survey fields: {sorted(unknown)}")
        for key in ("relevance", "discovery_value"):
            value = survey.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5):
                raise ValueError(f"survey.{key} must be an integer 1-5")
        for key in ("too_unfamiliar", "would_save"):
            if survey.get(key) is not None and not isinstance(survey[key], bool):
                raise ValueError(f"survey.{key} must be a boolean")
        record = {
            "schema_version": SCHEMA_VERSIONS["feedback"],
            "feedback_id": "fb_" + uuid.uuid4().hex[:16], "impression_id": impression_id,
            "user_id": user_id, "song_id": impression["song_id"], "run_id": impression["run_id"],
            "strategy_version": impression["strategy_version"], "channel": impression["channel"],
            "bucket": impression["bucket"], "events": events, "survey": survey,
            "phase": self.feedback_phase, "created_at": _now(),
        }
        if impression.get("interleaving"):
            record["interleaving"] = impression["interleaving"]
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

    def saved_songs(self, user_id: str) -> list[dict[str, Any]]:
        """Songs with a ``saved`` event, newest first (one entry per song)."""
        songs_by_id = {s["song_id"]: s for s in self.songs}
        out: dict[str, dict[str, Any]] = {}
        for fb in _read_jsonl(self._user_dir(user_id) / "feedback.jsonl"):
            if any(e.get("type") == "saved" for e in fb.get("events", [])) and fb["song_id"] in songs_by_id:
                song = songs_by_id[fb["song_id"]]
                playback = song.get("playback") or {}
                out.pop(fb["song_id"], None)
                out[fb["song_id"]] = {"song_id": fb["song_id"], "title": song.get("title"),
                                      "artist": song.get("artist_credit"),
                                      "album": (song.get("release") or {}).get("title"),
                                      "bucket": (song.get("popularity") or {}).get("bucket"),
                                      "url": playback.get("url") if playback.get("verified") else None,
                                      "saved_at": fb["created_at"]}
        return list(reversed(out.values()))

    # ---------------------------------------------------------------- deletion
    def delete_user_data(self, user_id: str, scope: str = "interactions") -> dict[str, Any]:
        """Delete a participant's data.

        ``interactions``: impressions, feedback, interleavings, runs, conversation sessions
        and the candidate sets those runs used; context lists derived from them are reset (seeds kept).
        ``all``: the whole user directory, including context and seeds.
        """
        if scope not in DELETE_SCOPES:
            raise ValueError(f"scope must be one of {DELETE_SCOPES}")
        d = self._user_dir(user_id)
        if not d.is_dir():
            raise LookupError(f"no data for {user_id!r}")
        removed: dict[str, int] = {}
        cs_ids = set()
        runs_dir = d / "runs"
        if runs_dir.is_dir():
            for path in runs_dir.glob("*.json"):
                try:
                    cs_ids.add(json.loads(path.read_text("utf-8")).get("candidate_set_id"))
                except (OSError, ValueError):
                    pass
        for cs_id in filter(None, cs_ids):
            path = self.candidates_root / f"{cs_id}.json"
            if path.is_file():
                path.unlink()
                removed["candidate_sets"] = removed.get("candidate_sets", 0) + 1
        if scope == "all":
            shutil.rmtree(d)
            removed["user_dir"] = 1
            return {"user_id": user_id, "scope": scope, "removed": removed}
        for name in ("impressions.jsonl", "feedback.jsonl", "interleavings.jsonl"):
            path = d / name
            if path.is_file():
                removed[name] = len(_read_jsonl(path))
                path.unlink()
        if runs_dir.is_dir():
            removed["runs"] = len(list(runs_dir.glob("*.json")))
            shutil.rmtree(runs_dir)
        sessions_dir = d / "sessions"
        if sessions_dir.is_dir():
            removed["sessions"] = len(list(sessions_dir.glob("*.json")))
            shutil.rmtree(sessions_dir)
        context = load_user_context(user_id, self.users_root, legacy_root=None)
        context["heard_song_ids"] = []
        context["recommended_song_ids"] = []
        context["recent_feedback_ids"] = []
        context.setdefault("exclusions", {})["song_ids"] = []
        save_user_context(context, self.users_root)
        return {"user_id": user_id, "scope": scope, "removed": removed}

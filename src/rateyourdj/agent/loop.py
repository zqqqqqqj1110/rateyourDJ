"""The V2 ReAct loop: the model decides which tool to call next; the program executes
it and feeds the observation back, until the model submits a selection.

Guards:
  * every submission goes through validate_selection (candidate set only, count,
    artist cap, min tail, evidence refs); one repair round is allowed
  * any LLM error, malformed call, step budget overrun or second invalid
    submission -> deterministic fallback (rag-tailmix-v1 on the latest candidate set)
No hidden reasoning is stored: each step keeps the model's one-line ``summary``.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from rateyourdj.ranking import slot_plan, validate_selection

from .llm import ChatModel, LLMError, tool_call_arguments
from .tools import Toolbox

AGENT_STRATEGY = "agent-react-v1"
FALLBACK_STRATEGY = "rag-tailmix-v1"

SYSTEM_PROMPT = """你是 rateyourDJ，一个音乐推荐 Agent：在用户熟悉的口味和值得探索的冷门歌之间取得平衡，平衡点由探索强度决定。你通过调用工具工作。

硬性规则：
1. 只能推荐 retrieve_candidates 返回的候选集里的歌，绝不提及或推荐候选集以外的歌曲。
2. 用 submit_recommendations 提交恰好 {count} 首，全部来自同一个 candidate_set_id；其中至少 {min_tail} 首的 bucket 是 tail；同一艺人最多 {max_per_artist} 首。
   本次探索强度 {exploration}：tail 歌曲的目标数量是 {tail_low}–{tail_high} 首，其余优先选与口味最相关的熟悉歌曲（head / mid）。
3. 每首的 reason 只能依据该歌曲的 evidence，并用 evidence_refs 引用证据下标；不要编造证据里没有的事实（年份、专辑、故事等）。
4. 每次调用工具都在 summary 里用一句话说明这一步的决定，不要写长篇推理。
5. 用户提到的风格、年代、情绪、排除要求要体现在检索参数（query / branch_hint / exploration_level / exclude_artists）或最终选择里；query 只写想要的内容，不要把“不要某艺人”写进 query，而是放进 exclude_artists。
6. message 用中文，一两句话介绍这批推荐。

建议流程：retrieve_candidates →（需要时 get_track_facts 或 rank_candidates）→ submit_recommendations。"""


def run_agent(llm: ChatModel, toolbox: Toolbox, *, request_text: str, count: int = 10,
              exploration_level: float | None = None, branch_hint: str | None = None,
              max_steps: int = 8, max_repairs: int = 2) -> dict[str, Any]:
    started = time.perf_counter()
    e = toolbox.context.get("exploration_level", 0.5) if exploration_level is None else exploration_level
    min_tail = slot_plan(count, e)["tail"]
    messages = build_messages(toolbox, request_text=request_text, count=count, exploration_level=e,
                              branch_hint=branch_hint)
    tools = toolbox.schemas()
    steps: list[dict[str, Any]] = []
    repairs = nudges = 0
    last_set: dict[str, Any] | None = None

    def finish(picks, message, candidate_set, strategy, fallback_reason, validation):
        return {"run_id": "run_" + uuid.uuid4().hex[:12], "strategy_version": strategy,
                "model": llm.name, "fallback_reason": fallback_reason, "message": message,
                "picks": picks, "candidate_set": candidate_set, "validation": validation,
                "steps": steps, "repairs": repairs, "request": {"text": request_text, "count": count,
                                            "exploration_level": e, "branch_hint": branch_hint,
                                            "min_tail": min_tail},
                "latency_ms": round((time.perf_counter() - started) * 1000, 1)}

    def fallback(reason: str):
        cs = last_set
        if cs is None:
            cs = toolbox.retriever.retrieve(request_text, branch_hint=branch_hint, exploration_level=e)
        picks, validation = deterministic_picks(toolbox, cs, count, min_tail)
        steps.append({"step": len(steps) + 1, "tool": "fallback", "summary": reason, "status": "ok"})
        return finish(picks, "按确定性排序为你挑选了这批歌。", cs, FALLBACK_STRATEGY, reason, validation)

    for turn in range(max_steps):
        t0 = time.perf_counter()
        try:
            reply = llm.chat(messages, tools)
        except LLMError as error:
            return fallback(f"llm_error: {error}")
        calls = reply.get("tool_calls") or []
        messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls}
                        if calls else {"role": "assistant", "content": reply.get("content") or ""})
        if not calls:
            steps.append({"step": len(steps) + 1, "tool": None, "summary": (reply.get("content") or "")[:200],
                          "status": "no_tool_call"})
            if nudges >= 1:
                return fallback("model stopped without submitting")
            nudges += 1
            messages.append({"role": "user", "content": "请调用 submit_recommendations 提交结果。"})
            continue
        for call in calls:
            name = (call.get("function") or {}).get("name", "")
            try:
                args = tool_call_arguments(call)
            except (ValueError, json.JSONDecodeError) as error:
                obs = {"tool": name, "status": "error", "diagnostics": [f"bad arguments: {error}"]}
                args = {}
            else:
                if name == "submit_recommendations":
                    cs = toolbox.candidate_sets.get(args.get("candidate_set_id", ""))
                    if cs is None:
                        report = {"ok": False, "errors": ["unknown candidate_set_id"], "out_of_set": 0}
                    else:
                        report = validate_selection(args.get("picks"), cs, count=count,
                                                    max_per_artist=toolbox.max_per_artist,
                                                    min_tail=min_tail)
                    steps.append({"step": len(steps) + 1, "tool": name, "summary": args.get("summary"),
                                  "arguments": {"candidate_set_id": args.get("candidate_set_id"),
                                                "n_picks": len(args.get("picks") or [])},
                                  "status": "ok" if report["ok"] else "invalid",
                                  "validation": report,
                                  "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
                    if report["ok"]:
                        return finish(args["picks"], args.get("message") or "", cs, AGENT_STRATEGY, None, report)
                    if repairs >= max_repairs:
                        last_set = cs or last_set
                        return fallback("invalid selection: " + "; ".join(report["errors"][:3]))
                    repairs += 1
                    obs = repair_observation(report, args.get("picks") or [], cs, toolbox.max_per_artist)
                else:
                    obs = toolbox.execute(name, args)
                    if name == "retrieve_candidates" and obs["status"] != "error":
                        last_set = toolbox.candidate_sets[obs["data"]["candidate_set_id"]]
                    steps.append({"step": len(steps) + 1, "tool": name, "summary": args.get("summary"),
                                  "arguments": {k: v for k, v in args.items() if k != "summary"},
                                  "status": obs["status"], "diagnostics": obs.get("diagnostics", []),
                                  "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": json.dumps(obs, ensure_ascii=False)})
    return fallback(f"step budget exhausted ({max_steps})")


def build_messages(toolbox: Toolbox, *, request_text: str, count: int, exploration_level: float,
                   branch_hint: str | None = None) -> list[dict[str, Any]]:
    """System + user messages exactly as the agent sees them (shared with SFT data generation)."""
    e = exploration_level
    min_tail = slot_plan(count, e)["tail"]
    branches = ", ".join(toolbox.branch_ids)
    user = (f"用户请求：{request_text.strip() or '（没有具体请求，按我的口味推荐）'}\n"
            f"需要 {count} 首；探索强度 {e}；兴趣分支：{branches}"
            + (f"；用户指定分支：{branch_hint}" if branch_hint else ""))
    low, high = tail_target(count, e)
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(
            count=count, min_tail=min_tail, max_per_artist=toolbox.max_per_artist, exploration=e,
            tail_low=low, tail_high=high)},
        {"role": "user", "content": user}]


def repair_observation(report: dict[str, Any], picks: list[Any], candidate_set: dict[str, Any] | None,
                       max_per_artist: int) -> dict[str, Any]:
    """The tool observation returned for an invalid submission (shared with SFT data generation)."""
    return {"tool": "submit_recommendations", "status": "error",
            "diagnostics": ["选择未通过校验，请按下面的要求修改后重新提交 submit_recommendations：",
                            *repair_hints(report, picks, candidate_set, max_per_artist)]}


def repair_hints(report: dict[str, Any], picks: list[Any], candidate_set: dict[str, Any] | None,
                 max_per_artist: int) -> list[str]:
    """Turn structured violations into concrete instructions: which picks to drop, what to add."""
    if candidate_set is None:
        return ["candidate_set_id 无效：请使用 retrieve_candidates 返回的 candidate_set_id。"]
    by_id = {c["song_id"]: c for c in candidate_set["candidates"]}
    chosen = {p.get("song_id") for p in picks if isinstance(p, dict)}
    counts: dict[str, int] = {}
    for sid in chosen:
        if sid in by_id:
            a = (by_id[sid].get("artist_credit") or "").strip().lower()
            counts[a] = counts.get(a, 0) + 1

    def alternatives(n: int, tail_only: bool = False) -> str:
        options = repair_alternatives(candidate_set, chosen, counts, max_per_artist, n, tail_only)
        return "；".join(f"{c['song_id']}（{c.get('artist_credit')} - {c.get('title')}，{c['bucket']}）"
                        for c in options) or "无"

    hints: list[str] = []
    for v in report.get("violations", []):
        kind = v.get("type")
        if kind == "artist_cap":
            names = [f"{sid}（{by_id[sid].get('title')}）" for sid in v["song_ids"] if sid in by_id]
            hints.append(f"艺人 {v['artist']} 选了 {len(v['song_ids'])} 首（最多 {v['max']} 首）："
                         f"{'、'.join(names)}。请从中去掉 {v['remove']} 首，换成其他艺人的歌，可选：{alternatives(v['remove'])}")
        elif kind == "out_of_set":
            hints.append(f"{v['song_id']} 不在候选集中，必须删除，换成候选集里的歌，可选：{alternatives(1)}")
        elif kind == "duplicate":
            hints.append(f"{v['song_id']} 重复了，保留一次，另选：{alternatives(1)}")
        elif kind == "excluded":
            hints.append(f"{v['song_id']} 被用户排除，必须删除，另选：{alternatives(1)}")
        elif kind == "count":
            hints.append(f"需要恰好 {v['expected']} 首，现在是 {v['got']} 首。"
                         + (f"可补充：{alternatives(v['expected'] - v['got'])}" if v["got"] < v["expected"] else "请删掉多余的。"))
        elif kind == "min_tail":
            hints.append(f"tail 歌曲只有 {v['have']} 首，至少需要 {v['need']} 首，可换入：{alternatives(v['need'] - v['have'], True)}")
        elif kind == "bad_evidence_refs":
            hints.append(f"{v['song_id']} 的 evidence_refs 无效，只能引用 {v['valid_range'][0]}–{v['valid_range'][1]}")
        elif kind == "empty_reason":
            hints.append(f"{v['song_id']} 缺少 reason")
    return hints or report.get("errors", [])[:8]


def repair_alternatives(candidate_set: dict[str, Any], chosen: set[str], artist_counts: dict[str, int],
                        max_per_artist: int, n: int, tail_only: bool = False) -> list[dict[str, Any]]:
    """Replacement candidates offered in repair hints: most relevant first, not chosen, artist not full."""
    return [c for c in sorted(candidate_set["candidates"], key=lambda c: -c["relevance"])
            if c["song_id"] not in chosen
            and artist_counts.get((c.get("artist_credit") or "").strip().lower(), 0) < max_per_artist
            and (not tail_only or c["bucket"] == "tail")][: max(3, n + 2)]


def tail_target(count: int, exploration: float) -> tuple[int, int]:
    """Soft target for the agent: from the tail-mix minimum up to tail + explore slots + 1.
    count=10: e=0.1 -> 1-2, e=0.5 -> 3-5, e=0.9 -> 5-8. Guidance only (not validated)."""
    plan = slot_plan(count, exploration)
    return plan["tail"], min(count, plan["tail"] + plan["explore"] + 1)


def deterministic_picks(toolbox: Toolbox, candidate_set: dict[str, Any], count: int, min_tail: int,
                        strategy: str = FALLBACK_STRATEGY) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranked = toolbox.rank(candidate_set, strategy=strategy, count=count)["ranked"]
    picks = [{"song_id": r["song_id"], "reason": r["reason"], "evidence_refs": r["evidence_refs"]}
             for r in ranked]
    report = validate_selection(picks, candidate_set, count=count, max_per_artist=toolbox.max_per_artist,
                                min_tail=min_tail if strategy == "rag-tailmix-v1" else 0)
    return picks, report


def run_pipeline(toolbox: Toolbox, *, request_text: str, count: int = 10,
                 exploration_level: float | None = None, branch_hint: str | None = None,
                 strategy: str = FALLBACK_STRATEGY) -> dict[str, Any]:
    """No-LLM path: retrieve -> rank -> validate (the stage 3 baselines)."""
    started = time.perf_counter()
    e = toolbox.context.get("exploration_level", 0.5) if exploration_level is None else exploration_level
    min_tail = slot_plan(count, e)["tail"]
    cs = toolbox.retriever.retrieve(request_text, branch_hint=branch_hint, exploration_level=e)
    toolbox.candidate_sets[cs["candidate_set_id"]] = cs
    picks, report = deterministic_picks(toolbox, cs, count, min_tail, strategy)
    return {"run_id": "run_" + uuid.uuid4().hex[:12], "strategy_version": strategy, "model": None,
            "fallback_reason": None, "message": "按确定性排序为你挑选了这批歌。", "picks": picks,
            "candidate_set": cs, "validation": report,
            "steps": [{"step": 1, "tool": "retrieve_candidates", "status": "ok"},
                      {"step": 2, "tool": "rank_candidates", "arguments": {"strategy": strategy},
                       "status": "ok"}],
            "request": {"text": request_text, "count": count, "exploration_level": e,
                        "branch_hint": branch_hint, "min_tail": min_tail},
            "latency_ms": round((time.perf_counter() - started) * 1000, 1)}

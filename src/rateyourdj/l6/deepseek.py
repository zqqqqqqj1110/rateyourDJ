from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .provider import (
    AgentDecision,
    AgentTurn,
    LLMProviderError,
    LLMResponseError,
)


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_SECONDS = 45

DeepSeekRequest = Callable[[dict[str, Any]], dict[str, Any]]

_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "agent_update_request",
        "description": (
            "Update the structured recommendation request after interpreting the "
            "user's natural language. Only include fields that need correction or "
            "additional constraints."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": (
                        "ReAct reasoning: why this request update is needed now. "
                        "Required."
                    ),
                },
                "summary": {"type": "string"},
                "request_patch": {
                    "type": "object",
                    "properties": {
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
                        "max_per_artist": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                        },
                        "min_retrieval_score": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "preference_terms": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "exclude_terms": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "reference_artists": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "avoid_artists": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "refinement_notes": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "intent": {
                            "type": "string",
                            "enum": ["recommend", "more"],
                        },
                        "exclude_seen": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["thought", "summary", "request_patch"],
            "additionalProperties": False,
        },
    },
}

_FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "agent_finish",
        "description": (
            "Finish only after the tool history contains enough eligible tracks "
            "or the profile is empty. Do not expose private chain-of-thought."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": (
                        "ReAct reasoning: why finishing now is justified. "
                        "Required."
                    ),
                },
                "summary": {"type": "string"},
                "response_text": {"type": "string"},
            },
            "required": ["thought", "summary"],
            "additionalProperties": False,
        },
    },
}

_SYSTEM_PROMPT = """\
You are the rateyourDJ recommendation orchestration agent, and you operate as a
ReAct agent: you reason then act, one step at a time, observing tool results
before deciding the next step.

On EVERY step you call exactly one function, and EVERY function call must include
a `thought` argument: one or two sentences of explicit reasoning that (a) reflect
on the latest observation in the tool history and (b) justify the action you are
about to take. The thought is a concise rationale, not hidden chain-of-thought;
do not include private deliberation, just the verifiable reasoning for this step.

Action policy:
First correct the structured request with agent_update_request when the fallback
parser missed user intent. Then use the provided read-only tools to find
recommendation candidates. If search_tracks is available, prefer it for new music
discovery from external providers. Use local rank_candidates/L4 ranking only when
external provider search is unavailable, fails, or returns insufficient eligible
tracks. Finish with agent_finish only when constraints are satisfied or the
profile is empty.

Never access another user. Never weaken explicit exclusions, song count,
similarity, artist diversity, or exclude-seen constraints. Put only a short,
verifiable action summary in `summary`, and the step rationale in `thought`.
Do not copy historical session constraints into a new recommendation unless the
user asks for another batch or explicitly refers to the prior request.
When the user is dissatisfied with the prior batch, wants something more like
or less like a specific artist/style, or asks to change direction, use
agent_update_request to express that refinement explicitly. Prefer:
- intent="more" and exclude_seen=true for follow-up refinements
- reference_artists for positive anchors like "more like Oasis"
- avoid_artists and exclude_terms for negative anchors like "not Sex Pistols"
- refinement_notes for short contrastive hints such as "less punk" or
  "more melodic"
Do not repeat agent_update_request or an identical tool call. If search_tracks is
available and no provider search has run, call search_tracks before local ranking.
If remaining_steps is 2 or less and no candidate-producing tool has run, call
search_tracks when available; otherwise call rank_candidates/L4__rank_candidates.
"""

# Injected into every tool's parameters so the model must reason before acting.
_THOUGHT_PROPERTY = {
    "thought": {
        "type": "string",
        "description": (
            "One or two sentences of explicit ReAct reasoning: reflect on the "
            "latest observation and justify this action. Required."
        ),
    }
}


_QA_SYSTEM_PROMPT = """\
你是 rateyourDJ，一位懂行、健谈的音乐 DJ 助手，按 ReAct 方式工作：先思考，再决定
这一轮要不要推荐歌曲。无论用户说什么，你都【先给出一段文字回答】，然后由你自己
决定是否附上歌曲。

请始终调用 answer_with_tracks 函数，并包含这些字段：
1. thought：一两句推理——判断用户这条消息属于哪种情况，并说明依据。
2. action：三选一：
   - "explain_only"：用户在追问上面已经推荐过的歌（如“为什么推荐这三首”“第二首
     是什么风格”“有什么理由吗”），或在问一个关于已推荐内容的判断（如“他们是英伦
     摇滚吗”）。此时【只用文字回答/解释，绝不生成新歌】，suggested_tracks 留空。
     可参考提供的 last_recommendations 上下文。
   - "suggest_new"：用户想要歌——无论是直接点歌（“推荐些英伦摇滚”“来点爵士”
     “换一批”），还是问某乐队/风格有什么好歌（“Blur 有什么经典歌曲”）。此时
     answer 里【先用一两句话回应】（例如“Blur 最出圈的几首是…”），然后在
     suggested_tracks 给 2-3 首真实存在、贴合请求的歌，每首带 title、artist 和一句
     中文 reason。若提供了 avoid_tracks，尽量不要重复其中的歌。
   - "answer_only"：纯事实问答或闲聊（如“某乐队哪年成立”“你知道 Oasis 吗”），
     用文字回答即可，无需歌曲。
3. answer：给用户看的文字回答，用中文，自然友好，一般 2-5 句。【任何情况都要先有
   这段回答】。
4. suggested_tracks：仅当 action="suggest_new" 时填写；其余情况留空。

重要：
- 当用户用“为什么/凭什么/理由/解释/是不是/算不算/他们是…吗/这几首/这三首”指向
  【已推荐内容】时，几乎总是 explain_only——不要误当成“再推荐一批”。
- 只提议你确信真实存在的歌曲，用原始录音室版本的标题。
"""

_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "answer_with_tracks",
        "description": (
            "Reply to a music chat turn. Always give a text answer first, then "
            "decide via `action` whether to explain already-recommended tracks, "
            "suggest new ones, or just answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": (
                        "ReAct reasoning: is the user asking about already-"
                        "recommended tracks, asking for new ones, or just "
                        "chatting? Justify the chosen action."
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": ["explain_only", "suggest_new", "answer_only"],
                },
                "answer": {"type": "string"},
                "suggested_tracks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "artist": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["title", "artist"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["thought", "action", "answer"],
            "additionalProperties": False,
        },
    },
}


class DeepSeekProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        request_json: DeepSeekRequest | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek API key is required")
        if not model.strip():
            raise ValueError("DeepSeek model is required")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._request_json = request_json or self._post_json

    @property
    def name(self) -> str:
        return f"deepseek:{self.model}"

    @classmethod
    def from_env(
        cls,
        *,
        required: bool = False,
        model: str | None = None,
        base_url: str | None = None,
    ) -> DeepSeekProvider | None:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            if required:
                raise ValueError("DEEPSEEK_API_KEY is not configured")
            return None
        return cls(
            api_key,
            model=model
            or os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
            base_url=base_url
            or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
        )

    def next_decision(self, turn: AgentTurn) -> AgentDecision:
        tool_name_map: dict[str, str] = {}
        tools = [
            self._deepseek_tool_schema(schema, tool_name_map)
            for schema in turn.tool_schemas
        ]
        tools.extend([_UPDATE_TOOL, _FINISH_TOOL])
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_scope": "current_user",
                            "query": turn.query,
                            "structured_request": turn.request,
                            "session": turn.session,
                            "tool_history": _redact_private_fields(
                                turn.tool_history
                            ),
                            "validation_feedback": turn.validation_feedback,
                            "remaining_steps": turn.remaining_steps,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "tools": tools,
            "tool_choice": "required",
            "thinking": {"type": "disabled"},
            "stream": False,
            "temperature": 0,
        }
        try:
            response = self._request_json(payload)
        except LLMProviderError:
            raise
        except Exception as error:
            raise LLMProviderError(f"DeepSeek request failed: {error}") from error
        return self._parse_decision(response, tool_name_map)

    def answer_question(
        self,
        query: str,
        *,
        history: list[dict[str, Any]] | None = None,
        last_recommendations: list[dict[str, Any]] | None = None,
        avoid_tracks: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, str, list[dict[str, str]]]:
        """Answer a music chat turn and decide (ReAct) whether to suggest tracks.

        Returns ``(answer_text, action, thought, suggested_tracks)`` where
        ``action`` is one of ``answer_only`` / ``explain_only`` / ``suggest_new``
        and each suggested track is ``{"title", "artist", "reason"}``.

        ``history`` is the recent conversation; ``last_recommendations`` is the
        previous turn's recommended tracks (title/artist/reason) so the model can
        EXPLAIN them when the user asks "why these?" instead of inventing new
        songs.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _QA_SYSTEM_PROMPT}
        ]
        if last_recommendations:
            context = [
                {
                    "title": str(item.get("title") or ""),
                    "artist": str(item.get("artist") or ""),
                    "reason": str(item.get("reason") or ""),
                }
                for item in last_recommendations
            ]
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "last_recommendations（上一轮已经推荐给用户的歌，"
                        "当用户追问“为什么/这几首”时请解释这些，不要另生成新歌）："
                        + json.dumps(context, ensure_ascii=False)
                    ),
                }
            )
        for turn in (history or [])[-8:]:
            text = str(turn.get("text") or "").strip()
            if not text:
                continue
            role = "assistant" if turn.get("role") == "dj" else "user"
            messages.append({"role": role, "content": text})
        if avoid_tracks:
            avoid = [
                f"{item.get('title', '')} - {item.get('artist', '')}".strip(" -")
                for item in avoid_tracks
            ]
            avoid = [item for item in avoid if item]
            if avoid:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "avoid_tracks（本次会话已经展示过的歌，若要推荐新歌请"
                            "尽量避免重复这些）：" + json.dumps(avoid, ensure_ascii=False)
                        ),
                    }
                )
        messages.append({"role": "user", "content": query})
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": [_ANSWER_TOOL],
            "tool_choice": {
                "type": "function",
                "function": {"name": "answer_with_tracks"},
            },
            "thinking": {"type": "disabled"},
            "stream": False,
            "temperature": 0.7,
        }
        try:
            response = self._request_json(payload)
        except LLMProviderError:
            raise
        except Exception as error:  # noqa: BLE001
            raise LLMProviderError(
                f"DeepSeek answer request failed: {error}"
            ) from error
        return _parse_answer(response)

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                parsed = json.load(response)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise LLMProviderError(
                f"DeepSeek HTTP {error.code}: {body[:500]}"
            ) from error
        except (TimeoutError, socket.timeout, URLError) as error:
            raise LLMProviderError(
                f"DeepSeek network error: {getattr(error, 'reason', error)}"
            ) from error
        if not isinstance(parsed, dict):
            raise LLMProviderError("DeepSeek returned a non-object response")
        return parsed

    @staticmethod
    def _deepseek_tool_schema(
        schema: dict[str, Any],
        tool_name_map: dict[str, str],
    ) -> dict[str, Any]:
        internal_name = str(schema["name"])
        external_name = internal_name.replace(".", "__")
        tool_name_map[external_name] = internal_name
        parameters = json.loads(json.dumps(schema["parameters"]))
        properties = parameters.get("properties", {})
        if isinstance(properties, dict):
            properties.pop("user_id", None)
            # Inject a required `thought` so the model reasons before acting.
            properties.update(_THOUGHT_PROPERTY)
        required = parameters.get("required")
        if isinstance(required, list):
            required = [name for name in required if name != "user_id"]
        else:
            required = []
        if "thought" not in required:
            required = ["thought", *required]
        parameters["required"] = required
        return {
            "type": "function",
            "function": {
                "name": external_name,
                "description": str(schema["description"]),
                "parameters": parameters,
            },
        }

    @staticmethod
    def _parse_decision(
        response: dict[str, Any],
        tool_name_map: dict[str, str],
    ) -> AgentDecision:
        try:
            choices = response["choices"]
            message = choices[0]["message"]
            tool_calls = message["tool_calls"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMResponseError(
                "DeepSeek response is missing choices[0].message.tool_calls"
            ) from error
        if not isinstance(tool_calls, list) or not tool_calls:
            raise LLMResponseError(
                "DeepSeek must return at least one tool call per agent step"
            )
        selected_call = min(
            tool_calls,
            key=lambda call: (
                0
                if isinstance(call, dict)
                and isinstance(call.get("function"), dict)
                and call["function"].get("name") == "agent_update_request"
                else 1
            ),
        )
        try:
            function = selected_call["function"]
            external_name = str(function["name"])
            arguments = _tool_call_arguments(selected_call)
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise LLMResponseError(
                "DeepSeek returned an invalid function call"
            ) from error
        if not isinstance(arguments, dict):
            raise LLMResponseError("DeepSeek tool arguments must be an object")

        # ReAct: pull the explicit reasoning out so it is recorded on the
        # decision, not passed downstream as a tool argument or request patch.
        thought = ""
        if "thought" in arguments:
            raw_thought = arguments.pop("thought")
            if isinstance(raw_thought, str):
                thought = raw_thought.strip()

        if external_name == "agent_update_request":
            summary = arguments.get("summary") or "update structured request"
            patch = arguments.get("request_patch")
            if not isinstance(patch, dict):
                patch = {
                    key: value
                    for key, value in arguments.items()
                    if key
                    in {
                        "top_k",
                        "max_per_artist",
                        "min_retrieval_score",
                        "preference_terms",
                        "exclude_terms",
                        "reference_artists",
                        "avoid_artists",
                        "refinement_notes",
                        "intent",
                        "exclude_seen",
                    }
                }
            if not isinstance(summary, str) or not patch:
                raise LLMResponseError(
                    "agent_update_request requires a non-empty request patch"
                )
            return AgentDecision(
                kind="update",
                summary=summary,
                request_patch=patch,
                thought=thought,
            )
        if external_name == "agent_finish":
            summary = arguments.get("summary")
            response_text = arguments.get("response_text")
            if not isinstance(summary, str):
                raise LLMResponseError("agent_finish requires summary")
            if response_text is not None and not isinstance(response_text, str):
                raise LLMResponseError(
                    "agent_finish response_text must be text"
                )
            return AgentDecision(
                kind="finish",
                summary=summary,
                response_text=response_text,
                thought=thought,
            )
        try:
            internal_name = tool_name_map[external_name]
        except KeyError as error:
            raise LLMResponseError(
                f"DeepSeek selected unknown tool: {external_name}"
            ) from error
        return AgentDecision(
            kind="tool",
            tool_name=internal_name,
            arguments=arguments,
            summary=f"call {internal_name}",
            thought=thought,
        )


def configured_llm_provider(
    provider_name: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> DeepSeekProvider | None:
    if provider_name == "none":
        return None
    if provider_name == "auto":
        return DeepSeekProvider.from_env(
            required=False,
            model=model,
            base_url=base_url,
        )
    if provider_name == "deepseek":
        return DeepSeekProvider.from_env(
            required=True,
            model=model,
            base_url=base_url,
        )
    raise ValueError("llm_provider must be auto, deepseek, or none")


def _tool_call_arguments(call: dict[str, Any]) -> Any:
    """Return a tool call's arguments, tolerating dict-or-JSON-string forms.

    The OpenAI-compatible contract says `function.arguments` is a JSON string,
    but some DeepSeek responses (or SDK paths) deliver an already-parsed dict.
    Accept both so a valid call is never misreported as a parse failure.
    """
    arguments = call["function"]["arguments"]
    if isinstance(arguments, dict):
        return arguments
    return json.loads(arguments)


def _parse_answer(
    response: dict[str, Any],
) -> tuple[str, str, str, list[dict[str, str]]]:
    """Parse answer_with_tracks → (answer, action, thought, tracks).

    Tolerates a plain content reply (treated as answer_only). Enforces the
    ReAct decision: only ``suggest_new`` keeps suggested tracks; ``explain_only``
    and ``answer_only`` always return an empty track list.
    """
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMResponseError(
            "DeepSeek response is missing choices[0].message"
        ) from error

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        try:
            arguments = _tool_call_arguments(tool_calls[0])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise LLMResponseError(
                "DeepSeek returned invalid answer_with_tracks arguments"
            ) from error
        if not isinstance(arguments, dict):
            raise LLMResponseError("answer_with_tracks arguments must be object")
        answer = str(arguments.get("answer") or "").strip()
        thought = str(arguments.get("thought") or "").strip()
        action = str(arguments.get("action") or "").strip()
        if action not in {"answer_only", "explain_only", "suggest_new"}:
            # Infer: tracks present implies a suggestion, else plain answer.
            action = (
                "suggest_new"
                if arguments.get("suggested_tracks")
                else "answer_only"
            )
        tracks = (
            _clean_suggested_tracks(arguments.get("suggested_tracks"))
            if action == "suggest_new"
            else []
        )
        if answer:
            return answer, action, thought, tracks

    # Fallback: some responses may carry a plain content string instead.
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip(), "answer_only", "", []
    raise LLMResponseError("DeepSeek returned an empty answer")


def _clean_suggested_tracks(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    tracks: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        artist = str(item.get("artist") or "").strip()
        if not title or not artist:
            continue
        tracks.append(
            {
                "title": title,
                "artist": artist,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return tracks


def _redact_private_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_private_fields(item)
            for key, item in value.items()
            if key != "user_id"
        }
    if isinstance(value, list):
        return [_redact_private_fields(item) for item in value]
    return value

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
            "required": ["summary", "request_patch"],
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
                "summary": {"type": "string"},
                "response_text": {"type": "string"},
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
}

_SYSTEM_PROMPT = """\
You are the rateyourDJ recommendation orchestration agent.
Choose exactly one function per turn.
First correct the structured request with agent_update_request when the fallback
parser missed user intent. Then use the provided read-only tools to find
recommendation candidates. If search_tracks is available, prefer it for new music
discovery from external providers. Use local rank_candidates/L4 ranking only when
external provider search is unavailable, fails, or returns insufficient eligible
tracks. Finish with agent_finish only when constraints are satisfied or the
profile is empty.

Never access another user. Never weaken explicit exclusions, song count,
similarity, artist diversity, or exclude-seen constraints. Do not record or reveal
hidden chain-of-thought. Put only a short, verifiable action summary in `summary`.
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


_QA_SYSTEM_PROMPT = """\
你是 rateyourDJ，一位懂行、健谈的音乐 DJ 助手。用户在和你聊天，这一条消息是
一个「问答」而不是让你推荐歌单的请求（例如询问某首歌/某位艺人的来历、风格、
年代、背景，或追问刚才对话里提到的内容）。

请始终通过调用 answer_with_tracks 函数来回应，包含两部分：
1. answer：直接、准确地回答问题，用中文，语气自然友好，像懂音乐的朋友聊天。
   可以适当展开背景、轶事、风格脉络，但保持简洁，一般 2-5 句话。如果用户在追问
   “刚才/上一首/第二首”，请结合提供的对话历史回答。不要捏造事实；不确定就坦诚说。
2. suggested_tracks：可选。当你的回答聊到了具体的乐队、歌曲或风格，并且顺带听几首
   会让对话更连贯时，附上 2-3 首真实存在、与话题高度相关的歌（每首给 title、artist
   和一句中文 reason 说明为什么推荐它、和话题怎么关联）。

什么时候【不要】附歌：纯事实性的追问（如“刚才第二首叫什么”“这首歌哪年发行的”），
或话题与具体可听的歌无关时，suggested_tracks 留空即可。不要硬塞歌。
只提议你确信真实存在的歌曲，用原始录音室版本的标题。
"""

_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "answer_with_tracks",
        "description": (
            "Reply to a conversational music question, optionally suggesting a "
            "few real, relevant tracks the user could listen to next."
        ),
        "parameters": {
            "type": "object",
            "properties": {
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
            "required": ["answer"],
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
    ) -> tuple[str, list[dict[str, str]]]:
        """Answer a conversational music question, optionally suggesting tracks.

        Returns ``(answer_text, suggested_tracks)`` where each suggested track
        is ``{"title", "artist", "reason"}``. ``history`` is an optional list of
        prior turns like ``{"role": "user"|"dj", "text": "..."}`` so the model
        can resolve references such as "刚才第二首叫什么".
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _QA_SYSTEM_PROMPT}
        ]
        for turn in (history or [])[-8:]:
            text = str(turn.get("text") or "").strip()
            if not text:
                continue
            role = "assistant" if turn.get("role") == "dj" else "user"
            messages.append({"role": role, "content": text})
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
        required = parameters.get("required")
        if isinstance(required, list):
            parameters["required"] = [
                name for name in required if name != "user_id"
            ]
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
            arguments = json.loads(function["arguments"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise LLMResponseError(
                "DeepSeek returned an invalid function call"
            ) from error
        if not isinstance(arguments, dict):
            raise LLMResponseError("DeepSeek tool arguments must be an object")

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


def _parse_answer(
    response: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    """Parse the answer_with_tracks tool call; tolerate a plain content reply."""
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMResponseError(
            "DeepSeek response is missing choices[0].message"
        ) from error

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        try:
            arguments = json.loads(tool_calls[0]["function"]["arguments"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise LLMResponseError(
                "DeepSeek returned invalid answer_with_tracks arguments"
            ) from error
        if not isinstance(arguments, dict):
            raise LLMResponseError("answer_with_tracks arguments must be object")
        answer = str(arguments.get("answer") or "").strip()
        tracks = _clean_suggested_tracks(arguments.get("suggested_tracks"))
        if answer:
            return answer, tracks

    # Fallback: some responses may carry a plain content string instead.
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip(), []
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

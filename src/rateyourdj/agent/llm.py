"""OpenAI-compatible chat client with tool calling.

Works with DeepSeek today and with a self-hosted fine-tuned model later
(vLLM / Ollama both expose the same /chat/completions API): only the base URL,
key and model name change.
"""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class LLMError(RuntimeError):
    pass


class ChatModel(Protocol):
    name: str

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the assistant message: {"content": str|None, "tool_calls": [...]}"""


class OpenAICompatibleChat:
    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 60.0, retries: int = 2, temperature: float = 0.2,
                 transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.temperature = temperature
        self._post = transport or self._http_post
        self.name = f"{'deepseek' if 'deepseek' in self.base_url else 'openai-compatible'}:{model}"

    @classmethod
    def from_env(cls) -> "OpenAICompatibleChat | None":
        """AGENT_LLM_* wins (self-hosted model); otherwise DEEPSEEK_*; None if no key."""
        key = os.getenv("AGENT_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
        if not key:
            return None
        return cls(key,
                   model=os.getenv("AGENT_LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL,
                   base_url=os.getenv("AGENT_LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"model": self.model, "messages": messages, "tools": tools,
                   "tool_choice": "auto", "temperature": self.temperature}
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self._post(payload)
                choice = (response.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                return {"content": message.get("content"), "tool_calls": message.get("tool_calls") or []}
            except LLMError as error:
                last = error
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError(str(last))

    def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(f"{self.base_url}/chat/completions",
                          data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Accept": "application/json",
                                   "Authorization": f"Bearer {self.api_key}"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                parsed = json.load(response)
        except HTTPError as error:
            raise LLMError(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:300]}") from error
        except (TimeoutError, socket.timeout, URLError) as error:
            raise LLMError(f"network error: {getattr(error, 'reason', error)}") from error
        if not isinstance(parsed, dict):
            raise LLMError("non-object response")
        return parsed


def tool_call_arguments(call: dict[str, Any]) -> dict[str, Any]:
    """Tool-call arguments as a dict; tolerates the dict-or-JSON-string ambiguity seen with DeepSeek."""
    arguments = (call.get("function") or {}).get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if not arguments:
        return {}
    value = json.loads(arguments)
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be a JSON object")
    return value


class ScriptedChat:
    """Test double: replays scripted assistant messages and records what it was sent."""

    def __init__(self, script: list[dict[str, Any]], name: str = "scripted") -> None:
        self.script = list(script)
        self.name = name
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(list(messages))
        if not self.script:
            raise LLMError("script exhausted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

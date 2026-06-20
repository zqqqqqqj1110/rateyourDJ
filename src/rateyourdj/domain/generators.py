"""Track generators that propose candidate songs for discovery.

`DeepSeekTrackGenerator` asks the DeepSeek chat API to act as a music DJ and
return a structured list of songs from the user's taste profile. It reuses the
same OpenAI-compatible `/chat/completions` transport as the L6 provider.

`TasteSeedTrackGenerator` is a deterministic, network-free fallback used when no
API key is configured and in tests. It simply echoes the user's known
collection / preferred artists as candidates so the discovery + grounding
pipeline can run end-to-end without a model.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .discovery import GeneratedCandidate


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_SECONDS = 45

DeepSeekRequest = Callable[[dict[str, Any]], dict[str, Any]]


_GENERATOR_SYSTEM_PROMPT = """\
You are rateyourDJ, an expert music DJ. Given a listener's taste profile and a
listening request, propose real, existing songs that fit. Use your music
knowledge to pick tracks the listener is likely to enjoy.

Rules:
- Recommend AT MOST 3 songs. Curate a tight, high-quality set — never more
  than 3, even if more candidates are requested. Fewer is fine if you are not
  confident about a pick.
- Only propose songs you are confident actually exist, with the correct artist.
- Prefer the original studio recording's title (avoid "- Live", "- Remaster"
  suffixes unless explicitly requested).
- Do not propose songs by any artist in the exclude list.
- Cover a range of artists; do not fill the list with one artist.
- Return your answer by calling the propose_tracks function exactly once.

For EACH track, write a detailed `reason` IN CHINESE (用中文写, 2-3 句话) that
explains why you picked it. Do NOT just say it matches the listener's profile.
Draw on whichever angles are most convincing for that specific song:
- 相似度: 和哪位艺人 / 哪首歌在曲风、编曲、人声或情绪上接近
- 历史 / 背景: 乐队或这首歌的来历、所属年代、所属流派浪潮、影响力
- 场景 / 氛围: 适合什么心情或场合来听
- 音乐特征: 吉他/合成器/节奏/制作上的具体亮点
Make the reason concrete and specific to the song — mention real artist names,
eras, or musical details rather than generic praise.
"""

_PROPOSE_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_tracks",
        "description": (
            "Return the proposed candidate songs for grounding and ranking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tracks": {
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
                }
            },
            "required": ["tracks"],
            "additionalProperties": False,
        },
    },
}


class TrackGeneratorError(RuntimeError):
    """Raised when a generator cannot produce candidates."""


class DeepSeekTrackGenerator:
    """Propose candidate songs using the DeepSeek chat API."""

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
        return f"deepseek-generator:{self.model}"

    @classmethod
    def from_env(
        cls,
        *,
        required: bool = False,
        model: str | None = None,
        base_url: str | None = None,
        request_json: DeepSeekRequest | None = None,
    ) -> "DeepSeekTrackGenerator | None":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            if required:
                raise ValueError("DEEPSEEK_API_KEY is not configured")
            return None
        return cls(
            api_key,
            model=model or os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
            base_url=base_url
            or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
            request_json=request_json,
        )

    def generate(
        self,
        *,
        intent: str,
        user_taste: dict[str, Any],
        count: int,
        exclude_artists: list[str],
    ) -> list[GeneratedCandidate]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _GENERATOR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": intent,
                            "taste_profile": user_taste,
                            "exclude_artists": exclude_artists,
                            "desired_count": count,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "tools": [_PROPOSE_TOOL],
            "tool_choice": {
                "type": "function",
                "function": {"name": "propose_tracks"},
            },
            "thinking": {"type": "disabled"},
            "stream": False,
            "temperature": 0.7,
        }
        try:
            response = self._request_json(payload)
        except TrackGeneratorError:
            raise
        except Exception as error:  # noqa: BLE001
            raise TrackGeneratorError(
                f"DeepSeek generation failed: {error}"
            ) from error
        return _parse_candidates(response)

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
            raise TrackGeneratorError(
                f"DeepSeek HTTP {error.code}: {body[:500]}"
            ) from error
        except (TimeoutError, socket.timeout, URLError) as error:
            raise TrackGeneratorError(
                f"DeepSeek network error: {getattr(error, 'reason', error)}"
            ) from error
        if not isinstance(parsed, dict):
            raise TrackGeneratorError("DeepSeek returned a non-object response")
        return parsed


class TasteSeedTrackGenerator:
    """Deterministic fallback: propose songs straight from the user's taste.

    This generator does not invent new songs; it surfaces the user's own
    preferred artists / collection so the discovery + grounding pipeline runs
    without a model or network. Useful for tests and for degrading gracefully
    when no DEEPSEEK_API_KEY is configured.
    """

    @property
    def name(self) -> str:
        return "taste-seed-generator"

    def generate(
        self,
        *,
        intent: str,
        user_taste: dict[str, Any],
        count: int,
        exclude_artists: list[str],
    ) -> list[GeneratedCandidate]:
        seeds = user_taste.get("seed_tracks")
        candidates: list[GeneratedCandidate] = []
        if isinstance(seeds, list):
            for seed in seeds:
                if not isinstance(seed, dict):
                    continue
                title = str(seed.get("title") or "").strip()
                artist = str(seed.get("artist") or "").strip()
                if title and artist:
                    candidates.append(
                        GeneratedCandidate(
                            title=title,
                            artist=artist,
                            reason="from your listening profile",
                        )
                    )
        return candidates[: max(count, 0)]


def _parse_candidates(response: dict[str, Any]) -> list[GeneratedCandidate]:
    try:
        message = response["choices"][0]["message"]
        tool_calls = message["tool_calls"]
    except (KeyError, IndexError, TypeError) as error:
        raise TrackGeneratorError(
            "DeepSeek response is missing choices[0].message.tool_calls"
        ) from error
    if not isinstance(tool_calls, list) or not tool_calls:
        raise TrackGeneratorError("DeepSeek returned no tool calls")
    try:
        arguments = json.loads(tool_calls[0]["function"]["arguments"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise TrackGeneratorError(
            "DeepSeek returned invalid propose_tracks arguments"
        ) from error
    tracks = arguments.get("tracks") if isinstance(arguments, dict) else None
    if not isinstance(tracks, list):
        raise TrackGeneratorError("propose_tracks did not return a track list")
    candidates: list[GeneratedCandidate] = []
    for item in tracks:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        artist = str(item.get("artist") or "").strip()
        if not title or not artist:
            continue
        candidates.append(
            GeneratedCandidate(
                title=title,
                artist=artist,
                reason=str(item.get("reason") or "").strip(),
            )
        )
    return candidates

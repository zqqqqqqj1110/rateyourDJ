"""Cached, rate-limited JSON HTTP client (stdlib only).

* Every successful response is cached on disk under ``cache_dir`` keyed by
  method + URL + body, so re-running a pipeline step never re-downloads and an
  interrupted run resumes where it stopped.
* Requests to the same host are spaced by ``min_interval`` seconds, and
  ListenBrainz ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset-In`` headers are
  honoured.
* 429 / 5xx / network errors are retried with backoff (``Retry-After`` wins).

Tests inject ``transport`` to run fully offline.
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

RETRYABLE = {429, 500, 502, 503, 504}


@dataclass(slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


Transport = Callable[[str, str, dict[str, str], bytes | None, float], HttpResponse]


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str) -> None:
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")
        self.status = status
        self.url = url


def urllib_transport(method: str, url: str, headers: dict[str, str],
                     body: bytes | None, timeout: float) -> HttpResponse:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, {k.lower(): v for k, v in response.headers.items()},
                                response.read())
    except HTTPError as error:
        return HttpResponse(error.code, {k.lower(): v for k, v in (error.headers or {}).items()},
                            error.read() or b"")


class CachedJsonClient:
    def __init__(
        self,
        cache_dir: str | Path,
        *,
        headers: dict[str, str] | None = None,
        min_interval: float = 0.0,
        max_attempts: int = 5,
        timeout: float = 30.0,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.headers = {"Accept": "application/json", **(headers or {})}
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.transport = transport or urllib_transport
        self.sleep = sleep
        self.clock = clock
        self._next_allowed: dict[str, float] = {}
        self.stats = {"cache_hits": 0, "requests": 0, "retries": 0}

    # ------------------------------------------------------------------ cache
    def _cache_path(self, method: str, url: str, body: bytes | None) -> Path:
        key = hashlib.sha1(method.encode() + b" " + url.encode() + b"\n" + (body or b"")).hexdigest()
        return self.cache_dir / key[:2] / f"{key}.json"

    def _read_cache(self, path: Path) -> Any:
        if path.is_file():
            try:
                return json.loads(path.read_text("utf-8"))["data"]
            except (ValueError, KeyError):
                return None
        return None

    def _write_cache(self, path: Path, method: str, url: str, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"method": method, "url": url, "fetched_at": time.time(),
                                   "data": data}, ensure_ascii=False), "utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------ http
    def _wait_for_host(self, host: str) -> None:
        now = self.clock()
        ready = self._next_allowed.get(host, 0.0)
        if ready > now:
            self.sleep(ready - now)
        self._next_allowed[host] = max(ready, self.clock()) + self.min_interval

    def _note_rate_headers(self, host: str, headers: dict[str, str]) -> None:
        remaining = headers.get("x-ratelimit-remaining")
        reset_in = headers.get("x-ratelimit-reset-in")
        if remaining is not None and reset_in is not None:
            try:
                if int(remaining) <= 1:
                    self._next_allowed[host] = self.clock() + float(reset_in) + 0.5
            except ValueError:
                pass

    def request(self, method: str, url: str, *, json_body: Any = None,
                use_cache: bool = True, allow_404: bool = False) -> Any:
        body = None
        headers = dict(self.headers)
        if json_body is not None:
            body = json.dumps(json_body, sort_keys=True, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        path = self._cache_path(method, url, body)
        if use_cache:
            cached = self._read_cache(path)
            if cached is not None:
                self.stats["cache_hits"] += 1
                return cached

        host = urlsplit(url).netloc
        last: str = ""
        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_host(host)
            self.stats["requests"] += 1
            try:
                response = self.transport(method, url, headers, body, self.timeout)
            except (TimeoutError, socket.timeout, URLError, ConnectionError) as error:
                last = f"network error: {error}"
                self.stats["retries"] += 1
                self.sleep(min(60.0, 2.0 ** attempt))
                continue
            self._note_rate_headers(host, response.headers)
            if response.status == 404 and allow_404:
                data: Any = {"_not_found": True}
                if use_cache:
                    self._write_cache(path, method, url, data)
                return data
            if response.status in RETRYABLE:
                last = f"HTTP {response.status}"
                self.stats["retries"] += 1
                retry_after = response.headers.get("retry-after") or response.headers.get(
                    "x-ratelimit-reset-in")
                try:
                    delay = float(retry_after) + 0.5 if retry_after else 2.0 ** attempt
                except ValueError:
                    delay = 2.0 ** attempt
                self.sleep(min(120.0, delay))
                continue
            if response.status >= 400:
                raise HttpError(response.status, url, response.body.decode("utf-8", "replace"))
            text = response.body.decode("utf-8") if response.body else "null"
            data = json.loads(text) if text.strip() else None
            if use_cache and data is not None:
                self._write_cache(path, method, url, data)
            return data
        raise RuntimeError(f"request failed after {self.max_attempts} attempts: {url} ({last})")

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, json_body: Any, **kwargs: Any) -> Any:
        return self.request("POST", url, json_body=json_body, **kwargs)

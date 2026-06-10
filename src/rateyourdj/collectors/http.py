from __future__ import annotations

import json
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_ATTEMPTS = 3


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        request = Request(
            url,
            headers={"Accept": "application/json", **(headers or {})},
            data=data,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504}:
                body = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"HTTP {error.code} from {url}: {body[:500]}"
                ) from error
            last_error = error
        except (TimeoutError, socket.timeout, URLError) as error:
            last_error = error

        if attempt < max_attempts:
            time.sleep(attempt)

    reason = getattr(last_error, "reason", last_error)
    raise RuntimeError(
        f"Request failed after {max_attempts} attempts: {url}: {reason}"
    ) from last_error

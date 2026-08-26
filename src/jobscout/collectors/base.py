"""Collector protocol and resilient HTTP helper."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

import httpx

from jobscout.config import SourceConfig
from jobscout.domain import CollectedJob


class Collector(Protocol):
    def collect(self, source: SourceConfig, client: httpx.Client) -> list[CollectedJob]: ...


def _get_content(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None,
    attempts: int,
    max_response_seconds: float = 30,
) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            started = time.monotonic()
            with client.stream("GET", url, params=params) as response:
                if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < attempts:
                    retry_after = response.headers.get("Retry-After", "0")
                    try:
                        delay = min(max(float(retry_after), 0.0), 5.0)
                    except ValueError:
                        delay = 0.0
                    if delay:
                        time.sleep(delay)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > max_response_seconds:
                        raise httpx.ReadTimeout(
                            f"response exceeded {max_response_seconds:g} seconds",
                            request=response.request,
                        )
                    chunks.append(chunk)
                return b"".join(chunks), response.encoding or "utf-8"
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
    assert last_error is not None
    raise last_error


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    attempts: int = 2,
) -> Any:
    content, encoding = _get_content(
        client,
        url,
        params=params,
        attempts=attempts,
    )
    return json.loads(content.decode(encoding))


def get_text(client: httpx.Client, url: str, *, attempts: int = 2) -> str:
    content, encoding = _get_content(
        client,
        url,
        params=None,
        attempts=attempts,
    )
    return content.decode(encoding, errors="replace")

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.utils.url import ensure_public_url, resolve_redirect

USER_AGENT = "GlobalAIFrontierBot/0.1 (+cloud RSS reader; contact: operator)"


class ResponseTooLargeError(ValueError):
    pass


class SafeHttpClient:
    def __init__(
        self,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[..., Awaitable[list[tuple[object, ...]]]] | None = None,
        max_redirects: int = 3,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10))
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "application/rss+xml, application/atom+xml, "
                    "application/xml, text/xml;q=0.9, */*;q=0.2"
                ),
            },
            transport=transport,
        )
        self._resolver = resolver
        self._max_redirects = max_redirects
        self._max_response_bytes = max_response_bytes

    async def __aenter__(self) -> "SafeHttpClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._get_with_redirects(url)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    raise
                last_error = exc
            if attempt < 2:
                await asyncio.sleep((2**attempt) * 0.25 + random.uniform(0, 0.2))
        if last_error is None:
            raise RuntimeError("request failed without an error")
        raise last_error

    async def _get_with_redirects(self, url: str) -> str:
        current = await ensure_public_url(url, self._resolver)
        for redirect_count in range(self._max_redirects + 1):
            response = await self._client.get(current)
            if response.is_redirect:
                if redirect_count >= self._max_redirects:
                    raise httpx.TooManyRedirects(
                        "redirect limit exceeded", request=response.request
                    )
                location = response.headers.get("location")
                if not location:
                    raise httpx.HTTPStatusError(
                        "redirect has no location", request=response.request, response=response
                    )
                current = await ensure_public_url(
                    resolve_redirect(current, location), self._resolver
                )
                continue
            response.raise_for_status()
            if len(response.content) > self._max_response_bytes:
                raise ResponseTooLargeError("response exceeded configured size limit")
            return response.text
        raise httpx.TooManyRedirects("redirect limit exceeded")

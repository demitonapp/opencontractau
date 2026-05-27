"""
Rate-limited HTTP client with robots.txt compliance.

Per the OpenContractAU contributing guide:
- Identifies as OpenContractAU/0.x with a link to the project
- Rate-limits to one request per three seconds (configurable)
- Respects robots.txt for the target host
- Honours takedown requests (see CONTRIBUTING.md)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

OPENCONTRACTAU_UA = (
    "OpenContractAU/0.1 (+https://github.com/demitonapp/opencontractau)"
)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "OpenContractAU/0.1 (+https://github.com/demitonapp/opencontractau)"
)


class RobotsCache:
    """In-memory robots.txt cache. One RobotFileParser per host."""

    def __init__(self) -> None:
        self._parsers: dict[str, RobotFileParser] = {}

    def _host_key(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def can_fetch(self, url: str, user_agent: str) -> bool:
        host = self._host_key(url)
        if host not in self._parsers:
            robots_url = f"{host}/robots.txt"
            parser = RobotFileParser(robots_url)
            try:
                parser.read()
            except Exception:
                logger.debug("robots.txt unreachable for %s, assuming allowed", host)
                parser.allow_all = True
            self._parsers[host] = parser
        return self._parsers[host].can_fetch(user_agent, url)


_robots_cache = RobotsCache()


class RateLimitedClient:
    """
    Async HTTP client with per-instance rate limiting and robots.txt compliance.

    Usage::

        async with RateLimitedClient(min_interval_s=3.0) as client:
            response = await client.get("https://example.com/data.csv")
    """

    def __init__(
        self,
        base_url: str = "",
        min_interval_s: float = 3.0,
        user_agent: str = OPENCONTRACTAU_UA,
        extra_headers: dict[str, str] | None = None,
        timeout_s: float = 60.0,
        check_robots: bool = True,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.user_agent = user_agent
        self.check_robots = check_robots
        self._last_request_at: float = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json, text/csv, application/octet-stream, */*",
                "Accept-Language": "en-AU,en;q=0.9",
                **(extra_headers or {}),
            },
            follow_redirects=True,
        )

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            if elapsed < self.min_interval_s:
                await asyncio.sleep(self.min_interval_s - elapsed)
            self._last_request_at = time.monotonic()

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.check_robots and not _robots_cache.can_fetch(url, self.user_agent):
            raise PermissionError(f"robots.txt disallows {url} for {self.user_agent}")

        await self._throttle()
        logger.debug("GET %s", url)

        try:
            response = await self._client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            logger.warning("HTTP %s on %s", exc.response.status_code, url)
            raise

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.get(url, **kwargs)
        return response.json()

    async def get_text(self, url: str, **kwargs: Any) -> str:
        response = await self.get(url, **kwargs)
        return response.text

    async def get_bytes(self, url: str, **kwargs: Any) -> bytes:
        response = await self.get(url, **kwargs)
        return response.content

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RateLimitedClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

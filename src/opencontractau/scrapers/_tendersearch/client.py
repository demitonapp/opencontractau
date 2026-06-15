"""
Cloudflare-aware async HTTP client for TenderSearch portals.

Uses curl_cffi for Chrome TLS fingerprint impersonation. Without TLS
impersonation Cloudflare returns the "Attention Required!" challenge
page (verified against tenders.vic.gov.au, contracts.sa.gov.au, and
qtenders.hpw.qld.gov.au).

Bundled with rate limiting (3s default) and per-instance session
cookies so that any nonce/state the portal hands out persists across
calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from curl_cffi import requests as curl_requests

logger = logging.getLogger(__name__)

CHROME_IMPERSONATION = "chrome120"


class TenderSearchClient:
    """Rate-limited curl_cffi session for TenderSearch portals."""

    def __init__(
        self,
        base_url: str,
        min_interval_s: float = 3.0,
        impersonate: str = CHROME_IMPERSONATION,
        timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = min_interval_s
        self.impersonate = impersonate
        self.timeout_s = timeout_s
        self._session = curl_requests.Session()
        self._last_request_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            if elapsed < self.min_interval_s:
                await asyncio.sleep(self.min_interval_s - elapsed)
            self._last_request_at = time.monotonic()

    async def get_text(self, path: str, **params: Any) -> str:
        await self._throttle()
        url = self.base_url + path
        logger.debug("GET %s params=%s", url, params)

        loop = asyncio.get_event_loop()

        def _do_request() -> str:
            response = self._session.get(
                url,
                params=params or None,
                impersonate=self.impersonate,
                timeout=self.timeout_s,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response.text

        return await loop.run_in_executor(None, _do_request)

    async def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    async def __aenter__(self) -> "TenderSearchClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

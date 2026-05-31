"""
Playwright-based rate-limited async browser client for JavaScript-rendered sites.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from playwright.async_api import async_playwright, Browser, Page

from opencontractau.scrapers.base import BROWSER_UA, _robots_cache

logger = logging.getLogger(__name__)


class PlaywrightClient:
    """
    Rate-limited Playwright client for JavaScript-rendered sites.
    Respects robots.txt and throttles requests.
    """

    def __init__(
        self,
        min_interval_s: float = 3.0,
        user_agent: str = BROWSER_UA,
        check_robots: bool = True,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.user_agent = user_agent
        self.check_robots = check_robots
        self._last_request_at: float = 0.0
        self._lock = asyncio.Lock()
        self._playwright = None
        self._browser: Browser | None = None
        self._context = None

    async def __aenter__(self) -> PlaywrightClient:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 720},
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            if elapsed < self.min_interval_s:
                await asyncio.sleep(self.min_interval_s - elapsed)
            self._last_request_at = time.monotonic()

    async def get_html(
        self,
        url: str,
        wait_for_selector: str | None = None,
        timeout_ms: float = 30000,
    ) -> str:
        if self.check_robots and not _robots_cache.can_fetch(url, self.user_agent):
            raise PermissionError(f"robots.txt disallows {url} for {self.user_agent}")

        await self._throttle()
        logger.debug("Playwright GET %s", url)

        page: Page = await self._context.new_page()
        try:
            response = await page.goto(url, timeout=timeout_ms)
            if response is None or response.status >= 400:
                status = response.status if response else "No Response"
                logger.warning("Playwright HTTP %s on %s", status, url)
                raise RuntimeError(f"Failed to fetch {url}: HTTP {status}")

            if wait_for_selector:
                await page.wait_for_selector(wait_for_selector, timeout=timeout_ms)

            return await page.content()
        finally:
            await page.close()

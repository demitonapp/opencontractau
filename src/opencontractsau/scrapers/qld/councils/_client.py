"""
curl_cffi-based async HTTP client for QLD council websites.

Many QLD council sites (Logan, Moreton Bay, Scenic Rim, Ipswich) return
HTTP 403 to standard user-agent strings. curl_cffi with Chrome TLS
impersonation bypasses these bot-detection checks.

Rate limited to 3s between requests (OpenContractsAU contributing policy).
"""

from __future__ import annotations

import asyncio
import logging
import time
from html.parser import HTMLParser
from typing import Any

from curl_cffi import requests as curl_requests

logger = logging.getLogger(__name__)

CHROME_IMPERSONATION = "chrome120"


class CouncilClient:
    """Rate-limited curl_cffi session for QLD council portals."""

    def __init__(
        self,
        base_url: str,
        min_interval_s: float = 3.0,
        timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = min_interval_s
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

    async def get_text(self, path: str = "", **params: Any) -> str:
        await self._throttle()
        url = (self.base_url + path) if path else self.base_url
        logger.debug("CouncilClient GET %s", url)
        loop = asyncio.get_event_loop()

        def _do() -> str:
            resp = self._session.get(
                url,
                params=params or None,
                impersonate=CHROME_IMPERSONATION,
                timeout=self.timeout_s,
                allow_redirects=True,
            )
            resp.raise_for_status()
            return resp.text

        return await loop.run_in_executor(None, _do)

    async def get_bytes(self, path: str = "", **params: Any) -> bytes:
        await self._throttle()
        url = (self.base_url + path) if path else self.base_url
        logger.debug("CouncilClient GET bytes %s", url)
        loop = asyncio.get_event_loop()

        def _do() -> bytes:
            resp = self._session.get(
                url,
                params=params or None,
                impersonate=CHROME_IMPERSONATION,
                timeout=self.timeout_s,
                allow_redirects=True,
            )
            resp.raise_for_status()
            return resp.content

        return await loop.run_in_executor(None, _do)

    async def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    async def __aenter__(self) -> "CouncilClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# Generic HTML table parser (used by all council scrapers)
# ---------------------------------------------------------------------------


class _TableParser(HTMLParser):
    """Extract all tables from an HTML page as list[list[list[str]]]."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif tag in ("tr",) and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False
            self._current_table = []
        elif tag == "tr" and self._in_table:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._in_row = False
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            cell_text = " ".join(self._current_cell).strip()
            self._current_row.append(cell_text)
            self._in_cell = False
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell.append(stripped)


def extract_tables(html: str) -> list[list[list[str]]]:
    """Return all HTML tables as list[table[row[cell]]]."""
    parser = _TableParser()
    parser.feed(html)
    return parser.tables


def table_to_dicts(table: list[list[str]]) -> list[dict[str, str]]:
    """Convert a table (list of rows) into a list of dicts using first row as headers."""
    if not table or len(table) < 2:
        return []
    headers = [h.strip() for h in table[0]]
    result = []
    for row in table[1:]:
        # Pad short rows
        padded = row + [""] * (len(headers) - len(row))
        result.append(dict(zip(headers, padded)))
    return result

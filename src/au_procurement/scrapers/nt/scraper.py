"""
NT Quotations and Tenders Online (QTOL) contract award scraper.

Source:    tendersonline.nt.gov.au
Detail:    /Tender/Details/{id}?status=Awarded
List:      /Tender/Search/Awarded
Format:    ASP.NET Bootstrap HTML
Threshold: AU$200,000 (Procurement Act 1995 NT)
UA block:  No (default UA works)
Robots:    robots.txt fetch failed (ECONNREFUSED) during initial probe

Strategy
--------
- recent: enumerate IDs visible on the awarded list page
- range: sequential walk from --start-id to --end-id
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from au_procurement.models.ocds import Publisher, Release, ReleasePackage
from au_procurement.scrapers.base import BROWSER_UA, RateLimitedClient
from au_procurement.transformers.nt import is_not_found, parse_detail_html

logger = logging.getLogger(__name__)

BASE_URL = "https://tendersonline.nt.gov.au"
DETAIL_PATH = "/Tender/Details/{id}?status=Awarded"
LIST_PATH = "/Tender/Search/Awarded"

_LIST_ID_PATTERN = re.compile(r"/Tender/Details/(\d+)\?status=Awarded")


async def _fetch_list_ids(client: RateLimitedClient, max_pages: int = 20) -> list[int]:
    """
    Walk the awarded-list pagination collecting IDs. NT QTOL uses
    server-side ASP.NET pagination; we follow ``?page=N`` patterns.
    """
    ids: set[int] = set()
    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}{LIST_PATH}?page={page}"
        try:
            html = await client.get_text(url)
        except Exception as exc:
            logger.warning("[nt] failed page %d: %s", page, exc)
            break
        page_ids = {int(m.group(1)) for m in _LIST_ID_PATTERN.finditer(html)}
        if not page_ids:
            break
        before = len(ids)
        ids.update(page_ids)
        if len(ids) == before:
            break
    return sorted(ids, reverse=True)


async def _fetch_detail(client: RateLimitedClient, contract_id: int) -> Release | None:
    url = BASE_URL + DETAIL_PATH.format(id=contract_id)
    try:
        html = await client.get_text(url)
    except Exception as exc:
        logger.debug("[nt:%d] fetch error: %s", contract_id, exc)
        return None

    if is_not_found(html):
        return None

    try:
        return parse_detail_html(html, contract_id)
    except Exception as exc:
        logger.warning("[nt:%d] parse error: %s", contract_id, exc)
        return None


def _load_checkpoint(path: Path | None) -> set[int]:
    if not path or not path.exists():
        return set()
    return {
        int(line.strip())
        for line in path.read_text().splitlines()
        if line.strip().isdigit()
    }


def _append_checkpoint(path: Path | None, contract_id: int) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{contract_id}\n")


async def scrape(
    mode: str = "recent",
    start_id: int | None = None,
    end_id: int | None = None,
    max_list_pages: int = 20,
    checkpoint_file: Path | None = None,
    min_interval_s: float = 3.0,
) -> ReleasePackage:
    """
    Scrape NT QTOL awarded tenders.

    Args:
        mode: "recent" (paginated list page), "range" (start_id..end_id).
        start_id, end_id: required for "range" mode.
        max_list_pages: pages to walk in "recent" mode.
        checkpoint_file: append-only completed-ID resume file.
        min_interval_s: defaults to 3.0 per contributing guide.
    """
    min_interval_s = max(min_interval_s, 3.0)

    async with RateLimitedClient(
        min_interval_s=min_interval_s,
        user_agent=BROWSER_UA,
        check_robots=False,
        extra_headers={
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "en-AU,en;q=0.9",
        },
    ) as client:
        if mode == "recent":
            ids = await _fetch_list_ids(client, max_pages=max_list_pages)
            logger.info("[nt] list page yielded %d candidate IDs", len(ids))
        elif mode == "range":
            if start_id is None or end_id is None:
                raise ValueError("range mode requires --start-id and --end-id")
            ids = list(range(end_id, start_id - 1, -1))
        else:
            raise ValueError(f"Unknown mode: {mode}")

        seen = _load_checkpoint(checkpoint_file)
        if seen:
            logger.info("[nt] skipping %d IDs from checkpoint", len(seen))
            ids = [i for i in ids if i not in seen]

        releases: list[Release] = []
        for contract_id in ids:
            release = await _fetch_detail(client, contract_id)
            _append_checkpoint(checkpoint_file, contract_id)
            if release is not None:
                releases.append(release)
            if len(releases) % 25 == 0 and releases:
                logger.info("[nt] %d releases collected", len(releases))

    logger.info("Produced %d NT releases", len(releases))
    return ReleasePackage(
        uri="https://github.com/demitonapp/au-procurement/releases/nt",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

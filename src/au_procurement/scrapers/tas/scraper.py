"""
Tasmania eTenders contract award scraper.

Source:    tenders.tas.gov.au
Detail:    /ContractAwarded/Details/{id}  (sequential integer IDs)
List:      /ContractAwarded/List/DateAwarded  (last 30 days)
Format:    ASP.NET MVC HTML
Threshold: AU$50,000 (Financial Management Act 2016 TAS)
UA block:  Yes - 500 on default WebFetch UA, fine with Chrome UA
Robots:    robots.txt could not be retrieved during initial probe

Strategy
--------
- recent: enumerate IDs visible on /ContractAwarded/List/DateAwarded
- range: sequential walk from --start-id to --end-id
- backfill: walk from 1 to a probed current max

A checkpoint file can be passed to resume long backfill runs.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from au_procurement.models.ocds import Publisher, Release, ReleasePackage
from au_procurement.scrapers.base import BROWSER_UA, RateLimitedClient
from au_procurement.transformers.tas import is_not_found, parse_detail_html

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tenders.tas.gov.au"
DETAIL_PATH = "/ContractAwarded/Details/{id}"
LIST_PATH = "/ContractAwarded/List/DateAwarded"

_LIST_ID_PATTERN = re.compile(r"/ContractAwarded/Details/(\d+)")


async def _fetch_list_ids(client: RateLimitedClient) -> list[int]:
    try:
        html = await client.get_text(BASE_URL + LIST_PATH)
    except Exception as exc:
        logger.warning("Failed to fetch TAS list page: %s", exc)
        return []
    ids = sorted({int(m.group(1)) for m in _LIST_ID_PATTERN.finditer(html)}, reverse=True)
    return ids


async def _fetch_detail(
    client: RateLimitedClient,
    contract_id: int,
) -> Release | None:
    url = BASE_URL + DETAIL_PATH.format(id=contract_id)
    try:
        html = await client.get_text(url)
    except Exception as exc:
        logger.debug("[tas:%d] fetch error: %s", contract_id, exc)
        return None

    if is_not_found(html):
        return None

    try:
        return parse_detail_html(html, contract_id)
    except Exception as exc:
        logger.warning("[tas:%d] parse error: %s", contract_id, exc)
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
    checkpoint_file: Path | None = None,
    min_interval_s: float = 3.0,
) -> ReleasePackage:
    """
    Scrape Tasmania eTenders contract awards.

    Args:
        mode: "recent" (list page IDs), "range" (start_id..end_id),
              or "backfill" (1..end_id, with checkpoint support).
        start_id, end_id: required for "range" and "backfill" modes.
        checkpoint_file: resume support for long backfills; append-only
            file of completed IDs.
        min_interval_s: seconds between requests. Defaults to 3 per
            the OpenContractAU contributing guide.
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
            ids = await _fetch_list_ids(client)
            logger.info("TAS list page yielded %d candidate IDs", len(ids))
        elif mode == "range":
            if start_id is None or end_id is None:
                raise ValueError("range mode requires --start-id and --end-id")
            ids = list(range(end_id, start_id - 1, -1))
        elif mode == "backfill":
            if end_id is None:
                raise ValueError("backfill mode requires --end-id (current max ID)")
            ids = list(range(end_id, (start_id or 1) - 1, -1))
        else:
            raise ValueError(f"Unknown mode: {mode}")

        seen = _load_checkpoint(checkpoint_file)
        if seen:
            logger.info("Skipping %d IDs from checkpoint", len(seen))
            ids = [i for i in ids if i not in seen]

        releases: list[Release] = []
        consecutive_misses = 0

        for contract_id in ids:
            release = await _fetch_detail(client, contract_id)
            _append_checkpoint(checkpoint_file, contract_id)

            if release is None:
                consecutive_misses += 1
                if mode == "recent" and consecutive_misses > 5:
                    logger.info("Many consecutive misses on recent list - continuing")
                continue

            consecutive_misses = 0
            releases.append(release)
            if len(releases) % 25 == 0:
                logger.info("TAS: %d releases collected", len(releases))

    logger.info("Produced %d TAS releases", len(releases))
    return ReleasePackage(
        uri="https://github.com/demitonapp/opencontractau/releases/tas",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

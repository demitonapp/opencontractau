"""
Victoria recently-awarded contracts scraper.

Source:    tenders.vic.gov.au
List:      /contract/search?preset=recentlyAwarded&page={N}
Detail:    /contract/view?id={id}
Format:    TenderSearch Java HTML (see ../_tendersearch/)
Threshold: AU$100,000 (Standing Directions 2018 VIC)
UA block:  Cloudflare - requires Chrome TLS fingerprint (curl_cffi)
Robots:    Crawl-delay: 2 on buyingfor.vic.gov.au; tenders.vic.gov.au
           does not block /contract/ paths

Pagination
----------
- 25 contracts per page; ``?page=N`` for additional pages
- VIC truncates search results at large counts; for full backfill,
  chunk by date range (out of scope for v0.1.0)
"""

from __future__ import annotations

import logging
from datetime import datetime

from au_procurement.models.ocds import Publisher, Release, ReleasePackage
from au_procurement.scrapers._tendersearch.client import TenderSearchClient
from au_procurement.scrapers._tendersearch.parser import (
    parse_contract_ids,
    parse_detail_html,
)
from au_procurement.scrapers._tendersearch.transformer import detail_to_release

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tenders.vic.gov.au"
JURISDICTION_CODE = "vic"


async def _enumerate_ids(
    client: TenderSearchClient, preset: str, max_pages: int
) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for page in range(1, max_pages + 1):
        try:
            html = await client.get_text(
                "/contract/search",
                preset=preset,
                page=page,
            )
        except Exception as exc:
            logger.warning("[vic] failed page %d: %s", page, exc)
            break
        page_ids = parse_contract_ids(html)
        new = [i for i in page_ids if i not in seen]
        if not new:
            break
        ids.extend(new)
        seen.update(new)
        logger.info("[vic] page %d: %d new IDs (total %d)", page, len(new), len(ids))
    return ids


async def _fetch_detail(client: TenderSearchClient, contract_id: int) -> Release | None:
    try:
        html = await client.get_text(f"/contract/view", id=contract_id)
    except Exception as exc:
        logger.debug("[vic:%d] fetch error: %s", contract_id, exc)
        return None

    detail = parse_detail_html(html)
    if not detail.fields:
        return None

    try:
        return detail_to_release(detail, contract_id, JURISDICTION_CODE)
    except Exception as exc:
        logger.warning("[vic:%d] transform error: %s", contract_id, exc)
        return None


async def scrape(
    preset: str = "recentlyAwarded",
    max_pages: int = 20,
    min_interval_s: float = 3.0,
) -> ReleasePackage:
    """
    Scrape Victoria recently-awarded contracts.

    Args:
        preset: TenderSearch preset (recentlyAwarded, organisationWide, ...)
        max_pages: cap on list pages to walk. Default 20 = 500 contracts.
        min_interval_s: seconds between requests. Default 3.0.
    """
    min_interval_s = max(min_interval_s, 3.0)

    async with TenderSearchClient(
        base_url=BASE_URL,
        min_interval_s=min_interval_s,
    ) as client:
        ids = await _enumerate_ids(client, preset=preset, max_pages=max_pages)
        logger.info("[vic] enumerated %d contract IDs", len(ids))

        releases: list[Release] = []
        for contract_id in ids:
            release = await _fetch_detail(client, contract_id)
            if release is None:
                continue
            releases.append(release)
            if len(releases) % 10 == 0:
                logger.info("[vic] %d releases collected", len(releases))

    logger.info("Produced %d VIC releases", len(releases))
    return ReleasePackage(
        uri="https://github.com/demitonapp/au-procurement/releases/vic",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

"""
ACT Contracts Register scraper.

Source:    data.act.gov.au Socrata SODA API
Dataset:   pfs5-8d64 (ACT Government Contracts Register)
Publisher: ACT Government Procurement (procurement.act.gov.au)
Format:    JSON via Socrata SODA API
Threshold: AU$25,000 (Government Procurement Act 2001 ACT)
Updates:   Monthly
Coverage:  2016-present

No API key is required for read access to public Socrata datasets. Higher
rate limits and named app token are available but not needed at this volume.
"""

from __future__ import annotations

import logging
from datetime import datetime

from au_procurement.models.ocds import Publisher, Release, ReleasePackage
from au_procurement.scrapers.base import OPENCONTRACTSAU_UA, RateLimitedClient
from au_procurement.transformers.act import record_to_release

logger = logging.getLogger(__name__)

BASE_URL = "https://www.data.act.gov.au"
DATASET_VIEW_ID = "pfs5-8d64"
RESOURCE_ENDPOINT = f"/resource/{DATASET_VIEW_ID}.json"

SOCRATA_PAGE_SIZE = 50000


async def _fetch_page(
    client: RateLimitedClient,
    offset: int,
    limit: int,
    where: str | None = None,
) -> list[dict]:
    params: dict[str, str | int] = {
        "$limit": limit,
        "$offset": offset,
        "$order": "execution_date DESC",
    }
    if where:
        params["$where"] = where

    response = await client.get(BASE_URL + RESOURCE_ENDPOINT, params=params)
    data = response.json()
    if not isinstance(data, list):
        logger.warning("Unexpected Socrata response shape: %s", type(data).__name__)
        return []
    return data


async def scrape(
    where: str | None = None,
    max_records: int | None = None,
    min_interval_s: float = 1.0,
) -> ReleasePackage:
    """
    Scrape the ACT Contracts Register.

    Args:
        where: optional SoQL WHERE clause, e.g. "execution_date > '2024-01-01'"
        max_records: cap on total records (None = unlimited)
        min_interval_s: seconds between paginated requests (default 1.0)
    """
    all_records: list[dict] = []
    offset = 0

    async with RateLimitedClient(
        base_url=BASE_URL,
        min_interval_s=min_interval_s,
        user_agent=OPENCONTRACTSAU_UA,
        check_robots=False,  # data.act.gov.au is a public open-data API, not a website
    ) as client:
        while True:
            page_size = SOCRATA_PAGE_SIZE
            if max_records is not None:
                remaining = max_records - len(all_records)
                if remaining <= 0:
                    break
                page_size = min(page_size, remaining)

            logger.info("Fetching ACT records offset=%d limit=%d", offset, page_size)
            page = await _fetch_page(client, offset, page_size, where)
            if not page:
                break
            all_records.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

    logger.info("Fetched %d ACT records", len(all_records))

    releases: list[Release] = []
    for seq, record in enumerate(all_records, start=1):
        try:
            release = record_to_release(record, seq=seq)
        except Exception as exc:
            logger.warning("Skip malformed ACT record %d: %s", seq, exc)
            continue
        if release is not None:
            releases.append(release)

    return ReleasePackage(
        uri="https://github.com/demitonapp/au-procurement/releases/act",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

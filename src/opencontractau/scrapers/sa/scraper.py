"""
South Australia recently-awarded contracts scraper using Playwright.

Source:    contracts.sa.gov.au
List:      /contract/search?page={N}
Detail:    /contract/view?id={id}
Format:    TenderSearch Java HTML (see ../_tendersearch/)
"""

from __future__ import annotations

import logging
from datetime import datetime

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers._tendersearch.parser import (
    parse_contract_ids,
    parse_detail_html,
)
from opencontractau.scrapers._tendersearch.transformer import detail_to_release
from opencontractau.scrapers.browser import PlaywrightClient

logger = logging.getLogger(__name__)

BASE_URL = "https://www.contracts.sa.gov.au"
JURISDICTION_CODE = "sa"


async def _enumerate_ids(
    client: PlaywrightClient, max_pages: int
) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()

    for page_num in range(1, max_pages + 1):
        try:
            url = f"{BASE_URL}/contract/search?page={page_num}"
            logger.info("[sa] Enumerating page %d", page_num)

            # Use custom page logic with Playwright to handle potential form clicks
            page = await client._context.new_page()
            try:
                await page.goto(url, timeout=30000)
                await page.wait_for_selector(
                    'input[type="submit"], button[type="submit"], tr[id^="contractRow"]',
                    timeout=15000,
                )

                # Check if rows are present
                rows = await page.query_selector_all('tr[id^="contractRow"]')
                if not rows:
                    logger.info("[sa] No rows found, attempting to submit search form...")
                    submit_button = await page.query_selector(
                        'input[type="submit"], button[type="submit"], input[value="Search"]'
                    )
                    if submit_button:
                        await submit_button.click()
                        await page.wait_for_selector('tr[id^="contractRow"]', timeout=15000)

                html = await page.content()
            finally:
                await page.close()

            page_ids = parse_contract_ids(html)
            new = [i for i in page_ids if i not in seen]
            if not new:
                break
            ids.extend(new)
            seen.update(new)
            logger.info("[sa] page %d: %d new IDs (total %d)", page_num, len(new), len(ids))
        except Exception as exc:
            logger.warning("[sa] failed page %d: %s", page_num, exc)
            break

    return ids


async def _fetch_detail(client: PlaywrightClient, contract_id: int) -> Release | None:
    try:
        url = f"{BASE_URL}/contract/view?id={contract_id}"
        html = await client.get_html(url, wait_for_selector='span.LIST_TITLE, div.col-sm-8')
    except Exception as exc:
        logger.debug("[sa:%d] fetch error: %s", contract_id, exc)
        return None

    detail = parse_detail_html(html)
    if not detail.fields:
        return None

    try:
        return detail_to_release(detail, contract_id, JURISDICTION_CODE)
    except Exception as exc:
        logger.warning("[sa:%d] transform error: %s", contract_id, exc)
        return None


async def scrape(
    max_pages: int = 20,
    min_interval_s: float = 3.0,
) -> ReleasePackage:
    """
    Scrape South Australia recently-awarded contracts.

    Args:
        max_pages: cap on list pages to walk. Default 20 = 500 contracts.
        min_interval_s: seconds between requests. Default 3.0.
    """
    min_interval_s = max(min_interval_s, 3.0)

    async with PlaywrightClient(min_interval_s=min_interval_s) as client:
        ids = await _enumerate_ids(client, max_pages=max_pages)
        logger.info("[sa] enumerated %d contract IDs", len(ids))

        releases: list[Release] = []
        for contract_id in ids:
            release = await _fetch_detail(client, contract_id)
            if release is None:
                continue
            releases.append(release)
            if len(releases) % 10 == 0:
                logger.info("[sa] %d releases collected", len(releases))

    logger.info("Produced %d SA releases", len(releases))
    return ReleasePackage(
        uri="https://github.com/demitonapp/opencontractau/releases/sa",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

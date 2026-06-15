"""
Queensland TMR contract disclosure scraper.

Data source: data.qld.gov.au CKAN package `tmr-contract-disclosure`
Publisher:   Queensland Department of Transport and Main Roads
Format:      CSV per financial year, updated monthly
Threshold:   Contracts over $10,000 (Queensland Procurement Policy)
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime

from opencontractsau.models.ocds import Publisher, Release, ReleasePackage
from opencontractsau.scrapers.base import OPENCONTRACTSAU_UA, RateLimitedClient
from opencontractsau.transformers.qld import row_to_release

logger = logging.getLogger(__name__)

CKAN_BASE = "https://www.data.qld.gov.au"
PACKAGE_ID = "tmr-contract-disclosure"
PACKAGE_URL = f"{CKAN_BASE}/api/3/action/package_show?id={PACKAGE_ID}"


@dataclass
class ResourceMeta:
    resource_id: str
    name: str
    url: str
    year: str | None
    format: str


async def _fetch_resources(client: RateLimitedClient) -> list[ResourceMeta]:
    payload = await client.get_json(PACKAGE_URL)
    result = payload.get("result", {})
    resources = result.get("resources", [])

    out: list[ResourceMeta] = []
    for r in resources:
        fmt = (r.get("format") or "").upper()
        if fmt not in ("CSV", "XLSX"):
            continue
        if fmt != "CSV":
            logger.debug("Skipping non-CSV resource: %s (%s)", r.get("name"), fmt)
            continue
        name = r.get("name") or ""
        year = _extract_year(name)
        out.append(ResourceMeta(
            resource_id=r["id"],
            name=name,
            url=r["url"],
            year=year,
            format=fmt,
        ))

    out.sort(key=lambda r: r.year or "", reverse=True)
    return out


def _extract_year(name: str) -> str | None:
    import re
    m = re.search(r"20\d{2}-20\d{2}|20\d{2}-\d{2}", name)
    return m.group(0) if m else None


async def _fetch_and_parse_csv(
    client: RateLimitedClient,
    resource: ResourceMeta,
) -> list[Release]:
    logger.info("Downloading %s (%s)", resource.name, resource.url)
    try:
        raw_bytes = await client.get_bytes(resource.url)
    except Exception as exc:
        logger.error("Failed to download %s: %s", resource.url, exc)
        return []

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")

    releases: list[Release] = []
    reader = csv.DictReader(io.StringIO(text))
    for seq, row in enumerate(reader, start=1):
        try:
            release = row_to_release(row, seq=seq)
        except Exception as exc:
            logger.warning("Row %d parse error in %s: %s", seq, resource.name, exc)
            continue
        if release is not None:
            releases.append(release)

    logger.info("Parsed %d releases from %s", len(releases), resource.name)
    return releases


async def scrape(
    years: list[str] | None = None,
    all_years: bool = False,
    min_interval_s: float = 1.0,
) -> ReleasePackage:
    """
    Scrape QLD TMR contract disclosures.

    Args:
        years: financial year strings to fetch, e.g. ["2024-2025", "2023-2024"].
               If None and all_years is False, fetches the most recent year only.
        all_years: fetch all available years (may be slow).
        min_interval_s: seconds between requests (default 1.0, CKAN is tolerant).
    """
    async with RateLimitedClient(
        base_url=CKAN_BASE,
        min_interval_s=min_interval_s,
        user_agent=OPENCONTRACTSAU_UA,
        check_robots=False,  # data.qld.gov.au/robots.txt disallows /api/ but this is a public open-data API
    ) as client:
        resources = await _fetch_resources(client)

    if not resources:
        logger.warning("No CSV resources found in %s", PACKAGE_ID)
        return ReleasePackage(
            publishedDate=datetime.utcnow(),
            releases=[],
        )

    if years:
        year_set = set(years)
        selected = [r for r in resources if r.year and r.year in year_set]
        if not selected:
            logger.warning("No resources found for years %s", years)
            selected = resources[:1]
    elif all_years:
        selected = resources
    else:
        selected = resources[:1]

    logger.info("Fetching %d resource(s): %s", len(selected), [r.year for r in selected])

    all_releases: list[Release] = []
    async with RateLimitedClient(
        min_interval_s=min_interval_s,
        user_agent=OPENCONTRACTSAU_UA,
        check_robots=False,  # data.qld.gov.au/robots.txt disallows /api/ but this is a public open-data API
    ) as client:
        for resource in selected:
            releases = await _fetch_and_parse_csv(client, resource)
            all_releases.extend(releases)
            if len(selected) > 1:
                await asyncio.sleep(min_interval_s)

    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractsau/releases/qld/tmr",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=all_releases,
    )

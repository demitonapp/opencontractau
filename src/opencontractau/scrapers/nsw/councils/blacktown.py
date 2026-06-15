"""
Blacktown City Council awarded contract register scraper.

Source:    blacktown.nsw.gov.au/Contracts-Register
Format:    HTML list page + individual contract detail pages
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Ongoing

The register has a list page with links to individual contract pages
(e.g. /Contracts-Register/9-2025). Each contract page has a simple
field layout with title, contractor, value, and award date.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers.base import BROWSER_UA
from opencontractau.scrapers.qld.councils._client import extract_tables
from opencontractau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.blacktown.nsw.gov.au"
LIST_PATH = "/Contracts-Register"
_CONTRACT_LINK_RE = re.compile(r'href="(/Contracts-Register/[\w\-]+)"', re.IGNORECASE)

# Field label patterns for detail page (definition-list or table layout)
_DL_RE = re.compile(
    r'<dt[^>]*>\s*(.*?)\s*</dt>\s*<dd[^>]*>\s*(.*?)\s*</dd>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

COUNCIL_KEY = "BLACKTOWN"
COUNCIL_NAME = "Blacktown City Council"


def _strip(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _parse_detail_page(html: str, path: str) -> CouncilContractRow | None:
    """Parse a single Blacktown contract detail page."""
    fields: dict[str, str] = {}

    # Try definition list (<dt>/<dd>) first
    for m in _DL_RE.finditer(html):
        label = _strip(m.group(1))
        value = _strip(m.group(2))
        if label and value:
            fields[label.lower()] = value

    # Fallback: try table rows
    if not fields:
        tables = extract_tables(html)
        for t in tables:
            for row in t:
                if len(row) >= 2:
                    fields[row[0].lower().strip()] = row[1].strip()

    if not fields:
        return None

    def _get(*keys: str) -> str:
        for key in keys:
            for k, v in fields.items():
                if key in k and v:
                    return v
        return ""

    title = _get("title", "subject", "description", "contract name") or f"Blacktown {path}"
    supplier = _get("contractor", "supplier", "awarded to", "company", "vendor")
    if not supplier:
        return None

    value_raw = _get("value", "amount", "contract value", "price")
    date_raw = _get("award date", "date awarded", "date", "executed", "commence")
    ref = _get("contract number", "reference", "contract no", "number")

    return CouncilContractRow(
        council_key=COUNCIL_KEY,
        council_name=COUNCIL_NAME,
        reference=ref or path.split("/")[-1],
        title=title,
        awarded_to=supplier,
        value_aud=parse_value(value_raw),
        award_date=parse_au_date(date_raw),
    )


async def scrape(max_contracts: int = 200, **kwargs) -> ReleasePackage:
    """Crawl and parse the Blacktown City Council contract register."""
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        # Step 1: fetch the list page and extract contract links
        try:
            list_resp = await client.get(BASE_URL + LIST_PATH)
            list_resp.raise_for_status()
            list_html = list_resp.text
        except Exception as exc:
            logger.error("BLACKTOWN: list page fetch failed: %s", exc)
            return ReleasePackage(
                uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
                publishedDate=datetime.utcnow(),
                publisher=Publisher(),
                releases=[],
            )

        paths = list(dict.fromkeys(m.group(1) for m in _CONTRACT_LINK_RE.finditer(list_html)))
        # Exclude the base list page itself
        paths = [p for p in paths if p.lower() != "/contracts-register"]
        logger.info("BLACKTOWN: found %d contract links", len(paths))

        # Step 2: fetch each contract detail page
        rows: list[CouncilContractRow] = []
        import asyncio
        for path in paths[:max_contracts]:
            try:
                resp = await client.get(BASE_URL + path)
                resp.raise_for_status()
                row = _parse_detail_page(resp.text, path)
                if row:
                    rows.append(row)
            except Exception as exc:
                logger.debug("BLACKTOWN: %s failed: %s", path, exc)
            await asyncio.sleep(3.0)

    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("BLACKTOWN: %d releases ready from %d pages", len(releases), len(paths))
    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

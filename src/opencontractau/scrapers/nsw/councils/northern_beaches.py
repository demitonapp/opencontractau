"""
Northern Beaches Council awarded contract register scraper.

Source:    northernbeaches.nsw.gov.au/council/tenders/contracts-register
Format:    HTML table (sortable, ~90+ entries per page)
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Ongoing (regularly maintained)

The register is an HTML table published on the council's website.
Two date-range pages: current year and pre-July 2025 archive.
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

REGISTER_URLS = [
    "https://www.northernbeaches.nsw.gov.au/council/tenders/contracts-register",
    "https://www.northernbeaches.nsw.gov.au/council/tenders/contracts-register/contracts-prior-1-july-2025",
]

COUNCIL_KEY = "NORTHERN_BEACHES"
COUNCIL_NAME = "Northern Beaches Council"


def _find_col(headers: list[str], *fragments: str) -> int | None:
    for i, h in enumerate(headers):
        if any(f.lower() in h.lower() for f in fragments):
            return i
    return None


def _parse_rows(html: str) -> list[CouncilContractRow]:
    tables = extract_tables(html)
    if not tables:
        return []

    best: list[list[str]] | None = None
    for t in tables:
        if not t or len(t) < 2:
            continue
        header = " ".join(t[0]).lower()
        if any(k in header for k in ("contractor", "supplier", "awarded", "contract", "description")):
            best = t
            break
    if best is None:
        best = max(tables, key=len) if tables else None
    if not best or len(best) < 2:
        return []

    headers = [h.strip() for h in best[0]]
    col_ref = _find_col(headers, "contract no", "reference", "number", "ref", "id")
    col_title = _find_col(headers, "description", "title", "subject", "contract name", "purpose")
    col_supplier = _find_col(headers, "contractor", "supplier", "awarded to", "vendor", "company")
    col_value = _find_col(headers, "value", "amount", "contract value", "$")
    col_date = _find_col(headers, "award date", "date awarded", "date", "commence", "signed")

    if col_supplier is None:
        col_supplier = 1 if len(headers) > 1 else 0
    if col_title is None:
        col_title = 0

    rows: list[CouncilContractRow] = []
    for data_row in best[1:]:
        padded = data_row + [""] * (len(headers) - len(data_row))
        supplier = padded[col_supplier].strip() if col_supplier is not None else ""
        if not supplier:
            continue
        title = padded[col_title].strip() if col_title is not None else ""
        value_raw = padded[col_value].strip() if col_value is not None else ""
        date_raw = padded[col_date].strip() if col_date is not None else ""
        ref = padded[col_ref].strip() if col_ref is not None else None

        rows.append(CouncilContractRow(
            council_key=COUNCIL_KEY,
            council_name=COUNCIL_NAME,
            reference=ref or None,
            title=title or f"Northern Beaches Contract - {supplier}",
            awarded_to=supplier,
            value_aud=parse_value(value_raw),
            award_date=parse_au_date(date_raw),
        ))

    logger.info("NORTHERN_BEACHES: parsed %d rows from page", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the Northern Beaches Council contracts register."""
    all_rows: list[CouncilContractRow] = []
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        for url in REGISTER_URLS:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                rows = _parse_rows(resp.text)
                all_rows.extend(rows)
            except Exception as exc:
                logger.warning("NORTHERN_BEACHES: %s failed: %s", url, exc)

    releases: list[Release] = [r for seq, row in enumerate(all_rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("NORTHERN_BEACHES: %d releases ready", len(releases))
    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

"""
Brisbane City Council awarded contract register scraper.

Source:     brisbane.qld.gov.au/.../closed-and-awarded-tenders
Format:     HTML page with two tables; second table = Awarded Contracts
Threshold:  All contracts (no stated lower bound)
ABN:        Not disclosed -- supplier name only
Updates:    Ad hoc (not a regular publication schedule)

Verified column structure (2026-05):
  RFx number / title | Approval date | Suppliers who tendered |
  Awarded to | Estimated/maximum value | Potential maximum term
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from opencontractsau.models.ocds import Organization, Publisher, Release, ReleasePackage
from opencontractsau.scrapers.base import BROWSER_UA, RateLimitedClient
from opencontractsau.scrapers.qld.councils._client import extract_tables, table_to_dicts
from opencontractsau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

REGISTER_URL = (
    "https://www.brisbane.qld.gov.au/business/"
    "council-tenders-and-market-led-proposals/"
    "current-tenders/closed-and-awarded-tenders"
)

COUNCIL_KEY = "BCC"
COUNCIL_NAME = "Brisbane City Council"

# Column header fragments to match (case-insensitive substring)
_COL_REFERENCE = "rfx"
_COL_APPROVAL = "approval"
_COL_AWARDED_TO = "awarded to"
_COL_VALUE = "value"
_COL_TERM = "term"


def _find_col(headers: list[str], fragment: str) -> int | None:
    for i, h in enumerate(headers):
        if fragment.lower() in h.lower():
            return i
    return None


def _parse_rows(html: str) -> list[CouncilContractRow]:
    tables = extract_tables(html)

    # Find the table that contains "awarded to" in its header row
    awarded_table: list[list[str]] | None = None
    for table in tables:
        if not table:
            continue
        header_text = " ".join(table[0]).lower()
        if "awarded" in header_text:
            awarded_table = table
            break

    if not awarded_table:
        logger.warning("BCC: could not find awarded contracts table in page HTML")
        return []

    headers = [h.strip() for h in awarded_table[0]]
    col_ref = _find_col(headers, _COL_REFERENCE)
    col_approval = _find_col(headers, _COL_APPROVAL)
    col_awarded = _find_col(headers, _COL_AWARDED_TO)
    col_value = _find_col(headers, _COL_VALUE)
    col_term = _find_col(headers, _COL_TERM)

    if col_awarded is None:
        logger.warning("BCC: 'Awarded to' column not found. Headers: %s", headers)
        return []

    rows: list[CouncilContractRow] = []
    for data_row in awarded_table[1:]:
        padded = data_row + [""] * (len(headers) - len(data_row))

        reference = padded[col_ref].strip() if col_ref is not None else None
        title = padded[0].strip()  # first cell usually has RFx + title
        award_date_raw = padded[col_approval].strip() if col_approval is not None else ""
        awarded_to = padded[col_awarded].strip() if col_awarded is not None else ""
        value_raw = padded[col_value].strip() if col_value is not None else ""

        if not awarded_to:
            continue

        rows.append(CouncilContractRow(
            council_key=COUNCIL_KEY,
            council_name=COUNCIL_NAME,
            reference=reference or None,
            title=title or f"BCC Contract {reference or ''}",
            awarded_to=awarded_to,
            value_aud=parse_value(value_raw),
            award_date=parse_au_date(award_date_raw),
        ))

    logger.info("BCC: parsed %d awarded contract rows", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the BCC awarded contract register."""
    async with RateLimitedClient(
        user_agent=BROWSER_UA,
        min_interval_s=3.0,
        timeout_s=60.0,
    ) as client:
        html = await client.get_text(REGISTER_URL)

    rows = _parse_rows(html)

    releases: list[Release] = []
    for seq, row in enumerate(rows, start=1):
        release = row_to_release(row, seq=seq)
        if release:
            releases.append(release)

    logger.info("BCC: %d releases ready", len(releases))

    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

"""
Mornington Peninsula Shire awarded public tenders scraper.

Source:    mornpen.vic.gov.au/About-Us/Doing-business-with-us/Awarded-Public-Tenders
Format:    HTML table
Threshold: Per Local Government Act 2020 VIC (contracts >$150,000 require Council resolution)
ABN:       Not disclosed
Updates:   Ongoing

Mornington Peninsula is one of Victoria's larger regional councils
with significant civil infrastructure spend. The awarded tenders page
is a publicly accessible HTML table with no bot protection.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from opencontractsau.models.ocds import Publisher, Release, ReleasePackage
from opencontractsau.scrapers.base import BROWSER_UA
from opencontractsau.scrapers.qld.councils._client import extract_tables
from opencontractsau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

REGISTER_URLS = [
    "https://www.mornpen.vic.gov.au/About-Us/Doing-business-with-us/Awarded-Public-Tenders",
    "https://www.mornpen.vic.gov.au/About-Us/Doing-Business-With-Us/Awarded-Public-Tenders",
]

COUNCIL_KEY = "MORNINGTON_PENINSULA"
COUNCIL_NAME = "Mornington Peninsula Shire"


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
        if any(k in header for k in ("contractor", "supplier", "tender", "contract", "description", "awarded")):
            best = t
            break
    if best is None:
        best = max(tables, key=len) if tables else None
    if not best or len(best) < 2:
        return []

    headers = [h.strip() for h in best[0]]
    col_ref = _find_col(headers, "tender no", "reference", "number", "ref", "id", "contract no")
    col_title = _find_col(headers, "description", "title", "subject", "tender title", "contract name", "purpose", "works")
    col_supplier = _find_col(headers, "contractor", "supplier", "awarded to", "successful", "vendor", "company")
    col_value = _find_col(headers, "value", "amount", "contract value", "$", "total", "price")
    col_date = _find_col(headers, "awarded", "date", "commence", "signed", "executed", "resolved")

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
            title=title or f"Mornington Peninsula Contract - {supplier}",
            awarded_to=supplier,
            value_aud=parse_value(value_raw),
            award_date=parse_au_date(date_raw),
        ))

    logger.info("MORNINGTON_PENINSULA: parsed %d rows", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the Mornington Peninsula Shire awarded public tenders register."""
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        html = ""
        for url in REGISTER_URLS:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                if len(resp.text) > 500:
                    html = resp.text
                    break
            except Exception as exc:
                logger.warning("MORNINGTON_PENINSULA: %s failed: %s", url, exc)

    if not html:
        logger.error("MORNINGTON_PENINSULA: all register URLs failed")
        return ReleasePackage(
            uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
            publishedDate=datetime.utcnow(),
            publisher=Publisher(),
            releases=[],
        )

    rows = _parse_rows(html)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("MORNINGTON_PENINSULA: %d releases ready", len(releases))
    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

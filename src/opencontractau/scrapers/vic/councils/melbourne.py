"""
City of Melbourne major service contracts register scraper.

Source:    melbourne.vic.gov.au/.../register-major-service-contracts.aspx
Format:    HTML table
Threshold: AU$1,000,000 (City of Melbourne procurement policy)
ABN:       Not disclosed -- supplier name only
Updates:   Ongoing (updated as contracts are awarded)

Melbourne is Victoria's largest council and a major civil infrastructure
buyer. The $1M+ threshold means lower volume but all records are
significant-scale works.

Verified columns (2026):
  Contract Reference | Contract Description | Contractor |
  Contract Value | Term/Period | Date Awarded
"""

from __future__ import annotations

import logging
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
    "https://www.melbourne.vic.gov.au/business/grants-tenders/tenders/Pages/register-major-service-contracts.aspx",
    "https://www.melbourne.vic.gov.au/business/tenders-and-contracts/Pages/register-major-service-contracts.aspx",
]

COUNCIL_KEY = "MELBOURNE"
COUNCIL_NAME = "City of Melbourne"


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
        if any(k in header for k in ("contractor", "supplier", "contract", "description", "value")):
            if len(t) > best_len if (best_len := len(best) if best else 0) else True:
                best = t

    if not best or len(best) < 2:
        return []

    headers = [h.strip() for h in best[0]]
    col_ref = _find_col(headers, "reference", "number", "contract no", "ref", "id")
    col_title = _find_col(headers, "description", "title", "subject", "service", "contract name", "purpose")
    col_supplier = _find_col(headers, "contractor", "supplier", "awarded to", "vendor", "company")
    col_value = _find_col(headers, "value", "amount", "contract value", "$", "total")
    col_date = _find_col(headers, "awarded", "date", "commence", "signed", "executed")

    if col_supplier is None:
        col_supplier = 2 if len(headers) > 2 else 0
    if col_title is None:
        col_title = 1 if len(headers) > 1 else 0

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
            title=title or f"Melbourne Contract - {supplier}",
            awarded_to=supplier,
            value_aud=parse_value(value_raw),
            award_date=parse_au_date(date_raw),
        ))

    logger.info("MELBOURNE: parsed %d rows", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the City of Melbourne major service contracts register."""
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
                logger.warning("MELBOURNE: %s failed: %s", url, exc)

    if not html:
        logger.error("MELBOURNE: all register URLs failed")
        return ReleasePackage(
            uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
            publishedDate=datetime.utcnow(),
            publisher=Publisher(),
            releases=[],
        )

    rows = _parse_rows(html)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("MELBOURNE: %d releases ready", len(releases))
    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

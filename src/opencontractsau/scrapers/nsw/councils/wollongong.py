"""
Wollongong City Council awarded contract register scraper.

Source:    wollongong.nsw.gov.au/your-council/access-to-information/information-registers/contracts-register
Format:    HTML table (interactive, paginated, Class 1/2/3 sections)
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Ongoing

The contracts register is split by GIPA class. Class 1 is the primary
table for civil construction contracts ($150k-$5M range).
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

BASE_URL = "https://www.wollongong.nsw.gov.au"
REGISTER_PATHS = [
    "/your-council/access-to-information/information-registers/contracts-register",
    "/council/access-to-information/contracts-register",
    "/council/access-to-information/information-registers/contracts-register",
]

COUNCIL_KEY = "WOLLONGONG"
COUNCIL_NAME = "Wollongong City Council"


def _find_col(headers: list[str], *fragments: str) -> int | None:
    for i, h in enumerate(headers):
        if any(f.lower() in h.lower() for f in fragments):
            return i
    return None


def _parse_rows(html: str) -> list[CouncilContractRow]:
    tables = extract_tables(html)
    if not tables:
        return []

    # Collect rows from all tables that look like contract registers
    all_rows: list[CouncilContractRow] = []

    for t in tables:
        if not t or len(t) < 2:
            continue
        header = " ".join(t[0]).lower()
        if not any(k in header for k in ("contractor", "supplier", "awarded", "contract", "description", "value")):
            continue

        headers = [h.strip() for h in t[0]]
        col_ref = _find_col(headers, "contract no", "reference", "number", "ref", "id")
        col_title = _find_col(headers, "description", "title", "subject", "contract name", "purpose", "goods")
        col_supplier = _find_col(headers, "contractor", "supplier", "awarded to", "vendor", "company")
        col_value = _find_col(headers, "value", "amount", "contract value", "$")
        col_date = _find_col(headers, "award date", "date awarded", "date", "commence", "signed", "executed")

        if col_supplier is None:
            col_supplier = 1 if len(headers) > 1 else 0
        if col_title is None:
            col_title = 0

        for data_row in t[1:]:
            padded = data_row + [""] * (len(headers) - len(data_row))
            supplier = padded[col_supplier].strip() if col_supplier is not None else ""
            if not supplier:
                continue
            title = padded[col_title].strip() if col_title is not None else ""
            value_raw = padded[col_value].strip() if col_value is not None else ""
            date_raw = padded[col_date].strip() if col_date is not None else ""
            ref = padded[col_ref].strip() if col_ref is not None else None

            all_rows.append(CouncilContractRow(
                council_key=COUNCIL_KEY,
                council_name=COUNCIL_NAME,
                reference=ref or None,
                title=title or f"Wollongong Contract - {supplier}",
                awarded_to=supplier,
                value_aud=parse_value(value_raw),
                award_date=parse_au_date(date_raw),
            ))

    logger.info("WOLLONGONG: parsed %d rows", len(all_rows))
    return all_rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the Wollongong City Council contracts register."""
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        html = ""
        for path in REGISTER_PATHS:
            try:
                resp = await client.get(BASE_URL + path)
                resp.raise_for_status()
                if len(resp.text) > 500:
                    html = resp.text
                    break
            except Exception as exc:
                logger.warning("WOLLONGONG: %s failed: %s", path, exc)

    if not html:
        logger.error("WOLLONGONG: all paths failed")
        return ReleasePackage(
            uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
            publishedDate=datetime.utcnow(),
            publisher=Publisher(),
            releases=[],
        )

    rows = _parse_rows(html)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("WOLLONGONG: %d releases ready", len(releases))
    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

"""
City of Moreton Bay awarded tender register scraper.

Source:     moretonbay.qld.gov.au/.../Awarded-Tenders
Format:     HTML table (403 to standard UA; curl_cffi bypasses)
Threshold:  Contracts per Local Government Regulation 2012 s.237 (>$200K)
ABN:        Not disclosed
Updates:    Regularly updated

Moreton Bay is one of Australia's fastest-growing councils ($400M capex 2025-26).
The awarded-tenders register is publicly accessible but blocks automated UAs.
curl_cffi with Chrome impersonation bypasses the check.
"""

from __future__ import annotations

import logging
from datetime import datetime

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers.qld.councils._client import CouncilClient, extract_tables
from opencontractau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.moretonbay.qld.gov.au"
REGISTER_PATHS = [
    "/Services/Business-Investment/Doing-Business-With-Council/Tenders/Awarded-Tenders",
    "/Council/Governance-Transparency/Contracts-Register",
]

COUNCIL_KEY = "MORETON_BAY"
COUNCIL_NAME = "City of Moreton Bay"


def _find_col(headers: list[str], *fragments: str) -> int | None:
    for i, h in enumerate(headers):
        if any(f.lower() in h.lower() for f in fragments):
            return i
    return None


def _parse_rows(html: str) -> list[CouncilContractRow]:
    tables = extract_tables(html)
    if not tables:
        logger.warning("MORETON_BAY: no tables found in HTML")
        return []

    best_table: list[list[str]] | None = None
    for table in tables:
        if not table or len(table) < 2:
            continue
        header_text = " ".join(table[0]).lower()
        if any(k in header_text for k in ("contractor", "supplier", "awarded", "tender", "contract")):
            best_table = table
            break

    if best_table is None:
        best_table = max(tables, key=len) if tables else None

    if not best_table or len(best_table) < 2:
        logger.warning("MORETON_BAY: suitable table not found")
        return []

    headers = [h.strip() for h in best_table[0]]
    col_title = _find_col(headers, "title", "description", "tender", "contract name", "subject")
    col_supplier = _find_col(headers, "contractor", "supplier", "awarded to", "vendor", "company")
    col_value = _find_col(headers, "value", "amount", "price", "contract value")
    col_date = _find_col(headers, "date", "awarded", "award date", "commence")
    col_ref = _find_col(headers, "ref", "number", "tender no", "rfq", "id")

    if col_title is None:
        col_title = 0
    if col_supplier is None:
        col_supplier = 1 if len(headers) > 1 else 0

    rows: list[CouncilContractRow] = []
    for data_row in best_table[1:]:
        padded = data_row + [""] * (len(headers) - len(data_row))

        supplier = padded[col_supplier].strip() if col_supplier is not None else ""
        if not supplier:
            continue

        title_raw = padded[col_title].strip() if col_title is not None else ""
        value_raw = padded[col_value].strip() if col_value is not None else ""
        date_raw = padded[col_date].strip() if col_date is not None else ""
        ref = padded[col_ref].strip() if col_ref is not None else None

        rows.append(CouncilContractRow(
            council_key=COUNCIL_KEY,
            council_name=COUNCIL_NAME,
            reference=ref or None,
            title=title_raw or f"MBRC Contract - {supplier}",
            awarded_to=supplier,
            value_aud=parse_value(value_raw),
            award_date=parse_au_date(date_raw),
        ))

    logger.info("MORETON_BAY: parsed %d rows", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the City of Moreton Bay awarded tender register."""
    html = ""
    async with CouncilClient(BASE_URL) as client:
        for path in REGISTER_PATHS:
            try:
                html = await client.get_text(path)
                if html and len(html) > 500:
                    break
            except Exception as exc:
                logger.warning("MORETON_BAY: path %s failed: %s", path, exc)

    from opencontractau.models.ocds import Publisher

    if not html:
        logger.error("MORETON_BAY: all register paths failed")
        return ReleasePackage(
            uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
            publishedDate=datetime.utcnow(),
            publisher=Publisher(),
            releases=[],
        )

    rows = _parse_rows(html)

    releases: list[Release] = []
    for seq, row in enumerate(rows, start=1):
        release = row_to_release(row, seq=seq)
        if release:
            releases.append(release)

    logger.info("MORETON_BAY: %d releases ready", len(releases))

    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

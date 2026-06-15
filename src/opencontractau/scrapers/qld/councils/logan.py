"""
Logan City Council awarded contract register scraper.

Source:     logan.qld.gov.au/...contracts-awarded/large-contractual-arrangements
Format:     HTML table (403 to standard UA; curl_cffi bypasses)
Threshold:  Contracts >$200,000
ABN:        Not disclosed
Updates:    Ongoing (regularly updated)

The council publishes contracts on two pages:
- /contracts-awarded/large-contractual-arrangements  (>$200K)
- /contracts-awarded-over-200k  (alternate URL, same content)

curl_cffi with Chrome TLS impersonation bypasses Logan's bot detection.

Typical column structure (QLD council standard):
  Contract Title | Contractor | Contract Value | Contract Period | Award Date
  (exact headers may vary -- parsed by substring match)
"""

from __future__ import annotations

import logging
from datetime import datetime

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers.qld.councils._client import (
    CouncilClient,
    extract_tables,
)
from opencontractau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.logan.qld.gov.au"
REGISTER_PATHS = [
    "/business-and-investment/doing-business-with-council/contracts-awarded/large-contractual-arrangements",
    "/contracts-awarded-over-200k",
]

COUNCIL_KEY = "LOGAN_COUNCIL"
COUNCIL_NAME = "Logan City Council"


def _find_col(headers: list[str], *fragments: str) -> int | None:
    for i, h in enumerate(headers):
        if any(f.lower() in h.lower() for f in fragments):
            return i
    return None


def _parse_rows(html: str) -> list[CouncilContractRow]:
    tables = extract_tables(html)
    if not tables:
        logger.warning("LOGAN_COUNCIL: no tables found in HTML")
        return []

    # Find the table most likely to be the contracts register
    best_table: list[list[str]] | None = None
    for table in tables:
        if not table or len(table) < 2:
            continue
        header_text = " ".join(table[0]).lower()
        if any(k in header_text for k in ("contractor", "supplier", "contract", "awarded")):
            best_table = table
            break

    if best_table is None:
        best_table = max(tables, key=len) if tables else None

    if not best_table or len(best_table) < 2:
        logger.warning("LOGAN_COUNCIL: suitable table not found")
        return []

    headers = [h.strip() for h in best_table[0]]
    col_title = _find_col(headers, "title", "description", "contract name")
    col_supplier = _find_col(headers, "contractor", "supplier", "awarded", "vendor")
    col_value = _find_col(headers, "value", "amount", "price")
    col_date = _find_col(headers, "date", "awarded", "commence")
    col_ref = _find_col(headers, "ref", "number", "id")

    # Positional fallback for standard QLD council layout
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
            title=title_raw or f"Logan Contract - {supplier}",
            awarded_to=supplier,
            value_aud=parse_value(value_raw),
            award_date=parse_au_date(date_raw),
        ))

    logger.info("LOGAN_COUNCIL: parsed %d rows", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the Logan City Council contract register."""
    html = ""
    async with CouncilClient(BASE_URL) as client:
        for path in REGISTER_PATHS:
            try:
                html = await client.get_text(path)
                if html and len(html) > 500:
                    break
                logger.debug("LOGAN_COUNCIL: path %s returned short response, trying next", path)
            except Exception as exc:
                logger.warning("LOGAN_COUNCIL: path %s failed: %s", path, exc)

    if not html:
        logger.error("LOGAN_COUNCIL: all register paths failed")
        return ReleasePackage(
            uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
            publishedDate=datetime.utcnow(),
            publisher=__import__("opencontractau.models.ocds", fromlist=["Publisher"]).Publisher(),
            releases=[],
        )

    rows = _parse_rows(html)

    from opencontractau.models.ocds import Publisher
    releases: list[Release] = []
    for seq, row in enumerate(rows, start=1):
        release = row_to_release(row, seq=seq)
        if release:
            releases.append(release)

    logger.info("LOGAN_COUNCIL: %d releases ready", len(releases))

    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

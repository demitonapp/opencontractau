"""
Ipswich City Council awarded contract register scraper.

Source:     ipswich.qld.gov.au Transparency and Integrity Hub (OpenGov platform)
Format:     OpenGov platform (Redman Solutions) -- likely JSON API or HTML table
Threshold:  $10,000 (GST exclusive) -- very high volume; scraper applies $50K floor
ABN:        Not disclosed
Updates:    Monthly

Ipswich uses the OpenGov platform (stories.opengov.com/ipswichqld) which may
expose structured data. The scraper tries the council's own transparency hub
first, then the OpenGov embed URL.

Note: The $10K threshold produces very high volume including many non-civil
contracts (maintenance, IT, supplies). The scraper applies a configurable
min_value_aud floor (default $50,000) to reduce noise.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from opencontractsau.models.ocds import Publisher, Release, ReleasePackage
from opencontractsau.scrapers.qld.councils._client import CouncilClient, extract_tables
from opencontractsau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ipswich.qld.gov.au"
REGISTER_PATHS = [
    "/About-Council/Initiatives/Transparency-and-Integrity-Hub",
    "/about_council/mayor_and_councillors/transparency-and-integrity-hub",
]

OPENGOV_BASE = "https://stories.opengov.com"
OPENGOV_PATH = "/ipswichqld"

COUNCIL_KEY = "IPSWICH_COUNCIL"
COUNCIL_NAME = "Ipswich City Council"

DEFAULT_MIN_VALUE = 50_000  # apply floor to reduce noise from $10K threshold


def _find_col(headers: list[str], *fragments: str) -> int | None:
    for i, h in enumerate(headers):
        if any(f.lower() in h.lower() for f in fragments):
            return i
    return None


def _parse_rows_from_html(html: str, min_value: float) -> list[CouncilContractRow]:
    tables = extract_tables(html)
    if not tables:
        logger.warning("IPSWICH_COUNCIL: no tables found in HTML")
        return []

    best_table: list[list[str]] | None = None
    for table in tables:
        if not table or len(table) < 2:
            continue
        header_text = " ".join(table[0]).lower()
        if any(k in header_text for k in ("contractor", "supplier", "vendor", "awarded", "contract")):
            best_table = table
            break

    if best_table is None:
        best_table = max(tables, key=len) if tables else None

    if not best_table or len(best_table) < 2:
        logger.warning("IPSWICH_COUNCIL: suitable table not found")
        return []

    headers = [h.strip() for h in best_table[0]]
    col_title = _find_col(headers, "title", "description", "contract name", "subject", "purpose")
    col_supplier = _find_col(headers, "contractor", "supplier", "awarded", "vendor", "payee")
    col_value = _find_col(headers, "value", "amount", "payment", "contract value")
    col_date = _find_col(headers, "date", "commence", "award")
    col_ref = _find_col(headers, "ref", "number", "id")

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

        value_aud = parse_value(value_raw)
        if value_aud is not None and float(value_aud) < min_value:
            continue  # apply value floor

        rows.append(CouncilContractRow(
            council_key=COUNCIL_KEY,
            council_name=COUNCIL_NAME,
            reference=ref or None,
            title=title_raw or f"ICC Contract - {supplier}",
            awarded_to=supplier,
            value_aud=value_aud,
            award_date=parse_au_date(date_raw),
        ))

    logger.info("IPSWICH_COUNCIL: parsed %d rows (min_value=$%s)", len(rows), min_value)
    return rows


async def scrape(min_value_aud: float = DEFAULT_MIN_VALUE, **kwargs) -> ReleasePackage:
    """Fetch and parse the Ipswich City Council contract register.

    Args:
        min_value_aud: Minimum contract value to include (default $50K).
                       Ipswich's $10K threshold generates very high volume.
    """
    html = ""

    # Try council site first
    async with CouncilClient(BASE_URL) as client:
        for path in REGISTER_PATHS:
            try:
                html = await client.get_text(path)
                if html and len(html) > 500:
                    break
            except Exception as exc:
                logger.warning("IPSWICH_COUNCIL: council path %s failed: %s", path, exc)

    # Fall back to OpenGov platform
    if not html:
        async with CouncilClient(OPENGOV_BASE) as client:
            try:
                html = await client.get_text(OPENGOV_PATH)
            except Exception as exc:
                logger.warning("IPSWICH_COUNCIL: OpenGov path failed: %s", exc)

    from opencontractsau.models.ocds import Publisher

    if not html:
        logger.error("IPSWICH_COUNCIL: all register paths failed")
        return ReleasePackage(
            uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
            publishedDate=datetime.utcnow(),
            publisher=Publisher(),
            releases=[],
        )

    rows = _parse_rows_from_html(html, min_value=min_value_aud)

    releases: list[Release] = []
    for seq, row in enumerate(rows, start=1):
        release = row_to_release(row, seq=seq)
        if release:
            releases.append(release)

    logger.info("IPSWICH_COUNCIL: %d releases ready", len(releases))

    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

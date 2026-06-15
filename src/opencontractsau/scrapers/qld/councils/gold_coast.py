"""
City of Gold Coast awarded contract register scraper.

Source:     goldcoast.qld.gov.au - PDF download
Format:     PDF containing a contract register table
Threshold:  Contracts >$200,000 with minimum 12-month duration
ABN:        Not disclosed
Updates:    Periodic (not a regular schedule)

Requires:   pdfplumber (optional dep, add if missing: uv add pdfplumber)

Gold Coast publishes their contract register as a PDF rather than an
HTML table. The PDF URL is static and updated in place.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

from opencontractsau.models.ocds import Publisher, Release, ReleasePackage
from opencontractsau.scrapers.base import BROWSER_UA, RateLimitedClient
from opencontractsau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

PDF_URL = (
    "https://www.goldcoast.qld.gov.au/files/sharedassets/public/v/63/"
    "pdfs/brochures-amp-factsheets/awarded-contracts.pdf"
)

COUNCIL_KEY = "GC_COUNCIL"
COUNCIL_NAME = "City of Gold Coast"


def _parse_pdf(pdf_bytes: bytes) -> list[CouncilContractRow]:
    try:
        import pdfplumber
    except ImportError:
        logger.error(
            "Gold Coast scraper requires pdfplumber. "
            "Install: uv add pdfplumber (in opencontractsau)"
        )
        return []

    rows: list[CouncilContractRow] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            table = page.extract_table()
            if not table:
                logger.debug("GC_COUNCIL: page %d has no table", page_num)
                continue

            # First row may be headers on each page
            header_row = table[0]
            header_text = " ".join(str(c) for c in header_row if c).lower()

            # Detect header row vs data row
            is_header = any(
                kw in header_text
                for kw in ("entity", "contractor", "supplier", "value", "purpose", "contract")
            )
            data_rows = table[1:] if is_header else table

            # Map columns by header content (best-effort)
            col_name = col_value = col_purpose = col_date = col_ref = None
            if is_header:
                for i, cell in enumerate(header_row):
                    if not cell:
                        continue
                    lc = str(cell).lower()
                    if any(k in lc for k in ("entity", "contractor", "supplier", "name")):
                        col_name = i
                    elif "value" in lc or "amount" in lc:
                        col_value = i
                    elif "purpose" in lc or "description" in lc or "title" in lc:
                        col_purpose = i
                    elif "date" in lc or "commence" in lc or "award" in lc:
                        col_date = i
                    elif "ref" in lc or "number" in lc or "id" in lc:
                        col_ref = i

            # Fall back to positional if headers unclear:
            # GC PDF typically: [Contracting Entity, Value, Contract Purpose, Commencement Date]
            if col_name is None:
                col_name = 0
            if col_value is None:
                col_value = 1
            if col_purpose is None:
                col_purpose = 2
            if col_date is None:
                col_date = 3

            for data_row in data_rows:
                padded = list(data_row) + [None] * 8

                supplier = str(padded[col_name] or "").strip()
                if not supplier or supplier.lower() in ("n/a", "-", "entity name", "contractor"):
                    continue

                value_raw = str(padded[col_value] or "").strip()
                purpose = str(padded[col_purpose] or "").strip()
                date_raw = str(padded[col_date] or "").strip()
                ref = str(padded[col_ref] or "").strip() if col_ref is not None else None

                rows.append(CouncilContractRow(
                    council_key=COUNCIL_KEY,
                    council_name=COUNCIL_NAME,
                    reference=ref or None,
                    title=purpose or f"GC Contract - {supplier}",
                    awarded_to=supplier,
                    value_aud=parse_value(value_raw),
                    award_date=parse_au_date(date_raw),
                ))

    logger.info("GC_COUNCIL: parsed %d rows from PDF", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Download and parse the Gold Coast awarded contracts PDF."""
    async with RateLimitedClient(
        user_agent=BROWSER_UA,
        min_interval_s=3.0,
        timeout_s=60.0,
    ) as client:
        pdf_bytes = await client.get_bytes(PDF_URL)

    rows = _parse_pdf(pdf_bytes)

    releases: list[Release] = []
    for seq, row in enumerate(rows, start=1):
        release = row_to_release(row, seq=seq)
        if release:
            releases.append(release)

    logger.info("GC_COUNCIL: %d releases ready", len(releases))

    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

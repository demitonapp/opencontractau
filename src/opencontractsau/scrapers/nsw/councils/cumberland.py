"""
Cumberland Council awarded contract register scraper.

Source:    cumberland.nsw.gov.au/register-government-contracts
Format:    PDF download (pdfplumber), URL pattern: gipa-report-{month}-{year}.pdf
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Monthly

URL pattern (confirmed May 2026):
  https://www.cumberland.nsw.gov.au/sites/default/files/inline-files/gipa-report-{month}-{year}.pdf
  where month is lowercase e.g. "may-2026", "april-2026"

The scraper tries the last 6 months in reverse order to find the latest PDF.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta

import httpx

from opencontractsau.models.ocds import Publisher, Release, ReleasePackage
from opencontractsau.scrapers.base import BROWSER_UA
from opencontractsau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.cumberland.nsw.gov.au"
PDF_PATTERN = "/sites/default/files/inline-files/gipa-report-{month}-{year}.pdf"

COUNCIL_KEY = "CUMBERLAND"
COUNCIL_NAME = "Cumberland Council"

_MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


def _candidate_urls(lookback_months: int = 6) -> list[str]:
    """Generate candidate PDF URLs for the last N months."""
    today = date.today()
    urls = []
    for i in range(lookback_months):
        d = date(today.year, today.month, 1) - timedelta(days=1) * (i * 30)
        month_name = _MONTHS[d.month - 1]
        urls.append(BASE_URL + PDF_PATTERN.format(month=month_name, year=d.year))
    return urls


def _parse_pdf(pdf_bytes: bytes) -> list[CouncilContractRow]:
    try:
        import pdfplumber
    except ImportError:
        logger.error("CUMBERLAND: pdfplumber not installed")
        return []

    rows: list[CouncilContractRow] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    # Find header row
                    header_idx = 0
                    for i, row in enumerate(table[:5]):
                        if row and any(
                            cell and any(k in (cell or "").lower() for k in ("supplier", "contractor", "description", "value", "contract"))
                            for cell in row
                        ):
                            header_idx = i
                            break

                    headers = [str(c or "").strip().lower() for c in table[header_idx]]

                    def _col(*keys: str) -> int | None:
                        for j, h in enumerate(headers):
                            if any(k in h for k in keys):
                                return j
                        return None

                    col_title = _col("description", "title", "subject", "goods", "services", "contract name")
                    col_supplier = _col("supplier", "contractor", "awarded", "vendor", "company")
                    col_value = _col("value", "amount", "$", "contract value")
                    col_date = _col("date", "award", "execute", "commence", "signed")
                    col_ref = _col("ref", "number", "contract no", "id")

                    if col_supplier is None:
                        continue

                    for data_row in table[header_idx + 1:]:
                        if not data_row or not any(data_row):
                            continue

                        def _cell(idx: int | None) -> str:
                            if idx is None or idx >= len(data_row):
                                return ""
                            return str(data_row[idx] or "").strip()

                        supplier = _cell(col_supplier)
                        if not supplier or len(supplier) < 2:
                            continue

                        rows.append(CouncilContractRow(
                            council_key=COUNCIL_KEY,
                            council_name=COUNCIL_NAME,
                            reference=_cell(col_ref) or None,
                            title=_cell(col_title) or f"Cumberland Contract - {supplier}",
                            awarded_to=supplier,
                            value_aud=parse_value(_cell(col_value)),
                            award_date=parse_au_date(_cell(col_date)),
                        ))
    except Exception as exc:
        logger.error("CUMBERLAND: PDF parse error: %s", exc)

    logger.info("CUMBERLAND: parsed %d rows from PDF", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Discover and parse the Cumberland Council GIPA contracts PDF."""
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        pdf_bytes: bytes | None = None
        for url in _candidate_urls():
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    pdf_bytes = resp.content
                    logger.info("CUMBERLAND: found PDF at %s", url)
                    break
            except Exception:
                pass

    if not pdf_bytes:
        logger.error("CUMBERLAND: could not find PDF for any of the last 6 months")
        return ReleasePackage(
            uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
            publishedDate=datetime.utcnow(),
            publisher=Publisher(),
            releases=[],
        )

    rows = _parse_pdf(pdf_bytes)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("CUMBERLAND: %d releases ready", len(releases))
    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

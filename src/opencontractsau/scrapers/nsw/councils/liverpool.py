"""
Liverpool City Council awarded contract register scraper.

Source:    liverpool.nsw.gov.au/council/corporate-information/public-access-to-information
Format:    PDF download (pdfplumber), URL discovered from the GIPA page
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Bimonthly (approx.)

URL pattern (confirmed Jan 2026, Nov 2025):
  liverpool.nsw.gov.au/__data/assets/pdf_file/{CMS_ID}/Government-Contracts-Register-GIPA-Act-Internet-{Month-Year}.PDF
  CMS ID changes per upload; must discover URL from the GIPA page at runtime.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime

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

BASE_URL = "https://www.liverpool.nsw.gov.au"
GIPA_PAGE_PATH = "/council/corporate-information/public-access-to-information"

# Matches links to the GIPA contracts register PDF (case-insensitive)
_PDF_LINK_RE = re.compile(
    r'href="([^"]*(?:contracts[- ]register|gipa[^"]*contract)[^"]*\.pdf)"',
    re.IGNORECASE,
)
_GENERIC_PDF_RE = re.compile(r'href="([^"]*\.pdf)"', re.IGNORECASE)

COUNCIL_KEY = "LIVERPOOL_NSW"
COUNCIL_NAME = "Liverpool City Council"


def _parse_pdf(pdf_bytes: bytes) -> list[CouncilContractRow]:
    try:
        import pdfplumber
    except ImportError:
        logger.error("LIVERPOOL_NSW: pdfplumber not installed")
        return []

    rows: list[CouncilContractRow] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue

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

                    col_title = _col("description", "title", "subject", "goods", "services", "contract name", "nature")
                    col_supplier = _col("supplier", "contractor", "awarded", "vendor", "company", "name of")
                    col_value = _col("value", "amount", "$", "contract value", "total")
                    col_date = _col("date", "award", "execute", "commence", "signed")
                    col_ref = _col("ref", "number", "contract no", "id", "registration")

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
                            title=_cell(col_title) or f"Liverpool Contract - {supplier}",
                            awarded_to=supplier,
                            value_aud=parse_value(_cell(col_value)),
                            award_date=parse_au_date(_cell(col_date)),
                        ))
    except Exception as exc:
        logger.error("LIVERPOOL_NSW: PDF parse error: %s", exc)

    logger.info("LIVERPOOL_NSW: parsed %d rows from PDF", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Discover and parse the Liverpool City Council GIPA contracts PDF."""
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"User-Agent": BROWSER_UA},
        follow_redirects=True,
    ) as client:
        # Step 1: find the PDF link on the GIPA page
        pdf_url: str | None = None
        try:
            page_resp = await client.get(BASE_URL + GIPA_PAGE_PATH)
            page_resp.raise_for_status()
            page_html = page_resp.text

            m = _PDF_LINK_RE.search(page_html)
            if not m:
                # Broader fallback: any PDF link mentioning register or gipa
                for m2 in _GENERIC_PDF_RE.finditer(page_html):
                    href = m2.group(1).lower()
                    if any(k in href for k in ("contract", "gipa", "register")):
                        m = m2
                        break

            if m:
                href = m.group(1)
                pdf_url = href if href.startswith("http") else BASE_URL + href
        except Exception as exc:
            logger.warning("LIVERPOOL_NSW: GIPA page fetch failed: %s", exc)

        if not pdf_url:
            logger.error("LIVERPOOL_NSW: could not discover PDF URL from GIPA page")
            return ReleasePackage(
                uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
                publishedDate=datetime.utcnow(),
                publisher=Publisher(),
                releases=[],
            )

        # Step 2: download PDF
        logger.info("LIVERPOOL_NSW: downloading %s", pdf_url)
        try:
            pdf_resp = await client.get(pdf_url)
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content
        except Exception as exc:
            logger.error("LIVERPOOL_NSW: PDF download failed: %s", exc)
            return ReleasePackage(
                uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
                publishedDate=datetime.utcnow(),
                publisher=Publisher(),
                releases=[],
            )

    rows = _parse_pdf(pdf_bytes)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("LIVERPOOL_NSW: %d releases ready", len(releases))
    return ReleasePackage(
        uri=f"https://github.com/demitonapp/opencontractsau/releases/{COUNCIL_KEY}",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

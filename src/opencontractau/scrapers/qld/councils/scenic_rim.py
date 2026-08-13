"""
Scenic Rim Regional Council awarded contract register scraper.

Source:     scenicrim.qld.gov.au Governance > Contracts and Accumulative Payments
Format:     PDF download (pdfplumber), URL discovered from the register page
Threshold:  $200,000 (GST inclusive) -- last 12 months + continuing contracts
ABN:        Not disclosed
Updates:    Periodic

Verified live (2026-08-13): the page moved from
".../Administration/Financial-Information/..." to
".../Governance/Previous-Years-Financial-Information/..." (the old path
301s there) and, more significantly, the register itself is no longer an
HTML table - it is two downloadable PDFs discovered from that page, whose
filenames carry a month/year stamp (e.g.
"contracts-greater-than-200000-feb-2026-20260130-pos.pdf").

The two PDFs are not duplicates: "-pos.pdf" ("Point-of-sale"? - the
council does not document the suffix) is the 12-month awarded-contracts
table with a Date column - Date | Contractor | Description of Goods or
Services | Value (GST Inclusive), header repeated on every page. "-pyts"
is a separate top-suppliers-by-spend table with no date and no per-
contract granularity (one row can already be an annual total across many
individual contracts with the one supplier), so it is not a register in
the same sense and is deliberately not parsed here - mixing it in would
double-count spend already carried by "-pos" rows and fabricate award
dates for money that was never tied to a single award. This register also
carries no contract-reference column, so ``row_to_release``'s OCID
fallback (a hash of title+supplier) is what disambiguates entries.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers.qld.councils._client import CouncilClient
from opencontractau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.scenicrim.qld.gov.au"
REGISTER_PATH = "/Our-Council/Governance/Previous-Years-Financial-Information/Contracts-and-Accumulative-Payments"

# Matches only the "-pos.pdf" awarded-contracts table, not "-pyts.pdf".
_PDF_LINK_RE = re.compile(
    r'href="([^"]*contracts[^"]*greater[^"]*200[,_]?000[^"]*-pos\.pdf)"',
    re.IGNORECASE,
)

COUNCIL_KEY = "SCENIC_RIM"
COUNCIL_NAME = "Scenic Rim Regional Council"

_HEADER_HINTS = ("date", "contractor", "description", "value")


def _clean(raw: str | None) -> str:
    return re.sub(r"\s+", " ", (raw or "")).strip()


def _is_header(cells: list[str | None]) -> bool:
    normed = [_clean(c).lower() for c in cells]
    return len(normed) >= 4 and all(hint in c for c, hint in zip(normed, _HEADER_HINTS))


def pdf_tables_to_rows(tables: list[list[list[str | None]]]) -> list[CouncilContractRow]:
    """Turn extracted PDF tables into contract rows. Pure - no PDF, no network."""
    rows: list[CouncilContractRow] = []
    for table in tables:
        for cells in table:
            if _is_header(cells) or not any(_clean(c) for c in cells):
                continue

            padded = list(cells) + [None] * (4 - len(cells))
            date_raw, supplier, title_raw, value_raw = (_clean(c) for c in padded[:4])

            if not supplier:
                continue

            rows.append(CouncilContractRow(
                council_key=COUNCIL_KEY,
                council_name=COUNCIL_NAME,
                reference=None,
                title=title_raw or f"{COUNCIL_NAME} Contract - {supplier}",
                awarded_to=supplier,
                value_aud=parse_value(value_raw),
                award_date=parse_au_date(date_raw),
            ))

    return rows


def _parse_pdf(pdf_bytes: bytes) -> list[CouncilContractRow]:
    try:
        import pdfplumber
    except ImportError:
        logger.error("SCENIC_RIM: pdfplumber not installed")
        return []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            tables = [t for page in pdf.pages for t in (page.extract_tables() or [])]
    except Exception as exc:
        logger.error("SCENIC_RIM: PDF parse error: %s", exc)
        return []

    rows = pdf_tables_to_rows(tables)
    logger.info("SCENIC_RIM: parsed %d rows from PDF", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Discover and parse the Scenic Rim Regional Council contract register PDF."""
    uri = f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}"

    # base_url="" - the register page path is relative to BASE_URL, but the
    # discovered PDF link is already absolute, so every request below passes
    # its own full URL through unchanged (CouncilClient always prepends
    # base_url, so a non-empty one would double up on an absolute URL).
    async with CouncilClient(base_url="") as client:
        try:
            page_html = await client.get_text(BASE_URL + REGISTER_PATH)
        except Exception as exc:
            logger.warning("SCENIC_RIM: register page fetch failed: %s", exc)
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

        match = _PDF_LINK_RE.search(page_html)
        if not match:
            logger.error("SCENIC_RIM: could not discover PDF URL from register page")
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

        href = match.group(1)
        pdf_url = href if href.startswith("http") else BASE_URL + href

        logger.info("SCENIC_RIM: downloading %s", pdf_url)
        try:
            pdf_bytes = await client.get_bytes(pdf_url)
        except Exception as exc:
            logger.error("SCENIC_RIM: PDF download failed: %s", exc)
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

    rows = _parse_pdf(pdf_bytes)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("SCENIC_RIM: %d releases ready", len(releases))
    return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=releases)

"""
Liverpool City Council awarded contract register scraper.

Source:    liverpool.nsw.gov.au/council/corporate-information/public-access-to-information
Format:    PDF download (pdfplumber), URL discovered from the GIPA page
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Bimonthly (approx.)

URL pattern (confirmed 2026-08-13):
  liverpool.nsw.gov.au/__data/assets/pdf_file/{CMS_ID}/Government-Contracts-Register-GIPA-Act-Internet-{Month-Year}.PDF
  CMS ID changes per upload; must discover URL from the GIPA page at runtime.

The GIPA page and the PDF both 403 a plain httpx request - this council sits
behind the same TLS-fingerprint bot check as the QLD councils, and needs the
curl_cffi Chrome impersonation in CouncilClient (see qld/councils/_client.py)
rather than a browser User-Agent header, which does not help (confirmed).

PDF layout: one continuous logical table split across pages by pdfplumber,
in three sections (Class 1/2/3 Contracts). The header row (Contract |
Description | Contractor | Contractor Address | Awarded Payable/Amount | ...)
repeats only at the START of each section, not on every page - a page-by-page
"first few rows must contain a header" scan (the shape used elsewhere in this
package) would treat a continuation page's first data row as a header and
silently drop every row on it. This parser instead streams rows across the
whole document and carries the most recently seen header forward.

Many "Contract" reference codes are LGP/NSW-panel arrangements shared by many
suppliers - RCL3233 alone covers 94 distinct professional-services suppliers
in the 2026-07 register. `_make_ocid` builds an OCID from the reference alone
when one is present, so without disambiguation here, 183 of 308 rows (every
supplier but the first under each shared reference) would collapse onto one
OCID and vanish on ingest while the harvest reported "308 releases, success".
Rather than change the shared OCID builder - and risk re-keying every other
council's already-live OCIDs - the reference is suffixed with a short
supplier fingerprint locally, but ONLY for references actually shared by more
than one supplier, so an ordinary single-supplier contract's reference stays
exactly as printed in the source register.
"""

from __future__ import annotations

import hashlib
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

# Column label -> normalised header text to match. "value"/"date"/"method" use
# a prefix match because the register uses more than one label for the same
# column across its three sections ("Awarded Payable" vs "Awarded Amount";
# "Contract Start\nDate" normalises the same way in both, so an exact match
# would also work for it, but prefix keeps this resilient to future drift).
_HEADER_EQUALS = {"ref": "contract", "title": "description", "supplier": "contractor"}
_HEADER_STARTSWITH = {
    "value": "awarded",
    "date": "contract start",
    "method": "method of tendering",
}


def _clean(raw: str | None) -> str:
    return re.sub(r"\s+", " ", (raw or "")).strip()


def _detect_header(cells: list[str | None]) -> dict[str, int] | None:
    """Return a column-name -> index map if *cells* looks like a header row."""
    normed = [_clean(c).lower() for c in cells]
    col: dict[str, int] = {}
    for i, h in enumerate(normed):
        for key, target in _HEADER_EQUALS.items():
            if key not in col and h == target:
                col[key] = i
        for key, prefix in _HEADER_STARTSWITH.items():
            if key not in col and h.startswith(prefix):
                col[key] = i
    # A real header carries at minimum the three columns every row needs.
    if {"ref", "title", "supplier"} <= col.keys():
        return col
    return None


def _is_divider_or_blank(cells: list[str | None]) -> bool:
    """Section titles ("Class 2 Contracts") and blank spacer rows carry at
    most one non-empty cell; every real header or data row carries several."""
    return sum(1 for c in cells if _clean(c)) <= 1


def pdf_tables_to_rows(tables: list[list[list[str | None]]]) -> list[CouncilContractRow]:
    """Turn extracted PDF tables into contract rows. Pure - no PDF, no network.

    Split out from the pdfplumber call so the header-carry-forward and
    panel-disambiguation logic is testable without checking a binary fixture
    into the repo.
    """
    parsed: list[dict[str, str]] = []
    active_header: dict[str, int] | None = None
    warned_no_header = False

    for table in tables:
        for cells in table:
            if _is_divider_or_blank(cells):
                continue

            header = _detect_header(cells)
            if header:
                active_header = header
                continue

            if active_header is None:
                if not warned_no_header:
                    logger.warning(
                        "LIVERPOOL_NSW: data row seen before any header - "
                        "the register's column layout may have changed."
                    )
                    warned_no_header = True
                continue

            def cell(key: str) -> str:
                idx = active_header.get(key)
                return _clean(cells[idx]) if idx is not None and idx < len(cells) else ""

            supplier = cell("supplier")
            if not supplier:
                continue

            parsed.append({
                "reference": cell("ref"),
                "title": cell("title"),
                "supplier": supplier,
                "value": cell("value"),
                "date": cell("date"),
                "method": cell("method"),
            })

    # A reference shared by more than one distinct (supplier, title) pair is
    # NOT one contract - either a panel arrangement (one reference, many
    # suppliers, e.g. RCL3233 covers 94 professional-services firms) or the
    # same umbrella reference covering genuinely separate contracts with the
    # same supplier (C4000 is Origin Energy's reference for both a single
    # large-site gas contract and an unrelated small-sites retail agreement).
    # Keying on supplier alone misses the second shape: two rows with the
    # same supplier still need different tags, so the tag is derived from the
    # full (supplier, title) pair, not the supplier alone.
    ref_keys: dict[str, set[tuple[str, str]]] = {}
    for row in parsed:
        if row["reference"]:
            ref_keys.setdefault(row["reference"], set()).add((row["supplier"], row["title"]))

    contract_rows: list[CouncilContractRow] = []
    for row in parsed:
        reference: str | None = row["reference"] or None
        if reference and len(ref_keys.get(reference, ())) > 1:
            tag = hashlib.sha1(
                f"{row['supplier']}|{row['title']}".encode(), usedforsecurity=False
            ).hexdigest()[:6]
            reference = f"{reference}-{tag}"

        contract_rows.append(CouncilContractRow(
            council_key=COUNCIL_KEY,
            council_name=COUNCIL_NAME,
            reference=reference,
            title=row["title"] or f"{COUNCIL_NAME} Contract - {row['supplier']}",
            awarded_to=row["supplier"],
            value_aud=parse_value(row["value"]),
            award_date=parse_au_date(row["date"]),
            procurement_method=row["method"] or None,
        ))

    return contract_rows


def _parse_pdf(pdf_bytes: bytes) -> list[CouncilContractRow]:
    try:
        import pdfplumber
    except ImportError:
        logger.error("LIVERPOOL_NSW: pdfplumber not installed")
        return []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            tables = [t for page in pdf.pages for t in (page.extract_tables() or [])]
    except Exception as exc:
        logger.error("LIVERPOOL_NSW: PDF parse error: %s", exc)
        return []

    rows = pdf_tables_to_rows(tables)
    logger.info("LIVERPOOL_NSW: parsed %d rows from PDF", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Discover and parse the Liverpool City Council GIPA contracts PDF."""
    uri = f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}"

    # base_url="" - the GIPA page path is relative to BASE_URL, but the
    # discovered PDF link is already absolute, so every request below passes
    # its own full URL through unchanged.
    client = CouncilClient(base_url="")
    try:
        try:
            page_html = await client.get_text(BASE_URL + GIPA_PAGE_PATH)
        except Exception as exc:
            logger.warning("LIVERPOOL_NSW: GIPA page fetch failed: %s", exc)
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

        match = _PDF_LINK_RE.search(page_html)
        if not match:
            for candidate in _GENERIC_PDF_RE.finditer(page_html):
                href = candidate.group(1).lower()
                if any(k in href for k in ("contract", "gipa", "register")):
                    match = candidate
                    break

        if not match:
            logger.error("LIVERPOOL_NSW: could not discover PDF URL from GIPA page")
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

        href = match.group(1)
        pdf_url = href if href.startswith("http") else BASE_URL + href

        logger.info("LIVERPOOL_NSW: downloading %s", pdf_url)
        try:
            pdf_bytes = await client.get_bytes(pdf_url)
        except Exception as exc:
            logger.error("LIVERPOOL_NSW: PDF download failed: %s", exc)
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])
    finally:
        await client.close()

    rows = _parse_pdf(pdf_bytes)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("LIVERPOOL_NSW: %d releases ready", len(releases))
    return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=releases)

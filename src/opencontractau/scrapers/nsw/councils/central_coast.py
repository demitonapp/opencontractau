"""
Central Coast Council awarded contract register scraper.

Source:    centralcoast.nsw.gov.au "Contracts register" page, PDF snapshot
Format:    PDF download (pdfplumber), discovered via Drupal's JSON:API
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Disclosed (one of the few councils that does)
Updates:   Periodic (observed roughly every 1-2 months)

The register page (/business/doing-business-council/contracts-register)
renders its ~600-row table via a proprietary "databaseTable" CMS widget
with no discoverable data source: it isn't in the page's server-rendered
HTML, isn't in any XHR fired while loading the page in a real browser, and
isn't in the Drupal content model reachable from the page node (checked
its body field, its field_content paragraphs, and their nested
field_single_list text - all come back empty; the widget's own JS,
filterable-table.js, only sorts rows already in the DOM, it does not fetch
them). curl_cffi gets a ~120KB stub with an empty <tbody>; a real browser
gets a >1MB page with 608 populated <tr> rows - differential serving this
package's Chrome-TLS impersonation does not get past.

The council separately publishes the same register as a periodic PDF
snapshot, and - unlike the live table - that file is reachable without a
browser: Drupal's JSON:API (open, no auth) indexes it as a
``media--document`` entity. There is no dedicated "latest register"
endpoint, so discovery paginates every document whose name contains
"contract", filters to ones also containing "register" (upload naming is
inconsistent: "Contract Register as at 070825.pdf", "contract-register-
110625.pdf", etc.), and takes the one with the newest ``changed``
timestamp. This snapshot lags the live table by however long since the
last upload (observed gap: 2026-08-13 checked, newest snapshot dated
2025-08-07) - real, correctly-structured GIPA data, just not real-time.

Verified columns (2026-08-13, snapshot dated 2025-08-07):
  Contract Number | Contract Title | Contract Description | Contractor |
  ABN | Organisation Business Address | Contract Value (Ex GST) |
  Variation Value (Ex GST) | Contract Start Date | Contract End Date |
  Extension Options Available | GIPA Classification | GIPA Withhold Value |
  Sourcing Method | ...

Header does not repeat on every PDF page (Scenic Rim's register does;
this one doesn't), so this uses Liverpool's carry-forward parser shape:
detect a header row once, then apply it to every following row until a
new header is seen.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import datetime

from opencontractau.scrapers.qld.councils._client import CouncilClient
from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.centralcoast.nsw.gov.au"
_DOCUMENT_SEARCH_PATH = (
    "/jsonapi/media/document?filter[name][operator]=CONTAINS&filter[name][value]=contract"
)
_MAX_SEARCH_PAGES = 10

COUNCIL_KEY = "CENTRAL_COAST_NSW"
COUNCIL_NAME = "Central Coast Council"

_HEADER_EQUALS = {
    "ref": "contract number",
    "title": "contract title",
    "supplier": "contractor",
    "abn": "abn",
}
_HEADER_STARTSWITH = {
    "value": "contract value",
    "date": "contract start date",
    "end_date": "contract end date",
    "method": "sourcing method",
}


def _clean(raw: str | None) -> str:
    return re.sub(r"\s+", " ", (raw or "")).strip()


def _detect_header(cells: list[str | None]) -> dict[str, int] | None:
    normed = [_clean(c).lower() for c in cells]
    col: dict[str, int] = {}
    for i, h in enumerate(normed):
        for key, target in _HEADER_EQUALS.items():
            if key not in col and h == target:
                col[key] = i
        for key, prefix in _HEADER_STARTSWITH.items():
            if key not in col and h.startswith(prefix):
                col[key] = i
    if {"ref", "title", "supplier"} <= col.keys():
        return col
    return None


def _is_divider_or_blank(cells: list[str | None]) -> bool:
    return sum(1 for c in cells if _clean(c)) <= 1


def pdf_tables_to_rows(tables: list[list[list[str | None]]]) -> list[CouncilContractRow]:
    """Turn extracted PDF tables into contract rows. Pure - no PDF, no network."""
    rows: list[CouncilContractRow] = []
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
                        "CENTRAL_COAST_NSW: data row seen before any header - "
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

            start_date = parse_au_date(cell("date"))
            rows.append(CouncilContractRow(
                council_key=COUNCIL_KEY,
                council_name=COUNCIL_NAME,
                reference=cell("ref") or None,
                title=cell("title") or f"{COUNCIL_NAME} Contract - {supplier}",
                awarded_to=supplier,
                value_aud=parse_value(cell("value")),
                award_date=start_date,
                start_date=start_date,
                end_date=parse_au_date(cell("end_date")),
                supplier_abn=cell("abn") or None,
                procurement_method=cell("method") or None,
            ))

    return rows


async def _discover_pdf_url(client: CouncilClient) -> str | None:
    """Find the newest register PDF via Drupal's JSON:API media search."""
    candidates: list[tuple[str, str]] = []  # (changed, media_id)
    path = _DOCUMENT_SEARCH_PATH

    for _ in range(_MAX_SEARCH_PAGES):
        try:
            text = await client.get_text(path if path.startswith("http") else BASE_URL + path)
        except Exception as exc:
            logger.warning("CENTRAL_COAST_NSW: document search page failed: %s", exc)
            break

        payload = json.loads(text)
        for item in payload.get("data", []):
            name = item.get("attributes", {}).get("name", "")
            if "register" not in name.lower():
                continue
            changed = item.get("attributes", {}).get("changed", {}).get("value", "")
            candidates.append((changed, item["id"]))

        next_href = payload.get("links", {}).get("next", {}).get("href")
        if not next_href:
            break
        path = next_href

    if not candidates:
        logger.error("CENTRAL_COAST_NSW: no register document found via JSON:API search")
        return None

    candidates.sort(reverse=True)
    media_id = candidates[0][1]

    try:
        text = await client.get_text(
            f"{BASE_URL}/jsonapi/media/document/{media_id}?include=field_media_document"
        )
    except Exception as exc:
        logger.error("CENTRAL_COAST_NSW: media entity fetch failed: %s", exc)
        return None

    payload = json.loads(text)
    for included in payload.get("included", []):
        if included.get("type") == "file--file":
            url = included.get("attributes", {}).get("uri", {}).get("url")
            if url:
                return url if url.startswith("http") else BASE_URL + url
    return None


def _parse_pdf(pdf_bytes: bytes) -> list[CouncilContractRow]:
    try:
        import pdfplumber
    except ImportError:
        logger.error("CENTRAL_COAST_NSW: pdfplumber not installed")
        return []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            tables = [t for page in pdf.pages for t in (page.extract_tables() or [])]
    except Exception as exc:
        logger.error("CENTRAL_COAST_NSW: PDF parse error: %s", exc)
        return []

    rows = pdf_tables_to_rows(tables)
    logger.info("CENTRAL_COAST_NSW: parsed %d rows from PDF", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Discover and parse the Central Coast Council contracts register PDF."""
    uri = f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}"

    async with CouncilClient(base_url="") as client:
        pdf_url = await _discover_pdf_url(client)
        if not pdf_url:
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

        logger.info("CENTRAL_COAST_NSW: downloading %s", pdf_url)
        try:
            pdf_bytes = await client.get_bytes(pdf_url)
        except Exception as exc:
            logger.error("CENTRAL_COAST_NSW: PDF download failed: %s", exc)
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

    rows = _parse_pdf(pdf_bytes)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("CENTRAL_COAST_NSW: %d releases ready", len(releases))
    return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=releases)

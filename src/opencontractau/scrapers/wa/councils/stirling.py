"""
City of Stirling awarded contract register scraper.

Source:    stirling.wa.gov.au Tender Register - one PDF per tender
Format:    PDF text extraction (pdfplumber), URLs discovered from the
           register page, published per-tender rather than as one
           consolidated register
Threshold: All public tenders (no GIPA-style $ threshold - WA's Local
           Government Act 1995 s3.57 disclosure regime, not NSW GIPA)
ABN:       Not disclosed
Updates:   Ongoing, one PDF added/updated per tender lifecycle stage

Unlike every NSW/QLD council in this package, Stirling has no single
"contracts register" document - each tender gets its own PDF, re-uploaded
as it moves through open -> evaluation -> awarded. The register page
(verified live 2026-08-13; the URL indexed by search engines had a stale
slug and 404s, the real one is only in the site's own sitemap.xml) links
~30 of these at a time. A PDF's filename suffix ("-open", "-evaluation",
"-closed", "-complete") hints at status but isn't reliable enough to
filter on, so this instead reads the awarded fields themselves and skips
any tender still showing "To be advised" - the placeholder every
unawarded field carries.

Text layout, not a table: pdfplumber's table detector finds nothing (no
gridlines), but plain extract_text() gives clean labelled lines. The
catch is label/value placement is inconsistent - "Successful Tenderer:"
puts its value on the same line, "Date of Council Meeting / CEO Award:"
puts it on the next line, and long values wrap onto a further line - so
this reads labels as boundaries in the whitespace-flattened text rather
than trying to parse per physical line. Also inconsistent: the value
label is "Estimated Annual Contract Value:" on some tenders and "Value of
Successful Tenderer:" on others - checked in that order.

One PDF per tender means one CouncilClient request per tender at this
package's mandatory 3s rate limit, on top of the register-page fetch -
unlike every other scraper here, that alone can approach the 90s harvest
budget a caller may be enforcing. ``max_contracts`` truncates the
(newest-listed-first) PDF list before the fetch loop runs, same shape as
VIC/NT's fix for the identical problem; the library's own default here is
20 (up to 63s: 21 requests * 3s), deliberately not None, because unlike
VIC/NT this isn't a Demiton-harvest-specific concern - fetching all ~30
individually-rate-limited PDFs is slow enough to matter for every caller,
not just one with a fixed external budget.
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

BASE_URL = "https://www.stirling.wa.gov.au"
REGISTER_PATH = "/business-and-investment/doing-business-with-the-city/tender-register"

_PDF_LINK_RE = re.compile(r'href="([^"]*Tenders/[^"]*Tender-Register[^"]*\.pdf)"', re.IGNORECASE)

COUNCIL_KEY = "STIRLING_WA"
COUNCIL_NAME = "City of Stirling"

_TBA_VALUES = {"", "to be advised", "tba", "xxxxx/xxxx"}

_LABELS = [
    "Advertisement Notice Tender awarded by:",
    "Date of Council Meeting / CEO Award:",
    "Council Resolution:",
    "Successful Tenderer:",
    "Value of Successful Tenderer:",
    # A third template combines the two value labels onto one wrapped line
    # - "Value of Successful Tenderer(s) or\nEstimated Annual Contract
    # Value:" - and pdfplumber's extraction order interleaves the value
    # between the two label halves rather than after both, e.g. "...
    # Value of Successful Tenderer(s) or To be advised Estimated Annual
    # Contract Value:". Listing the first half as its own boundary stops
    # the Successful Tenderer capture correctly and lets the value be read
    # from between the two halves instead of past the second one.
    "Value of Successful Tenderer(s) or",
    "Estimated Annual Contract Value:",
]


def _clean_pdf_text(pages: list[str]) -> str:
    """Strip the per-page header/footer noise pdfplumber otherwise leaves
    interleaved with real content ("Tender Register", "78986 Tender
    Register Page 2")."""
    lines = []
    for page_text in pages:
        for line in page_text.split("\n"):
            stripped = line.strip()
            if stripped == "Tender Register":
                continue
            if re.match(r"^\d+\s+Tender Register Page \d+$", stripped):
                continue
            lines.append(line)
    return "\n".join(lines)


def _field(flat: str, label: str) -> str:
    others = "|".join(re.escape(other) for other in _LABELS if other != label)
    m = re.search(re.escape(label) + r"\s*(.+?)(?:" + others + r"|\Z)", flat)
    return m.group(1).strip() if m else ""


def parse_tender_pdf(pages: list[str]) -> CouncilContractRow | None:
    """Turn one tender's extracted PDF pages into a contract row, or None
    if it isn't awarded yet. Pure - no PDF, no network."""
    text = _clean_pdf_text(pages)
    flat = re.sub(r"\s+", " ", text).strip().replace("Successful Tenderer(s):", "Successful Tenderer:")

    tenderer = _field(flat, "Successful Tenderer:")
    if tenderer.lower() in _TBA_VALUES:
        return None

    tender_no_m = re.search(r"Tender No:\s*(\d+)", text)
    title_m = re.search(r"^Tender Title:\s*(.+)$", text, re.MULTILINE)

    value_raw = (
        _field(flat, "Estimated Annual Contract Value:")
        or _field(flat, "Value of Successful Tenderer:")
        or _field(flat, "Value of Successful Tenderer(s) or")
    )
    date_raw = _field(flat, "Date of Council Meeting / CEO Award:")

    title = title_m.group(1).strip() if title_m else ""
    return CouncilContractRow(
        council_key=COUNCIL_KEY,
        council_name=COUNCIL_NAME,
        reference=tender_no_m.group(1) if tender_no_m else None,
        title=title or f"{COUNCIL_NAME} Contract - {tenderer}",
        awarded_to=tenderer,
        value_aud=parse_value(value_raw) if value_raw.lower() not in _TBA_VALUES else None,
        award_date=parse_au_date(date_raw) if date_raw.lower() not in _TBA_VALUES else None,
    )


def _parse_pdf_bytes(pdf_bytes: bytes) -> CouncilContractRow | None:
    try:
        import pdfplumber
    except ImportError:
        logger.error("STIRLING_WA: pdfplumber not installed")
        return None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
    except Exception as exc:
        logger.error("STIRLING_WA: PDF parse error: %s", exc)
        return None

    return parse_tender_pdf(pages)


async def scrape(max_contracts: int | None = 20, **kwargs) -> ReleasePackage:
    """Discover and parse the City of Stirling's per-tender register PDFs."""
    uri = f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}"

    async with CouncilClient(base_url="") as client:
        try:
            page_html = await client.get_text(BASE_URL + REGISTER_PATH)
        except Exception as exc:
            logger.error("STIRLING_WA: register page fetch failed: %s", exc)
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

        pdf_links = [m if m.startswith("http") else BASE_URL + m for m in _PDF_LINK_RE.findall(page_html)]
        if max_contracts is not None:
            pdf_links = pdf_links[:max_contracts]

        rows: list[CouncilContractRow] = []
        for url in pdf_links:
            try:
                pdf_bytes = await client.get_bytes(url)
            except Exception as exc:
                logger.warning("STIRLING_WA: %s failed: %s", url, exc)
                continue
            row = _parse_pdf_bytes(pdf_bytes)
            if row:
                rows.append(row)

    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("STIRLING_WA: %d releases ready from %d tender PDFs", len(releases), len(pdf_links))
    return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=releases)

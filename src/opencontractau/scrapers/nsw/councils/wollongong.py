"""
Wollongong City Council awarded contract register scraper.

Source:    wollongong.nsw.gov.au/.../information-registers/contracts-register
Format:    Split across three GIPA classes, each a different shape:
             Class 1 - a Funnelback-hosted CSV export, one request, no
                        per-contract detail page needed.
             Class 2/3 - a list page (contract number + link only, no
                        supplier or value) plus one detail page per
                        contract carrying the full field set.
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Ongoing

The landing page (.../contracts-register) is prose with three links to the
class pages - it carries no register data itself and is not fetched here.

Like Liverpool, this council sits behind a TLS-fingerprint bot check that
403s a plain HTTP client; CouncilClient's curl_cffi Chrome impersonation is
required (see qld/councils/_client.py). A single request against this
council occasionally 403s even with impersonation and succeeds on retry -
observed during development, not chased further; CouncilClient does not
retry, so a genuinely blocked run returns a partial or empty result rather
than hanging, which HARVEST_CAPABILITY's own timeout handling covers.

Every list-page and detail-page cell duplicates its label as invisible
"sr-only" accessibility text ahead of the real value
(`<span class="sr-only">Contractor</span>Batmac Constructions Pty Ltd`) -
stripped before parsing, or every field value comes back prefixed with its
own label.
"""

from __future__ import annotations

import csv
import io
import logging
import re
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

BASE_URL = "https://www.wollongong.nsw.gov.au"
_REGISTER_BASE = (
    "/council/access-to-information/information-registers/contracts-register"
)
_CLASS_LIST_PATH = _REGISTER_BASE + "/contracts-register-class-{cls}"
# Verified live 2026-08-13. Class 1's export exists at this fixed URL with no
# id parameter; classes 2 and 3 have no equivalent bulk export (checked every
# detail page of both - none embeds a Funnelback dataset link), so they are
# scraped per-contract from the list + detail pages instead.
_CLASS_1_CSV_URL = (
    "https://www.wollongong.nsw.gov.au/funnelback-datasets/csv/"
    "contracts-register-class-1.csv"
)

COUNCIL_KEY = "WOLLONGONG"
COUNCIL_NAME = "Wollongong City Council"

_SR_ONLY_RE = re.compile(r'<span class="sr-only">.*?</span>', re.IGNORECASE | re.DOTALL)
_DETAIL_LINK_RE = re.compile(
    r'href="([^"]*contracts-register-class-\d[^"]*details[^"]*)"', re.IGNORECASE,
)

# The detail page's title field is packed with reference numbers
# ("128623 - Wongawilli Hall Refurbishment"); the particulars field is the
# same contract's clean subject line ("Wongawilli Hall Refurbishment").
# Preferred when present and not itself a placeholder.
_TITLE_LABEL = "Contract Description"
_PARTICULARS_LABEL = (
    "Particulars of Project / Goods / Services Provided, or Real Property to "
    "be Leased or Transferred Under Contract"
)


def _strip_sr_only(html: str) -> str:
    return _SR_ONLY_RE.sub("", html)


# ---------------------------------------------------------------------------
# Class 1: CSV export
# ---------------------------------------------------------------------------

def csv_text_to_rows(csv_text: str) -> list[CouncilContractRow]:
    """Parse the Class 1 CSV export. Pure - no network, no pdfplumber."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[CouncilContractRow] = []

    for record in reader:
        supplier = (record.get("Contractor_Name") or "").strip()
        reference = (record.get("Contract") or "").strip()
        if not supplier or not reference:
            continue

        title = (record.get("Narration") or "").strip() or (record.get("Description") or "").strip()

        rows.append(CouncilContractRow(
            council_key=COUNCIL_KEY,
            council_name=COUNCIL_NAME,
            reference=reference,
            title=title or f"{COUNCIL_NAME} Contract - {supplier}",
            awarded_to=supplier,
            value_aud=parse_value(record.get("Estimated_Contract_Amount_Payable_inc_of_GST_over_the_life_of_contract")),
            award_date=parse_au_date(record.get("Start_Date")),
            start_date=parse_au_date(record.get("Start_Date")),
            end_date=parse_au_date(record.get("End_Date")),
            procurement_method=(record.get("Method_of_Tendering") or "").strip() or None,
        ))

    return rows


# ---------------------------------------------------------------------------
# Class 2 / 3: list page + per-contract detail page
# ---------------------------------------------------------------------------

def discover_detail_links(list_html: str) -> list[str]:
    """Distinct detail-page hrefs on a class list page, in document order."""
    seen: list[str] = []
    for href in _DETAIL_LINK_RE.findall(list_html):
        if href not in seen:
            seen.append(href)
    return seen


def detail_fields_to_row(detail_html: str) -> CouncilContractRow | None:
    """Parse one contract's detail page (label/value pairs) into a row.

    Every field on this page is its own single-row, two-column table
    (`<thead><tr><th>Label</th><td>Value</td></tr></thead>`, repeated) rather
    than one table with many rows - extract_tables does not care, since it
    only tracks table/tr/td/th boundaries and treats each as its own table.
    """
    stripped = _strip_sr_only(detail_html)
    fields: dict[str, str] = {}
    for table in extract_tables(stripped):
        for row in table:
            if len(row) >= 2 and row[0].strip():
                fields.setdefault(row[0].strip(), row[1].strip())

    supplier = fields.get("Contractor", "")
    reference = fields.get("Contract Number", "")
    if not supplier or not reference:
        return None

    particulars = fields.get(_PARTICULARS_LABEL, "")
    title = particulars if particulars and particulars.upper() not in ("N/A", "NA") else fields.get(_TITLE_LABEL, "")

    return CouncilContractRow(
        council_key=COUNCIL_KEY,
        council_name=COUNCIL_NAME,
        reference=reference,
        title=title or f"{COUNCIL_NAME} Contract - {supplier}",
        awarded_to=supplier,
        value_aud=parse_value(fields.get("Estimated contract amount (incl. GST)")),
        award_date=parse_au_date(fields.get("Start Date")),
        start_date=parse_au_date(fields.get("Start Date")),
        end_date=parse_au_date(fields.get("End Date")),
        procurement_method=fields.get("Method of Tendering") or None,
    )


async def _scrape_class_2_or_3(client: CouncilClient, cls: int) -> list[CouncilContractRow]:
    try:
        list_html = await client.get_text(BASE_URL + _CLASS_LIST_PATH.format(cls=cls))
    except Exception as exc:
        logger.warning("WOLLONGONG: class %d list page failed: %s", cls, exc)
        return []

    links = discover_detail_links(list_html)
    rows: list[CouncilContractRow] = []
    for href in links:
        url = href if href.startswith("http") else BASE_URL + href
        try:
            detail_html = await client.get_text(url)
        except Exception as exc:
            logger.warning("WOLLONGONG: class %d detail page failed: %s: %s", cls, url, exc)
            continue
        row = detail_fields_to_row(detail_html)
        if row:
            rows.append(row)

    logger.info("WOLLONGONG: class %d - %d of %d detail pages yielded a row", cls, len(rows), len(links))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    uri = f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}"
    client = CouncilClient(base_url="")
    all_rows: list[CouncilContractRow] = []

    try:
        try:
            csv_text = await client.get_text(_CLASS_1_CSV_URL)
            all_rows.extend(csv_text_to_rows(csv_text))
        except Exception as exc:
            logger.warning("WOLLONGONG: class 1 CSV failed: %s", exc)

        for cls in (2, 3):
            all_rows.extend(await _scrape_class_2_or_3(client, cls))
    finally:
        await client.close()

    releases: list[Release] = [
        r for seq, row in enumerate(all_rows, 1) if (r := row_to_release(row, seq=seq))
    ]
    logger.info("WOLLONGONG: %d releases ready across all 3 classes", len(releases))
    return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=releases)

"""
Sutherland Shire Council awarded contract register scraper.

Source:    sutherlandshire.nsw.gov.au GIPA Expenditure Contract Register
Format:    HTML table (curl_cffi; not observed bot-walled but kept
           consistent with the rest of this package)
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Ongoing

The council publishes two separate GIPA register pages: "Expenditure"
(council paying a contractor - normal procurement, what this scrapes) and
"Revenue" (council receiving money - leases, event-space licences). The
revenue register is a different transaction direction entirely (not
"who won a contract to build/supply something") and its table shape is
inconsistent between sub-sections (some rows carry no company name at
all, being property descriptions), so it is deliberately out of scope
here.

The expenditure register's summary table has no visible supplier column -
the supplier name is only in the ``title`` attribute of the "Contract
Name" link (e.g. ``<a title="JASMAX PTY LIMITED" href=".../GIPA-
Reporting.pdf">Lead Design Consultant for North Cronulla SLSC</a>``), so
this cannot use the shared ``extract_tables()`` helper - that flattens
cells to plain text and would discard the one attribute this scraper
needs. Verified live (2026-08-13): 18% of rows (the most recently added
contracts) have no ``title`` attribute yet - the per-contract GIPA PDF
exists but hasn't had its name annotated onto the summary table link.
Skipped rather than published with a fabricated or missing supplier, same
as every other council scraper in this package when the source has no
name to give.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from html import unescape

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers.qld.councils._client import CouncilClient
from opencontractau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sutherlandshire.nsw.gov.au"
REGISTER_PATH = (
    "/your-council/privacy-information-and-reporting/access-to-information/"
    "information-registers/gipa-contract-register/gipa-expenditure-contract-register"
)

COUNCIL_KEY = "SUTHERLAND_SHIRE"
COUNCIL_NAME = "Sutherland Shire Council"

_TABLE_RE = re.compile(r"<table.*?</table>", re.S)
_ROW_RE = re.compile(r"<tr.*?</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_LINK_TITLE_RE = re.compile(r'<a[^>]*\btitle="([^"]*)"', re.I)


def _clean(raw: str) -> str:
    return unescape(re.sub(r"\s+", " ", _TAG_RE.sub(" ", raw))).strip()


def _parse_rows(html: str) -> list[CouncilContractRow]:
    rows: list[CouncilContractRow] = []

    for table in _TABLE_RE.findall(html):
        table_rows = _ROW_RE.findall(table)
        if len(table_rows) < 2:
            continue

        for row_html in table_rows[1:]:
            cells = _CELL_RE.findall(row_html)
            if len(cells) < 5:
                continue

            title_match = _LINK_TITLE_RE.search(cells[1])
            supplier = _clean(title_match.group(1)) if title_match else ""
            if not supplier:
                continue

            value_raw = _clean(cells[4]).rstrip("+")

            rows.append(CouncilContractRow(
                council_key=COUNCIL_KEY,
                council_name=COUNCIL_NAME,
                reference=_clean(cells[0]) or None,
                title=_clean(cells[1]) or f"{COUNCIL_NAME} Contract - {supplier}",
                awarded_to=supplier,
                value_aud=parse_value(value_raw),
                award_date=parse_au_date(_clean(cells[2])),
                end_date=parse_au_date(_clean(cells[3])),
            ))

    logger.info("SUTHERLAND_SHIRE: parsed %d rows", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the Sutherland Shire Council expenditure contracts register."""
    uri = f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}"

    async with CouncilClient(base_url="") as client:
        try:
            html = await client.get_text(BASE_URL + REGISTER_PATH)
        except Exception as exc:
            logger.error("SUTHERLAND_SHIRE: register page fetch failed: %s", exc)
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

    rows = _parse_rows(html)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("SUTHERLAND_SHIRE: %d releases ready", len(releases))
    return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=releases)

"""
Shared scraper for Victorian council awarded-contract registers.

Victoria has no standard contract-register format. Under the Local Government
Act 2020 every council must publish awarded contracts, but each publishes in
its own shape, so the fourteen council scrapers that came before this module
were near-identical 150-line copies differing only in a URL, a column-name
guess and a council key.

This module keeps the one thing that actually varies - the register's markup
shape - as a named parser, and everything else as a data row in ``REGISTERS``.
Adding a council whose register matches an existing shape is one entry; adding
one with a new shape is one parser.

Shapes seen in the wild (all verified live 2026-08-13):

``wide_table``
    One row per contract across one or more tables. Wyndham publishes 14 such
    tables, one per financial year, so every table is parsed, not just the
    largest - the single-table assumption in the older council scrapers would
    have silently taken one year and reported success.

``key_value_table``
    One small two-column table per contract, label in column 0. Boroondara
    publishes 77 of them on a single page.

``paragraph_block``
    ``<p><strong>REF - Title</strong><br>Awarded DATE to:<br>SUPPLIER ...``
    blocks. Geelong uses this, and is the only Victorian register found that
    discloses the supplier's ACN/ABN.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape

from curl_cffi import requests as curl_requests

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers.qld.councils._client import extract_tables
from opencontractau.transformers.council import (
    CouncilContractRow,
    parse_au_date,
    parse_value,
    row_to_release,
)

logger = logging.getLogger(__name__)

CHROME_IMPERSONATION = "chrome120"

# Council sites are slow and several sit behind bot protection that rejects a
# plain user-agent; curl_cffi's Chrome TLS fingerprint is what the TenderSearch
# client already uses for the same reason.
_TIMEOUT_S = 60.0

# Contributing policy: 3s between requests to the same council.
_MIN_INTERVAL_S = 3.0


@dataclass(frozen=True)
class CouncilRegister:
    """One council's awarded-contract register."""

    key: str
    name: str
    urls: tuple[str, ...]
    parser: str

    def __post_init__(self) -> None:
        if self.parser not in _PARSERS:
            raise ValueError(
                f"{self.key}: unknown parser {self.parser!r}. "
                f"Known: {sorted(_PARSERS)}"
            )


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _find_col(headers: list[str], *fragments: str) -> int | None:
    for i, header in enumerate(headers):
        if any(f in header.lower() for f in fragments):
            return i
    return None


def _clean(raw: str) -> str:
    # \xa0 survives the \s+ collapse in some CMS exports; unescape first so
    # &nbsp; becomes \xa0, then treat it as whitespace like everything else.
    return re.sub(r"\s+", " ", unescape(raw).replace("\xa0", " ")).strip()


def _date_cell(raw: str | None) -> str | None:
    """Trim a council's trailing qualifier - "16 March 2028 (including Defects
    Liability Period)" is a date plus prose, and parses as neither."""
    if not raw:
        return None
    return raw.split("(")[0].strip() or None


def _looks_like_contract_table(header_row: list[str]) -> bool:
    joined = " ".join(header_row).lower()
    has_party = any(k in joined for k in ("contractor", "supplier", "awarded to", "successful", "vendor"))
    has_subject = any(k in joined for k in ("contract", "tender", "description", "title", "name"))
    return has_party and has_subject


def _parse_wide_table(html: str, reg: CouncilRegister) -> list[CouncilContractRow]:
    """One row per contract, across every contract-shaped table on the page."""
    rows: list[CouncilContractRow] = []

    for table in extract_tables(html):
        if len(table) < 2:
            continue
        headers = [_clean(h) for h in table[0]]
        if not _looks_like_contract_table(headers):
            continue

        col_ref = _find_col(headers, "contract number", "contract no", "tender no", "reference", "ref")
        col_title = _find_col(headers, "contract name", "description", "title", "subject", "works", "purpose")
        col_supplier = _find_col(headers, "supplier", "contractor", "awarded to", "successful", "vendor")
        col_value = _find_col(headers, "value", "amount", "price", "$")
        col_date = _find_col(headers, "date awarded", "awarded", "date", "commence", "signed")

        if col_supplier is None:
            continue

        for data_row in table[1:]:
            padded = [_clean(c) for c in data_row] + [""] * (len(headers) - len(data_row))
            supplier = padded[col_supplier]
            if not supplier:
                continue
            title = padded[col_title] if col_title is not None else ""
            rows.append(CouncilContractRow(
                council_key=reg.key,
                council_name=reg.name,
                reference=(padded[col_ref] if col_ref is not None else "") or None,
                title=title or f"{reg.name} Contract - {supplier}",
                awarded_to=supplier,
                value_aud=parse_value(padded[col_value]) if col_value is not None else None,
                award_date=parse_au_date(padded[col_date]) if col_date is not None else None,
            ))

    return rows


# Order matters: the first matching field wins, so the specific start/end
# labels are tested before the generic award-date ones.
_KV_LABELS = {
    "reference": ("contract number", "contract no", "tender number", "reference"),
    "title": ("contract name", "contract title", "description", "title", "subject"),
    "supplier": ("supplier", "contractor", "awarded to", "successful tenderer", "vendor"),
    "value": ("value", "amount", "price", "contract sum"),
    "start_date": ("start date", "commencement"),
    "end_date": ("end date", "expiry", "completion date"),
    "date": ("date awarded", "awarded", "date of award"),
}


_HEADING_TAG_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)
_TABLE_OPEN_RE = re.compile(r"<table", re.IGNORECASE)


def _headings_before_tables(html: str) -> list[str]:
    """Nearest preceding heading for each table, in document order.

    Boroondara's tables carry no title field at all - the contract name lives
    in the accordion <h3> above each table. Without this every release would
    fall back to "City of Boroondara Contract - <supplier>", which is a label,
    not a title, and useless for matching work types.
    """
    headings = [(m.start(), _clean(re.sub(r"<[^>]+>", " ", m.group(1)))) for m in _HEADING_TAG_RE.finditer(html)]
    result: list[str] = []
    for table_match in _TABLE_OPEN_RE.finditer(html):
        preceding = [text for pos, text in headings if pos < table_match.start() and text]
        result.append(preceding[-1] if preceding else "")
    return result


def _parse_key_value_table(html: str, reg: CouncilRegister) -> list[CouncilContractRow]:
    """One small label/value table per contract."""
    rows: list[CouncilContractRow] = []
    headings = _headings_before_tables(html)

    for table_index, table in enumerate(extract_tables(html)):
        fields: dict[str, str] = {}
        for cells in table:
            if len(cells) < 2:
                continue
            label = _clean(cells[0]).lower().rstrip(":")
            value = _clean(cells[1])
            if not label or not value:
                continue
            for field, fragments in _KV_LABELS.items():
                if field not in fields and any(f in label for f in fragments):
                    fields[field] = value
                    break

        supplier = fields.get("supplier")
        if not supplier:
            continue

        heading = headings[table_index] if table_index < len(headings) else ""

        rows.append(CouncilContractRow(
            council_key=reg.key,
            council_name=reg.name,
            reference=fields.get("reference"),
            title=fields.get("title") or heading or f"{reg.name} Contract - {supplier}",
            awarded_to=supplier,
            value_aud=parse_value(fields.get("value")),
            award_date=parse_au_date(_date_cell(fields.get("date"))),
            start_date=parse_au_date(_date_cell(fields.get("start_date"))),
            end_date=parse_au_date(_date_cell(fields.get("end_date"))),
        ))

    return rows


# <p><strong>C2600041 - Coastside Drive ...</strong><br>Awarded 1 June 2026 to:
# <br>James O. Millar Pty Ltd (ACN 007 406 206)<br>Fixed Price Lump Sum<br>...
_BLOCK_RE = re.compile(
    r"<p[^>]*>\s*<strong[^>]*>(?P<heading>.*?)</strong>(?P<body>.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)
_AWARDED_RE = re.compile(
    r"Awarded\s+(?P<date>\d{1,2}\s+\w+\s+\d{4})\s*(?:to)?\s*:?\s*(?P<rest>.*)",
    re.IGNORECASE | re.DOTALL,
)
# "REF - Title" / "REF – Title" (hyphen or en dash, the page uses both).
_HEADING_RE = re.compile(r"^\s*(?P<ref>[A-Z]{1,3}\d{4,}[\w/\-]*)\s*[-–—]\s*(?P<title>.+)$")

_ABN_RE = re.compile(r"\bABN[:\s]*([\d\s]{11,17})", re.IGNORECASE)
_ACN_RE = re.compile(r"\bACN[:\s]*([\d\s]{9,14})", re.IGNORECASE)
# Everything from the first company-number bracket onward is registry detail,
# not the trading name: "Chemprod Nominees Pty Ltd (ACN 005 032 744) ATF Fried
# Family Chemical Trust (ABN 32 982 143 022) t/a Omega Chemicals".
_NAME_TAIL_RE = re.compile(r"\s*\((?:ABN|ACN)\b.*$", re.IGNORECASE | re.DOTALL)


def _split_company_numbers(raw: str) -> tuple[str, str | None, str | None]:
    """Return (name, abn, acn) from a supplier string that may embed either."""
    abn_match = _ABN_RE.search(raw)
    acn_match = _ACN_RE.search(raw)
    name = _NAME_TAIL_RE.sub("", raw).strip(" -–—,") or raw.strip()
    return (
        name,
        abn_match.group(1).strip() if abn_match else None,
        acn_match.group(1).strip() if acn_match else None,
    )


def _parse_paragraph_block(html: str, reg: CouncilRegister) -> list[CouncilContractRow]:
    """Prose blocks: heading carries ref + title, body carries date + supplier."""
    rows: list[CouncilContractRow] = []

    for match in _BLOCK_RE.finditer(html):
        heading = _clean(re.sub(r"<[^>]+>", " ", match.group("heading")))
        body_lines = [
            _clean(line)
            for line in re.split(r"<br\s*/?>", match.group("body"), flags=re.IGNORECASE)
        ]
        body_lines = [line for line in (re.sub(r"<[^>]+>", " ", b).strip() for b in body_lines) if line]
        if not heading or not body_lines:
            continue

        awarded = _AWARDED_RE.search(" | ".join(body_lines))
        if not awarded:
            continue

        # The supplier is the first line after the "Awarded ... to:" line; when
        # the name sits on the same line it follows the colon.
        rest = _clean(awarded.group("rest")).lstrip("| ").strip()
        supplier_raw = rest.split("|")[0].strip() if rest else ""
        if not supplier_raw:
            continue

        supplier, abn, acn = _split_company_numbers(supplier_raw)
        if not supplier:
            continue

        ref_match = _HEADING_RE.match(heading)
        reference = ref_match.group("ref") if ref_match else None
        title = ref_match.group("title").strip() if ref_match else heading

        rows.append(CouncilContractRow(
            council_key=reg.key,
            council_name=reg.name,
            reference=reference,
            title=title,
            awarded_to=supplier,
            value_aud=None,  # Geelong publishes contract type, not value.
            award_date=parse_au_date(awarded.group("date")),
            supplier_abn=abn,
            supplier_acn=acn,
        ))

    return rows


_PARSERS = {
    "wide_table": _parse_wide_table,
    "key_value_table": _parse_key_value_table,
    "paragraph_block": _parse_paragraph_block,
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Every URL below returned a parseable register on 2026-08-13. A council whose
# register moves should fail loudly on the next harvest rather than be papered
# over with a guessed fallback URL - list real alternates only.

REGISTERS: dict[str, CouncilRegister] = {
    "WYNDHAM": CouncilRegister(
        key="WYNDHAM",
        name="Wyndham City Council",
        urls=("https://www.wyndham.vic.gov.au/about-council/tenders-suppliers/awarded-tenders",),
        parser="wide_table",
    ),
    "BOROONDARA": CouncilRegister(
        key="BOROONDARA",
        name="City of Boroondara",
        urls=("https://www.boroondara.vic.gov.au/services/business/doing-business-council/tenders-and-contracts/awarded-contracts",),
        parser="key_value_table",
    ),
    "GEELONG": CouncilRegister(
        key="GEELONG",
        name="City of Greater Geelong",
        urls=("https://www.geelongaustralia.com.au/tenders/article/item/8cbce9be96ea2e1.aspx",),
        parser="paragraph_block",
    ),
}


# ---------------------------------------------------------------------------
# Fetch + scrape
# ---------------------------------------------------------------------------

async def _fetch(urls: tuple[str, ...], key: str) -> str:
    """Return the first URL's HTML that comes back usable, else ""."""
    session = curl_requests.Session()
    loop = asyncio.get_event_loop()

    try:
        for i, url in enumerate(urls):
            if i:
                await asyncio.sleep(_MIN_INTERVAL_S)
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda u=url: session.get(
                        u,
                        impersonate=CHROME_IMPERSONATION,
                        timeout=_TIMEOUT_S,
                        allow_redirects=True,
                    ),
                )
            except Exception as exc:
                logger.warning("%s: %s failed: %s", key, url, exc)
                continue

            if resp.status_code == 200 and len(resp.text) > 500:
                return resp.text
            logger.warning("%s: %s returned %s (%d bytes)", key, url, resp.status_code, len(resp.text))
    finally:
        session.close()

    return ""


def parse_register(html: str, reg: CouncilRegister) -> list[CouncilContractRow]:
    """Parse *html* with the register's declared parser. Pure - no network."""
    return _PARSERS[reg.parser](html, reg)


async def scrape_register(key: str, **kwargs) -> ReleasePackage:
    """Fetch and parse one council's register."""
    reg = REGISTERS[key]
    uri = f"https://github.com/demitonapp/opencontractau/releases/{reg.key}"

    html = await _fetch(reg.urls, reg.key)
    if not html:
        logger.error("%s: all register URLs failed", reg.key)
        return ReleasePackage(
            uri=uri,
            publishedDate=datetime.utcnow(),
            publisher=Publisher(),
            releases=[],
        )

    rows = parse_register(html, reg)
    releases: list[Release] = [
        release
        for seq, row in enumerate(rows, 1)
        if (release := row_to_release(row, seq=seq))
    ]
    logger.info("%s: parsed %d rows -> %d releases", reg.key, len(rows), len(releases))

    return ReleasePackage(
        uri=uri,
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

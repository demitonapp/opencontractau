"""
Transform Tasmania eTenders contract award HTML into OCDS Releases.

Source: tenders.tas.gov.au/ContractAwarded/Details/{id}

The Tasmania eTenders portal renders ASP.NET MVC pages with a consistent
``<ul name="FieldName"><li>Value</li></ul>`` pattern for scalar fields,
plus structured tables for supplier and contact-person information.

Available fields (confirmed live 2026-05-21):
    ProcurementTitle, UniqueTenderId, ProcurementMethod, RecordStatus,
    AwardedDate, PeriodOfContract (date range string),
    Agency (multi-line: name + address),
    UNSPSC Category, Description (paragraph HTML),
    NumberOfTasmanianBidsReceived, TotalNumberOfBidsReceived,
    Successful supplier table: business name, Tasmanian flag,
        multi-line address, allocated amount
    Contact person table: name, firm/agency
    Contact email (mailto link)

Notable gap: ABN is NOT published on the public TAS contract detail page,
even though the QLD and ACT equivalents include it. Suppliers are
identified by business name and address only.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from decimal import Decimal

from opencontractau.models.ocds import (
    Address,
    Award,
    Contract,
    Organization,
    Period,
    Release,
    Tender,
    Value,
)

logger = logging.getLogger(__name__)

OCID_PREFIX = "ocau-tas"
BUYER_ID_PREFIX = "au-tas-agency"


_FIELD_PATTERN = re.compile(
    r'<ul\s+name="([^"]+)"\s*>(.*?)</ul>',
    re.IGNORECASE | re.DOTALL,
)
_LI_PATTERN = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")

_PERIOD_PATTERN = re.compile(
    r'<label\s+for="PeriodOfContract">.*?<ul[^>]*>\s*<li[^>]*>\s*From\s+'
    r'(\d{1,2}/\d{1,2}/\d{4})\s+to\s+(\d{1,2}/\d{1,2}/\d{4})',
    re.IGNORECASE | re.DOTALL,
)

_DESCRIPTION_PATTERN = re.compile(
    r'<label\s+for="Description">.*?<div\s+class="editor-field"\s*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

# Supplier table: appears under "Successful Supplier(s)" label
_SUPPLIER_TABLE_PATTERN = re.compile(
    r'Successful\s+Supplier\(s\).*?<table>\s*<thead>.*?</thead>\s*<tbody>(.*?)</tbody>',
    re.IGNORECASE | re.DOTALL,
)
_TABLE_ROW_PATTERN = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL_PATTERN = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)

_NOT_FOUND_MARKER = "The contract has not been found"


def _strip_html(html: str) -> str:
    text = _TAG_PATTERN.sub(" ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def _parse_au_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_value(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    cleaned = re.sub(r"[,$\s]", "", raw.strip())
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(re.sub(r"\D", "", raw))
    except (ValueError, TypeError):
        return None


def _normalise_method(raw: str | None) -> str | None:
    if not raw:
        return None
    lower = raw.strip().lower()
    if "request for tender" in lower or "open" in lower or "rft" in lower:
        return "open"
    if "request for quote" in lower or "rfq" in lower or "select" in lower:
        return "selective"
    if "direct" in lower or "limited" in lower or "single" in lower:
        return "limited"
    return "limited"


def _extract_fields(html: str) -> dict[str, list[str]]:
    """Extract all ``<ul name="X"><li>v</li></ul>`` field blocks."""
    out: dict[str, list[str]] = {}
    for match in _FIELD_PATTERN.finditer(html):
        name = match.group(1)
        inner = match.group(2)
        items = [_strip_html(li.group(1)) for li in _LI_PATTERN.finditer(inner)]
        items = [i for i in items if i]
        if items:
            out[name] = items
    return out


def _extract_suppliers(html: str) -> list[dict]:
    """Parse the 'Successful Supplier(s)' table block."""
    match = _SUPPLIER_TABLE_PATTERN.search(html)
    if not match:
        return []
    tbody = match.group(1)

    suppliers: list[dict] = []
    for row_match in _TABLE_ROW_PATTERN.finditer(tbody):
        row_html = row_match.group(1)
        cells = [_strip_html(c.group(1)) for c in _TABLE_CELL_PATTERN.finditer(row_html)]
        if not cells:
            continue
        # Expected columns: business name, tasmanian flag, address, [allocated amount]
        supplier = {
            "name": cells[0] if len(cells) > 0 else "",
            "tasmanian": cells[1] if len(cells) > 1 else "",
            "address": cells[2] if len(cells) > 2 else "",
            "amount": cells[3] if len(cells) > 3 else "",
        }
        if supplier["name"]:
            suppliers.append(supplier)
    return suppliers


def _extract_description(html: str) -> str | None:
    match = _DESCRIPTION_PATTERN.search(html)
    if not match:
        return None
    return _strip_html(match.group(1)) or None


def _extract_period(html: str) -> tuple[datetime | None, datetime | None]:
    match = _PERIOD_PATTERN.search(html)
    if not match:
        return None, None
    return _parse_au_date(match.group(1)), _parse_au_date(match.group(2))


def is_not_found(html: str) -> bool:
    """Detect TAS 'contract not found' soft-404 response."""
    return _NOT_FOUND_MARKER in html


def parse_detail_html(html: str, contract_id: int) -> Release | None:
    """Parse a single TAS contract detail page into an OCDS Release."""
    if is_not_found(html):
        return None

    fields = _extract_fields(html)
    title = (fields.get("ProcurementTitle") or [""])[0]
    tender_id = (fields.get("UniqueTenderId") or [""])[0]
    method_raw = (fields.get("ProcurementMethod") or [""])[0]
    awarded_date_raw = (fields.get("AwardedDate") or [""])[0]
    agency_lines = fields.get("Agency") or []
    tasmanian_bids = _parse_int((fields.get("NumberOfTasmanianBidsReceived") or [""])[0])
    total_bids = _parse_int((fields.get("TotalNumberOfBidsReceived") or [""])[0])
    total_contract_value = _parse_value(
        (fields.get("TotalContractValue") or [""])[0]
    )

    if not title and not tender_id and not agency_lines:
        return None

    description = _extract_description(html)
    period_start, period_end = _extract_period(html)
    awarded_date = _parse_au_date(awarded_date_raw)
    suppliers = _extract_suppliers(html)

    agency_name = (agency_lines[0] if agency_lines else "Tasmania Government").strip()
    agency_address = " ".join(line.strip() for line in agency_lines[1:]) or None

    ocid = f"{OCID_PREFIX}-{re.sub(r'[^a-zA-Z0-9]+', '-', tender_id).strip('-') if tender_id else contract_id}"
    date = awarded_date or datetime.utcnow()
    release_id = f"{ocid}-award-{date.strftime('%Y%m%d')}-1"

    agency_slug = re.sub(r"[^a-z0-9]+", "-", agency_name.lower()).strip("-")
    buyer = Organization(
        id=f"{BUYER_ID_PREFIX}-{agency_slug}" if agency_slug else BUYER_ID_PREFIX,
        name=agency_name,
        roles=["buyer"],
        address=Address(streetAddress=agency_address) if agency_address else None,
    )

    supplier_orgs: list[Organization] = []
    for idx, s in enumerate(suppliers, start=1):
        name = s["name"]
        addr = s["address"] or None
        slug = hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:8]
        org = Organization(
            id=f"au-tas-supplier-{slug}",
            name=name,
            roles=["supplier"],
            address=Address(streetAddress=addr) if addr else None,
        )
        supplier_orgs.append(org)

    # Prefer supplier-row amount over single TotalContractValue if multiple suppliers.
    award_value: Value | None = None
    if total_contract_value is not None:
        award_value = Value(amount=total_contract_value)
    elif suppliers and suppliers[0]["amount"]:
        amt = _parse_value(suppliers[0]["amount"])
        if amt is not None:
            award_value = Value(amount=amt)

    period = (
        Period(startDate=period_start, endDate=period_end)
        if period_start or period_end
        else None
    )

    award = Award(
        id=f"{release_id}-a1",
        title=title or None,
        description=description,
        status="active",
        date=awarded_date,
        value=award_value,
        suppliers=supplier_orgs,
        contractPeriod=period,
    )

    contract = Contract(
        id=f"{release_id}-c1",
        awardID=award.id,
        title=title or None,
        status="active",
        value=award_value,
        dateSigned=awarded_date,
        period=period,
    )

    tender = Tender(
        id=f"{ocid}-tender",
        title=title or None,
        status="complete",
        procurementMethod=_normalise_method(method_raw),
        procurementMethodDetails=method_raw or None,
        numberOfTenderers=total_bids,
        value=award_value,
        contractPeriod=period,
    )

    source: dict = {"tasContractId": contract_id}
    if tender_id:
        source["uniqueTenderId"] = tender_id
    if tasmanian_bids is not None:
        source["tasmanianBidsReceived"] = tasmanian_bids
    if suppliers:
        flags = [s["tasmanian"] for s in suppliers]
        if any(f.lower() == "yes" for f in flags):
            source["hasTasmanianSupplier"] = True

    return Release(
        ocid=ocid,
        id=release_id,
        date=date,
        tag=["award"],
        buyer=buyer,
        tender=tender,
        awards=[award],
        contracts=[contract],
        source=source,
    )

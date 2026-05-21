"""
Transform NT QTOL (Quotations and Tenders Online) contract award HTML
into OCDS Releases.

Source: tendersonline.nt.gov.au/Tender/Details/{id}?status=Awarded

The NT portal renders ASP.NET pages with Bootstrap accordion sections.
Field locations are stable but not as label-driven as TAS:
- Tender number:      <p class="...leader">Tender number RFT0661</p>
- Title:              <div id="tenderTitle"><h1>...</h1></div>
- Description:        <div id="tenderDescription">...</div>
- Category:           <div class="general-badge"> after "Category:" label
- Award stepper:      <div class="stepper-info-cell active"> entries
- Agency:             <h3 class="fs-20"> inside light-border block
- Awarded block:      <div id="accordionAwardedListBody"> contains supplier
                      name, address, and award amount

Notable gap: NT does NOT publish supplier ABN. Suppliers identified by
business name and address only (same as TAS). NT does publish three useful
flags: Territory Enterprise, Aboriginal Enterprise, Women Owned.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from decimal import Decimal

from au_procurement.models.ocds import (
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

OCID_PREFIX = "ocau-nt"

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WS_PATTERN = re.compile(r"\s+")

_TENDER_NUMBER_PATTERN = re.compile(
    r'<p[^>]*class="[^"]*leader[^"]*"[^>]*>\s*Tender number\s+([^<\s]+)\s*</p>',
    re.IGNORECASE,
)

_TITLE_PATTERN = re.compile(
    r'<div[^>]*id="tenderTitle"[^>]*>\s*<h1[^>]*>(.*?)</h1>',
    re.IGNORECASE | re.DOTALL,
)

_DESCRIPTION_PATTERN = re.compile(
    r'<div[^>]*id="tenderDescription"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

_CATEGORY_PATTERN = re.compile(
    r'>Category:</p>.*?<div[^>]*class="general-badge[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

_PROC_METHOD_PATTERN = re.compile(
    r'>Procurement method:</p>.*?<div[^>]*class="general-badge[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

_AWARD_DATE_PATTERN = re.compile(
    r'<div[^>]*class="stepper-info-cell[^"]*"[^>]*>\s*'
    r'<p[^>]*>Award</p>\s*<p[^>]*>(\d{1,2}/\d{1,2}/\d{4})</p>',
    re.IGNORECASE | re.DOTALL,
)

_RELEASE_DATE_PATTERN = re.compile(
    r'<div[^>]*class="stepper-info-cell[^"]*"[^>]*>\s*'
    r'<p[^>]*>Release</p>\s*<p[^>]*>(\d{1,2}/\d{1,2}/\d{4})</p>',
    re.IGNORECASE | re.DOTALL,
)

_LISTED_BY_PATTERN = re.compile(
    r'<div[^>]*class="light-border[^"]*"[^>]*>.*?<h3[^>]*>(.*?)</h3>\s*'
    r'(?:<p[^>]*>(.*?)</p>)?',
    re.IGNORECASE | re.DOTALL,
)

_AWARD_BODY_PATTERN = re.compile(
    r'<div\s+id="accordionAwardedListBody"[^>]*>(.*?)</div>\s*</div>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)

_AWARD_AMOUNT_PATTERN = re.compile(
    r'<p[^>]*class="fw-bold[^"]*"[^>]*>Awarded</p>\s*<p[^>]*>\s*<span[^>]*>\s*\$?\s*([0-9,.\s]+)',
    re.IGNORECASE | re.DOTALL,
)

_AWARD_AMOUNT_ALT_PATTERN = re.compile(
    r'<span[^>]*>\s*\$\s*([0-9][0-9,]*\.?\d*)',
    re.IGNORECASE,
)

_SUPPLIER_NAME_PATTERN = re.compile(
    r'<p[^>]*class="fw-bold\s+m-0"[^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)

_SUPPLIER_DETAIL_PATTERN = re.compile(
    r'<p[^>]*class="m-0(?:\s+font-color-supporting-2)?"[^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)


def _strip(html: str) -> str:
    text = _TAG_PATTERN.sub(" ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return _WS_PATTERN.sub(" ", text).strip()


def _parse_au_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y")
    except ValueError:
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


def _normalise_method(raw: str | None) -> str | None:
    if not raw:
        return None
    lower = raw.strip().lower()
    if "public" in lower or "open" in lower:
        return "open"
    if "select" in lower or "panel" in lower:
        return "selective"
    if "limited" in lower or "single" in lower or "direct" in lower:
        return "limited"
    return None


def _extract_agency(html: str) -> tuple[str, str | None]:
    match = _LISTED_BY_PATTERN.search(html)
    if not match:
        return "Northern Territory Government", None
    name = _strip(match.group(1))
    address = _strip(match.group(2) or "") or None
    return (name or "Northern Territory Government"), address


def _extract_award_amount(award_body: str) -> Decimal | None:
    match = _AWARD_AMOUNT_PATTERN.search(award_body)
    if match:
        return _parse_value(match.group(1))
    # Fallback - first dollar amount in the block.
    match = _AWARD_AMOUNT_ALT_PATTERN.search(award_body)
    if match:
        return _parse_value(match.group(1))
    return None


def _extract_supplier(award_body: str) -> dict | None:
    name_match = _SUPPLIER_NAME_PATTERN.search(award_body)
    if not name_match:
        return None
    name = _strip(name_match.group(1))

    details = [_strip(m.group(1)) for m in _SUPPLIER_DETAIL_PATTERN.finditer(award_body)]
    address = None
    flags: dict[str, bool] = {}
    for detail in details:
        if not detail:
            continue
        if detail.startswith("Territory Enterprise:"):
            flags["isTerritoryEnterprise"] = detail.endswith("Yes")
        elif detail.startswith("Aboriginal Enterprise:"):
            flags["isAboriginalEnterprise"] = detail.endswith("Yes")
        elif detail.startswith("Women Owned:"):
            if detail.endswith("Yes"):
                flags["isWomenOwned"] = True
            elif detail.endswith("No"):
                flags["isWomenOwned"] = False
        elif address is None and not detail.startswith(("Territory", "Aboriginal", "Women")):
            address = detail

    return {"name": name, "address": address, "flags": flags}


def is_not_found(html: str) -> bool:
    """Detect NT 'tender not found' soft-404."""
    return "Tender not found" in html or "no tender was found" in html.lower()


def parse_detail_html(html: str, contract_id: int) -> Release | None:
    if is_not_found(html):
        return None

    tender_num_match = _TENDER_NUMBER_PATTERN.search(html)
    title_match = _TITLE_PATTERN.search(html)

    tender_number = (tender_num_match.group(1).strip() if tender_num_match else "").strip()
    title = _strip(title_match.group(1)) if title_match else ""

    if not tender_number and not title:
        return None

    description_match = _DESCRIPTION_PATTERN.search(html)
    description = _strip(description_match.group(1)) if description_match else None

    category_match = _CATEGORY_PATTERN.search(html)
    category = _strip(category_match.group(1)) if category_match else None

    method_match = _PROC_METHOD_PATTERN.search(html)
    method_raw = _strip(method_match.group(1)) if method_match else None

    awarded_date = None
    award_date_match = _AWARD_DATE_PATTERN.search(html)
    if award_date_match:
        awarded_date = _parse_au_date(award_date_match.group(1))
    release_date_match = _RELEASE_DATE_PATTERN.search(html)
    release_date_obj = (
        _parse_au_date(release_date_match.group(1)) if release_date_match else None
    )

    agency_name, agency_address = _extract_agency(html)

    award_value: Decimal | None = None
    supplier_info: dict | None = None
    body_match = _AWARD_BODY_PATTERN.search(html)
    if body_match:
        award_body = body_match.group(1)
        award_value = _extract_award_amount(award_body)
        supplier_info = _extract_supplier(award_body)

    ocid = f"{OCID_PREFIX}-{re.sub(r'[^a-zA-Z0-9]+', '-', tender_number).strip('-') if tender_number else contract_id}"
    date = awarded_date or release_date_obj or datetime.utcnow()
    release_id = f"{ocid}-award-{date.strftime('%Y%m%d')}-1"

    agency_slug = re.sub(r"[^a-z0-9]+", "-", agency_name.lower()).strip("-")
    buyer = Organization(
        id=f"au-nt-agency-{agency_slug}",
        name=agency_name,
        roles=["buyer"],
        address=Address(streetAddress=agency_address) if agency_address else None,
    )

    supplier_orgs: list[Organization] = []
    if supplier_info:
        name = supplier_info["name"]
        slug = hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:8]
        supplier_orgs.append(
            Organization(
                id=f"au-nt-supplier-{slug}",
                name=name,
                roles=["supplier"],
                address=Address(streetAddress=supplier_info["address"])
                if supplier_info["address"]
                else None,
            )
        )

    value = Value(amount=award_value) if award_value is not None else None

    award = Award(
        id=f"{release_id}-a1",
        title=title or None,
        description=description,
        status="active",
        date=awarded_date,
        value=value,
        suppliers=supplier_orgs,
    )

    contract = Contract(
        id=f"{release_id}-c1",
        awardID=award.id,
        title=title or None,
        status="active",
        value=value,
        dateSigned=awarded_date,
    )

    tender = Tender(
        id=f"{ocid}-tender",
        title=title or None,
        status="complete",
        procurementMethod=_normalise_method(method_raw),
        procurementMethodDetails=method_raw,
        value=value,
    )

    source: dict = {"ntContractId": contract_id}
    if tender_number:
        source["tenderNumber"] = tender_number
    if category:
        source["category"] = category
    if supplier_info:
        source.update(supplier_info.get("flags", {}))

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

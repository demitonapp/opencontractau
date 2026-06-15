"""
Map TenderSearch DetailFields into OCDS Releases.

The TenderSearch platform is a Java product deployed across multiple
jurisdictions with consistent field labels. The same transformer therefore
handles VIC, SA, WA and any future TenderSearch state.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from decimal import Decimal

from opencontractsau.models.ocds import (
    Address,
    Award,
    Contract,
    Identifier,
    Organization,
    Period,
    Release,
    Tender,
    Value,
)
from opencontractsau.scrapers._tendersearch.parser import DetailFields, Supplier

logger = logging.getLogger(__name__)

_MONTH_DATE = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$")


def _parse_date(raw: str | None) -> datetime | None:
    """TenderSearch uses '7 Mar 2027' style dates."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_value(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    cleaned = re.sub(r"[,$\s]", "", raw.split("(")[0].strip())
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _clean_abn(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 else None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


_METHOD_HINTS = (
    ("open", "open"),
    ("request for tender", "open"),
    ("rft", "open"),
    ("selective", "selective"),
    ("panel", "selective"),
    ("standing offer", "selective"),
    ("request for quote", "selective"),
    ("rfq", "selective"),
    ("limited", "limited"),
    ("single source", "limited"),
    ("direct", "limited"),
    ("exemption", "limited"),
)


def _normalise_method(raw: str | None) -> str | None:
    if not raw:
        return None
    lower = raw.strip().lower()
    for needle, mapped in _METHOD_HINTS:
        if needle in lower:
            return mapped
    return None


def detail_to_release(
    detail: DetailFields,
    contract_id: int,
    jurisdiction_code: str,
) -> Release | None:
    """
    Map a parsed TenderSearch detail page into an OCDS Release.

    Args:
        detail: parsed fields from ``parse_detail_html``.
        contract_id: numeric TenderSearch contract id (the URL-path ID).
        jurisdiction_code: lowercase short code, e.g. "vic", "sa".
    """
    contract_number = detail.get("Contract Number", "Contract Code") or ""
    title = detail.get("Title") or ""
    public_body = detail.get("Public Body", "Department", "Agency") or ""
    type_label = detail.get("Type", "Contract Type") or None
    description = detail.get("Description") or None
    unspsc = detail.get("UNSPSC") or None
    method_raw = (
        detail.get("Procurement Method")
        or detail.get("Type/Procurement Method")
        or type_label
    )

    value_raw = (
        detail.get(
            "Total Value of the Contract",
            "Contract Value",
            "Value (excl GST)",
            "Value",
        )
        or ""
    )
    contract_value = _parse_value(value_raw)
    value_is_estimate = "estimate" in value_raw.lower()

    start_date = _parse_date(detail.get("Starting Date", "Commence Date", "Start Date"))
    end_date = _parse_date(detail.get("Expiry Date", "Finish Date", "End Date"))
    awarded_date = _parse_date(
        detail.get("Awarded Date", "Award Date", "Contract Award Date")
    )

    if not title and not contract_number and not public_body:
        return None

    ocid_prefix = f"ocau-{jurisdiction_code}"
    if contract_number:
        safe = re.sub(r"[^a-zA-Z0-9]+", "-", contract_number).strip("-")
        ocid = f"{ocid_prefix}-{safe}"
    else:
        ocid = f"{ocid_prefix}-{contract_id}"

    date = awarded_date or start_date or datetime.utcnow()
    release_id = f"{ocid}-award-{date.strftime('%Y%m%d')}-1"

    buyer_name = public_body or f"{jurisdiction_code.upper()} Government"
    buyer = Organization(
        id=f"au-{jurisdiction_code}-{_slug(buyer_name) or 'agency'}",
        name=buyer_name,
        roles=["buyer"],
    )

    supplier_orgs: list[Organization] = []
    for idx, s in enumerate(detail.suppliers, start=1):
        abn = _clean_abn(s.abn)
        identifier = (
            Identifier(scheme="AU-ABN", id=abn, legalName=s.name)
            if abn
            else (
                Identifier(scheme="AU-ACN", id=s.acn.strip(), legalName=s.name)
                if s.acn
                else None
            )
        )
        slug = (
            f"au-abn-{abn}"
            if abn
            else f"au-{jurisdiction_code}-supplier-{hashlib.sha1(s.name.encode(), usedforsecurity=False).hexdigest()[:8]}"
        )
        supplier_orgs.append(
            Organization(
                id=slug,
                name=s.name,
                identifier=identifier,
                roles=["supplier"],
                address=Address(streetAddress=s.address) if s.address else None,
            )
        )

    award_value = Value(amount=contract_value) if contract_value is not None else None
    period = (
        Period(startDate=start_date, endDate=end_date)
        if (start_date or end_date)
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
        procurementMethodDetails=method_raw,
        value=award_value,
        contractPeriod=period,
    )

    source: dict = {
        "tenderSearchId": contract_id,
        "jurisdiction": jurisdiction_code,
    }
    if contract_number:
        source["contractNumber"] = contract_number
    if unspsc:
        source["unspscDescription"] = unspsc
    if value_is_estimate:
        source["valueIsEstimate"] = True
    if type_label:
        source["contractType"] = type_label

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

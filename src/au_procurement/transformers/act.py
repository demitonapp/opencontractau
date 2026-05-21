"""
Transform ACT Contracts Register (Socrata dataset pfs5-8d64) into OCDS Releases.

Fields confirmed live (2026-05-21):
    contract_number, contract_title, brief_description_of_contract,
    contract_type, procurement_methodology, social_procurement,
    procurement_type, contractor_name, abn, original_amount,
    contract_amount, gst, execution_date, expiry_date, confidential_text,
    confidential_text_brief, unspsc_classification, directorate,
    prequalification_requested, infrastructure_or_goods_services,
    small_to_medium_enterprise, whole_of_government_contract,
    exemption_from_quotation, active_certification, ire_obligations

Source: https://www.data.act.gov.au/resource/pfs5-8d64.json
Threshold: $25,000 (ACT Government Procurement Act 2001)
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from au_procurement.models.ocds import (
    Award,
    Contract,
    Identifier,
    Organization,
    Period,
    Release,
    Tender,
    Value,
)

logger = logging.getLogger(__name__)

OCID_PREFIX = "ocau-act"
BUYER_ID_PREFIX = "au-act-directorate"

_METHOD_MAP = (
    ("standing offer", "selective"),
    ("single select", "limited"),
    ("single", "limited"),
    ("exemption", "limited"),
    ("limited", "limited"),
    ("panel", "selective"),
    ("selective", "selective"),
    ("select", "selective"),
    ("open", "open"),
)


def _normalise_method(raw: str | None) -> str | None:
    if not raw:
        return None
    lower = raw.strip().lower()
    for key, mapped in _METHOD_MAP:
        if key in lower:
            return mapped
    return "limited"


def _clean_abn(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 else None


def _parse_socrata_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_amount(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    try:
        return Decimal(raw.strip())
    except (InvalidOperation, AttributeError):
        return None


def _parse_unspsc(raw: str | None) -> tuple[str | None, str | None]:
    """Extract UNSPSC code + label from 'Description(UNSPSC:12345678)'."""
    if not raw:
        return None, None
    match = re.search(r"UNSPSC:\s*(\d{6,8})", raw)
    code = match.group(1) if match else None
    label = re.sub(r"\s*\(UNSPSC:[^)]+\)", "", raw).strip() or None
    return code, label


def _agency_id(directorate: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", directorate.lower()).strip("-")
    return f"{BUYER_ID_PREFIX}-{slug}" if slug else BUYER_ID_PREFIX


def _make_ocid(contract_number: str | None, fallback: str) -> str:
    if contract_number and contract_number.strip():
        safe = re.sub(r"[^a-zA-Z0-9]+", "-", contract_number.strip()).strip("-")
        return f"{OCID_PREFIX}-{safe}"
    fingerprint = hashlib.sha1(fallback.encode(), usedforsecurity=False).hexdigest()[:12]
    return f"{OCID_PREFIX}-{fingerprint}"


def record_to_release(record: dict, seq: int = 1) -> Release | None:
    contract_number = (record.get("contract_number") or "").strip() or None
    title = (record.get("contract_title") or "").strip()
    description = (record.get("brief_description_of_contract") or "").strip() or None
    contractor = (record.get("contractor_name") or "").strip()
    directorate = (record.get("directorate") or "ACT Government").strip()
    abn = _clean_abn(record.get("abn"))

    execution_date = _parse_socrata_datetime(record.get("execution_date"))
    expiry_date = _parse_socrata_datetime(record.get("expiry_date"))

    original_amount = _parse_amount(record.get("original_amount"))
    contract_amount = _parse_amount(record.get("contract_amount"))

    method_raw = (record.get("procurement_methodology") or "").strip() or None
    contract_type_raw = (record.get("contract_type") or "").strip().lower()
    unspsc_code, _unspsc_label = _parse_unspsc(record.get("unspsc_classification"))

    if not contract_number and not title and not contractor:
        return None

    ocid = _make_ocid(contract_number, f"{directorate}|{title}|{contractor}")
    date = execution_date or datetime.utcnow()
    release_id = f"{ocid}-award-{date.strftime('%Y%m%d')}-{seq}"

    is_variation = "variation" in contract_type_raw
    tag = ["awardUpdate"] if is_variation else ["award"]

    buyer = Organization(
        id=_agency_id(directorate),
        name=directorate,
        roles=["buyer"],
    )

    supplier_identifier = (
        Identifier(scheme="AU-ABN", id=abn, legalName=contractor or None)
        if abn
        else None
    )
    supplier = Organization(
        id=f"au-abn-{abn}" if abn else f"au-act-supplier-{hashlib.sha1(contractor.encode(), usedforsecurity=False).hexdigest()[:8]}",
        name=contractor or "Unknown supplier",
        identifier=supplier_identifier,
        roles=["supplier"],
    )

    period = Period(startDate=execution_date, endDate=expiry_date) if execution_date or expiry_date else None
    award_value = Value(amount=contract_amount) if contract_amount is not None else None
    tender_value = Value(amount=original_amount or contract_amount) if (original_amount or contract_amount) else None

    award = Award(
        id=f"{release_id}-a1",
        title=title or None,
        description=description,
        status="active",
        date=execution_date,
        value=award_value,
        suppliers=[supplier],
        contractPeriod=period,
    )

    contract = Contract(
        id=f"{release_id}-c1",
        awardID=award.id,
        title=title or None,
        status="active",
        value=award_value,
        dateSigned=execution_date,
        period=period,
    )

    tender = Tender(
        id=f"{ocid}-tender",
        title=title or None,
        status="complete",
        procurementMethod=_normalise_method(method_raw),
        procurementMethodDetails=method_raw,
        value=tender_value,
        contractPeriod=period,
    )

    source: dict = {}
    if contract_number:
        source["contractNumber"] = contract_number
    if unspsc_code:
        source["unspscCode"] = unspsc_code
    if (record.get("small_to_medium_enterprise") or "").strip().lower() == "yes":
        source["isSME"] = True
    if (record.get("whole_of_government_contract") or "").strip().lower() == "yes":
        source["isWholeOfGovernment"] = True
    if (record.get("social_procurement") or "").strip().lower() == "yes":
        source["isSocialProcurement"] = True
    if is_variation:
        source["isVariation"] = True
    gst = (record.get("gst") or "").strip()
    if gst:
        source["gst"] = gst

    return Release(
        ocid=ocid,
        id=release_id,
        date=date,
        tag=tag,
        buyer=buyer,
        tender=tender,
        awards=[award],
        contracts=[contract],
        source=source,
    )

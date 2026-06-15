"""
Transform QLD TMR contract disclosure CSV rows into OCDS Release objects.

QLD CSV columns (exact header names from the dataset):
    Agency (Dept or Stat Body), Agency address, Contract description/name,
    Award contract date, Contract value, Supplier name, Supplier address,
    Variation, Specific confidentiality provision used, Procurement method,
    Justification, Form of contract, Number of offers sought,
    Evaluation criteria and weightings, Deliverables, Contract milestones,
    Contract performance management, Contract reference number,
    Contract category group, Parent contract number/SOA,
    Commence date, Supplier ABN, Finish date
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from opencontractau.models.ocds import (
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

logger = logging.getLogger(__name__)

OCID_PREFIX = "ocau-qld-tmr"
BUYER_ID = "au-qld-tmr"
BUYER_NAME = "Queensland Department of Transport and Main Roads"


def _clean_abn(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 else None


def _parse_au_date(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    logger.debug("Unparseable date: %r", raw)
    return None


def _parse_value(raw: str | None) -> Decimal | None:
    if not raw or not raw.strip():
        return None
    cleaned = re.sub(r"[,$\s]", "", raw.strip())
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_int(raw: str | None) -> int | None:
    if not raw or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _normalize_procurement_method(raw: str | None) -> str | None:
    if not raw:
        return None
    mapping = {
        "open": "open",
        "selective": "selective",
        "limited": "limited",
        "direct": "limited",
        "sole source": "limited",
        "quotation": "limited",
        "standing offer": "selective",
        "panel": "selective",
    }
    lower = raw.strip().lower()
    for key, method in mapping.items():
        if key in lower:
            return method
    return "limited"


def _make_ocid(contract_ref: str | None, agency: str, description: str) -> str:
    if contract_ref and contract_ref.strip():
        safe_ref = re.sub(r"[^a-zA-Z0-9\-]", "-", contract_ref.strip())
        safe_ref = re.sub(r"-+", "-", safe_ref).strip("-")
        return f"{OCID_PREFIX}-{safe_ref}"
    fingerprint = hashlib.sha1(
        f"{agency}|{description}".encode(), usedforsecurity=False
    ).hexdigest()[:12]
    return f"{OCID_PREFIX}-{fingerprint}"


def _make_release_id(ocid: str, award_date: datetime | None, seq: int = 1) -> str:
    date_str = award_date.strftime("%Y%m%d") if award_date else "unknown"
    return f"{ocid}-award-{date_str}-{seq}"


def row_to_release(row: dict[str, str], seq: int = 1) -> Release | None:
    agency = (row.get("Agency (Dept or Stat Body)") or "").strip()
    description = (row.get("Contract description/name") or "").strip()
    contract_ref = (row.get("Contract reference number") or "").strip() or None
    award_date = _parse_au_date(row.get("Award contract date"))
    commence_date = _parse_au_date(row.get("Commence date"))
    finish_date = _parse_au_date(row.get("Finish date"))
    contract_value = _parse_value(row.get("Contract value"))
    supplier_name = (row.get("Supplier name") or "").strip()
    supplier_abn = _clean_abn(row.get("Supplier ABN"))
    supplier_address_raw = (row.get("Supplier address") or "").strip()
    agency_address_raw = (row.get("Agency address") or "").strip()
    procurement_method_raw = (row.get("Procurement method") or "").strip()
    justification = (row.get("Justification") or "").strip() or None
    num_offers = _parse_int(row.get("Number of offers sought"))
    category = (row.get("Contract category group") or "").strip() or None

    if not agency and not description and not supplier_name:
        return None

    ocid = _make_ocid(contract_ref, agency, description)
    release_id = _make_release_id(ocid, award_date, seq)
    release_date = award_date or datetime.utcnow()

    buyer = Organization(
        id=BUYER_ID,
        name=agency or BUYER_NAME,
        roles=["buyer"],
        address=Address(streetAddress=agency_address_raw or None) if agency_address_raw else None,
    )

    supplier_identifier = None
    if supplier_abn:
        supplier_identifier = Identifier(
            scheme="AU-ABN",
            id=supplier_abn,
            legalName=supplier_name or None,
        )

    supplier = Organization(
        id=f"au-abn-{supplier_abn}" if supplier_abn else f"au-name-{hashlib.sha1(supplier_name.encode(), usedforsecurity=False).hexdigest()[:8]}",
        name=supplier_name or "Unknown supplier",
        identifier=supplier_identifier,
        roles=["supplier"],
        address=Address(streetAddress=supplier_address_raw or None) if supplier_address_raw else None,
    )

    contract_period = Period(
        startDate=commence_date,
        endDate=finish_date,
    ) if commence_date or finish_date else None

    award_value = Value(amount=contract_value) if contract_value is not None else None

    award = Award(
        id=f"{release_id}-a1",
        title=description or None,
        status="active",
        date=award_date,
        value=award_value,
        suppliers=[supplier] if supplier_name else [],
        contractPeriod=contract_period,
    )

    contract = Contract(
        id=f"{release_id}-c1",
        awardID=award.id,
        title=description or None,
        status="active",
        value=award_value,
        dateSigned=award_date,
        period=contract_period,
    )

    tender = Tender(
        id=f"{ocid}-tender",
        title=description or None,
        status="complete",
        procurementMethod=_normalize_procurement_method(procurement_method_raw),
        procurementMethodDetails=procurement_method_raw or None,
        procurementMethodRationale=justification,
        numberOfTenderers=num_offers,
        value=Value(amount=contract_value) if contract_value is not None else None,
        contractPeriod=contract_period,
    )

    source: dict = {}
    if contract_ref:
        source["contractReference"] = contract_ref
    if category:
        source["categoryGroup"] = category
    parent_ref = (row.get("Parent contract number/SOA") or "").strip()
    if parent_ref:
        source["parentContractReference"] = parent_ref
    if (row.get("Variation") or "").strip().lower() == "yes":
        source["isVariation"] = True

    return Release(
        ocid=ocid,
        id=release_id,
        date=release_date,
        tag=["award"],
        buyer=buyer,
        tender=tender,
        awards=[award],
        contracts=[contract],
        source=source,
    )

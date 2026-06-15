"""
Generic transformer for Queensland agency contract disclosure CSVs.

The Queensland Procurement Policy mandates a common set of disclosure fields,
but agencies export from different tools (Excel, Power BI, custom dashboards).
This module normalises header variations across agencies into the same
canonical OCDS Release shape.

For TMR specifically, prefer ``transformers/qld.py`` which has a fixed
schema. This generic version is used by ``scrapers/qld/ckan.py`` to harvest
every other Queensland agency that publishes a contract disclosure dataset
on data.qld.gov.au.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

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
from opencontractsau.transformers.qld import (
    _clean_abn,
    _normalize_procurement_method,
    _parse_au_date,
    _parse_int,
    _parse_value,
)

logger = logging.getLogger(__name__)

OCID_PREFIX_TEMPLATE = "ocau-qld-{agency}"


COLUMN_ALIASES: dict[str, list[str]] = {
    "agency": [
        "Agency (Dept or Stat Body)",
        "Agency (Dept or Stat Body)2",
        "Agency",
        "Department",
        "Agency / Entity",
    ],
    "agency_address": [
        "Agency address",
        "Agency Address",
        "Agency Address2",
    ],
    "description": [
        "Contract description/name",
        "Contract description",
        "Description (Previously Used or Cost Element Category)",
        "Contract title",
        "Description",
    ],
    "award_date": [
        "Award contract date",
        "Award date",
        "Contract award date",
        "Date awarded",
    ],
    "value": [
        "Contract value",
        " '_Measures'[Contract value] ",
        "'_Measures'[Contract value]",
        "Contract Value",
        "Value (excl GST)",
        "Value",
        "Contract amount",
    ],
    "supplier_name": [
        "Supplier name",
        "Supplier Name",
        "Contractor name",
        "Supplier",
    ],
    "supplier_address": [
        "Supplier address",
        "Supplier Address",
    ],
    "variation": [
        "Variation",
        "Variation to contract (Yes/No)",
        "Variation to contract",
    ],
    "confidentiality": [
        "Specific confidentiality provision used",
        "Confidentiality",
    ],
    "procurement_method": [
        "Procurement method",
        "Procurement Method",
        "Purchase method",
    ],
    "justification": [
        "Justification",
        "Reason for use of limited tendering",
        "Reason for limited tendering",
    ],
    "form_of_contract": [
        "Form of contract",
        "Form of Contract",
    ],
    "num_offers": [
        "Number of offers sought",
        "Number of offers",
    ],
    "contract_ref": [
        "Contract reference number",
        "Contract reference",
        "Contract number",
        "Reference number",
    ],
    "category": [
        "Contract category group",
        "Category",
    ],
    "parent_contract": [
        "Parent contract number/SOA",
        "Parent contract",
        "SOA",
    ],
    "commence_date": [
        "Commence date",
        "Commencement date",
        "Contract start date",
        "Start date",
    ],
    "supplier_abn": [
        "Supplier ABN",
        "Supplier Abn",
        "ABN",
    ],
    "finish_date": [
        "Finish date",
        "Expiry date",
        "Contract end date",
        "End date",
    ],
}


def _normalise_header(raw: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation noise."""
    if not raw:
        return ""
    cleaned = raw.strip().strip("'\"").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.lower()


def build_column_map(headers: list[str]) -> dict[str, str]:
    """
    Map canonical field names to the first matching header in the CSV.

    Returns ``{canonical_field: actual_header_string}``. Canonical fields
    with no matching header are absent from the returned dict (callers
    should treat as None).
    """
    normalised_headers = {_normalise_header(h): h for h in headers if h}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            norm_alias = _normalise_header(alias)
            if norm_alias in normalised_headers:
                mapping[canonical] = normalised_headers[norm_alias]
                break
    return mapping


def _get(row: dict, column_map: dict[str, str], field: str) -> str | None:
    header = column_map.get(field)
    if not header:
        return None
    return row.get(header)


def row_to_release(
    row: dict[str, str],
    column_map: dict[str, str],
    agency_code: str,
    default_agency_name: str,
    seq: int = 1,
) -> Release | None:
    agency_raw = (_get(row, column_map, "agency") or "").strip()
    agency = agency_raw or default_agency_name
    description = (_get(row, column_map, "description") or "").strip()
    contract_ref = (_get(row, column_map, "contract_ref") or "").strip() or None
    award_date = _parse_au_date(_get(row, column_map, "award_date"))
    commence_date = _parse_au_date(_get(row, column_map, "commence_date"))
    finish_date = _parse_au_date(_get(row, column_map, "finish_date"))
    contract_value = _parse_value(_get(row, column_map, "value"))
    supplier_name = (_get(row, column_map, "supplier_name") or "").strip()
    supplier_abn = _clean_abn(_get(row, column_map, "supplier_abn"))
    supplier_addr = (_get(row, column_map, "supplier_address") or "").strip()
    agency_addr = (_get(row, column_map, "agency_address") or "").strip()
    procurement_method_raw = (_get(row, column_map, "procurement_method") or "").strip()
    justification = (_get(row, column_map, "justification") or "").strip() or None
    num_offers = _parse_int(_get(row, column_map, "num_offers"))
    category = (_get(row, column_map, "category") or "").strip() or None
    parent_ref = (_get(row, column_map, "parent_contract") or "").strip() or None
    variation_flag = (_get(row, column_map, "variation") or "").strip().lower()

    if not agency_raw and not description and not supplier_name:
        return None

    ocid_prefix = OCID_PREFIX_TEMPLATE.format(agency=agency_code)
    if contract_ref:
        safe = re.sub(r"[^a-zA-Z0-9]+", "-", contract_ref).strip("-")
        ocid = f"{ocid_prefix}-{safe}"
    else:
        fingerprint = hashlib.sha1(
            f"{agency}|{description}|{supplier_name}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:12]
        ocid = f"{ocid_prefix}-{fingerprint}"

    date = award_date or datetime.utcnow()
    release_id = f"{ocid}-award-{date.strftime('%Y%m%d')}-{seq}"

    buyer = Organization(
        id=f"au-qld-{agency_code}",
        name=agency or default_agency_name,
        roles=["buyer"],
        address=Address(streetAddress=agency_addr) if agency_addr else None,
    )

    supplier_identifier = (
        Identifier(scheme="AU-ABN", id=supplier_abn, legalName=supplier_name or None)
        if supplier_abn
        else None
    )
    supplier_id_suffix = (
        f"au-abn-{supplier_abn}"
        if supplier_abn
        else f"au-qld-{agency_code}-supplier-{hashlib.sha1(supplier_name.encode(), usedforsecurity=False).hexdigest()[:8]}"
    )
    supplier = Organization(
        id=supplier_id_suffix,
        name=supplier_name or "Unknown supplier",
        identifier=supplier_identifier,
        roles=["supplier"],
        address=Address(streetAddress=supplier_addr) if supplier_addr else None,
    )

    period = (
        Period(startDate=commence_date, endDate=finish_date)
        if commence_date or finish_date
        else None
    )
    award_value = Value(amount=contract_value) if contract_value is not None else None

    award = Award(
        id=f"{release_id}-a1",
        title=description or None,
        status="active",
        date=award_date,
        value=award_value,
        suppliers=[supplier] if supplier_name else [],
        contractPeriod=period,
    )

    contract = Contract(
        id=f"{release_id}-c1",
        awardID=award.id,
        title=description or None,
        status="active",
        value=award_value,
        dateSigned=award_date,
        period=period,
    )

    tender = Tender(
        id=f"{ocid}-tender",
        title=description or None,
        status="complete",
        procurementMethod=_normalize_procurement_method(procurement_method_raw),
        procurementMethodDetails=procurement_method_raw or None,
        procurementMethodRationale=justification,
        numberOfTenderers=num_offers,
        value=award_value,
        contractPeriod=period,
    )

    source: dict = {}
    if contract_ref:
        source["contractReference"] = contract_ref
    if category:
        source["categoryGroup"] = category
    if parent_ref:
        source["parentContractReference"] = parent_ref
    if variation_flag == "yes":
        source["isVariation"] = True

    return Release(
        ocid=ocid,
        id=release_id,
        date=date,
        tag=["awardUpdate"] if source.get("isVariation") else ["award"],
        buyer=buyer,
        tender=tender,
        awards=[award],
        contracts=[contract],
        source=source,
    )

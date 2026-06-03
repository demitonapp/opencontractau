"""
Shared transformer for QLD local government council contract registers.

Council registers disclose supplier name, value, and date but NOT ABN.
The identifier on the supplier Organization is set to None; Demiton resolves
names to ABNs via ABR search_by_name after ingestion.

Mirrors the shape of transformers/qld.py but with council-specific OCID
prefixes and a normalized intermediate dataclass.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from opencontractau.models.ocds import (
    Award,
    Contract,
    Organization,
    Period,
    Release,
    Tender,
    Value,
)

logger = logging.getLogger(__name__)

# Maps council_key -> OCID prefix
_OCID_PREFIXES: dict[str, str] = {
    # QLD SEQ councils
    "BCC":             "ocau-qld-bcc",
    "GC_COUNCIL":      "ocau-qld-gc",
    "LOGAN_COUNCIL":   "ocau-qld-logan",
    "MORETON_BAY":     "ocau-qld-mbrc",
    "IPSWICH_COUNCIL": "ocau-qld-icc",
    "SCENIC_RIM":      "ocau-qld-srrc",
    # NSW councils
    "SYDNEY_COUNCIL":   "ocau-nsw-sydney",
    "NORTHERN_BEACHES": "ocau-nsw-nb",
    "WOLLONGONG":       "ocau-nsw-wollongong",
    "BLACKTOWN":        "ocau-nsw-blacktown",
    "CUMBERLAND":       "ocau-nsw-cumberland",
    "LIVERPOOL_NSW":    "ocau-nsw-liverpool",
}

_BUYER_IDS: dict[str, str] = {
    # QLD SEQ councils
    "BCC":             "au-qld-bcc",
    "GC_COUNCIL":      "au-qld-gold-coast",
    "LOGAN_COUNCIL":   "au-qld-logan",
    "MORETON_BAY":     "au-qld-mbrc",
    "IPSWICH_COUNCIL": "au-qld-icc",
    "SCENIC_RIM":      "au-qld-srrc",
    # NSW councils
    "SYDNEY_COUNCIL":   "au-nsw-sydney",
    "NORTHERN_BEACHES": "au-nsw-northern-beaches",
    "WOLLONGONG":       "au-nsw-wollongong",
    "BLACKTOWN":        "au-nsw-blacktown",
    "CUMBERLAND":       "au-nsw-cumberland",
    "LIVERPOOL_NSW":    "au-nsw-liverpool",
}


@dataclass
class CouncilContractRow:
    """Normalized intermediate representation for a council contract record."""

    council_key: str
    council_name: str
    awarded_to: str
    title: str
    reference: Optional[str] = None
    value_aud: Optional[Decimal] = None
    award_date: Optional[datetime] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    description: Optional[str] = None
    procurement_method: Optional[str] = None


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def parse_au_date(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    for fmt in ("%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%B %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    logger.debug("council: unparseable date %r", raw)
    return None


def parse_value(raw: str | None) -> Decimal | None:
    if not raw or not raw.strip():
        return None
    cleaned = re.sub(r"[,$\s]", "", raw.strip().lstrip("$"))
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def normalize_method(raw: str | None) -> str | None:
    if not raw:
        return None
    mapping = {
        "open": "open",
        "selective": "selective",
        "limited": "limited",
        "direct": "limited",
        "sole": "limited",
        "quote": "limited",
        "panel": "selective",
        "standing": "selective",
    }
    lower = raw.strip().lower()
    for key, method in mapping.items():
        if key in lower:
            return method
    return "limited"


# ---------------------------------------------------------------------------
# OCID / release-id builders
# ---------------------------------------------------------------------------


def _make_ocid(council_key: str, reference: str | None, title: str, supplier: str) -> str:
    prefix = _OCID_PREFIXES.get(council_key, f"ocau-au-{council_key.lower()}")
    if reference and reference.strip():
        safe = re.sub(r"[^a-zA-Z0-9\-]", "-", reference.strip())
        safe = re.sub(r"-+", "-", safe).strip("-")
        return f"{prefix}-{safe}"
    fingerprint = hashlib.sha1(
        f"{title}|{supplier}".encode(), usedforsecurity=False
    ).hexdigest()[:12]
    return f"{prefix}-{fingerprint}"


def _make_release_id(ocid: str, award_date: datetime | None, seq: int) -> str:
    date_str = award_date.strftime("%Y%m%d") if award_date else "unknown"
    return f"{ocid}-award-{date_str}-{seq}"


# ---------------------------------------------------------------------------
# Main transformer
# ---------------------------------------------------------------------------


def row_to_release(row: CouncilContractRow, seq: int = 1) -> Release | None:
    """Convert a CouncilContractRow into an OCDS Release.

    Supplier identifier is intentionally None (no ABN disclosed by council).
    Demiton's council_pipeline_service resolves names post-ingest.
    """
    if not row.awarded_to.strip() and not row.title.strip():
        return None

    ocid = _make_ocid(row.council_key, row.reference, row.title, row.awarded_to)
    release_id = _make_release_id(ocid, row.award_date, seq)
    release_date = row.award_date or datetime.utcnow()

    buyer_id = _BUYER_IDS.get(row.council_key, f"au-council-{row.council_key.lower()}")
    buyer = Organization(id=buyer_id, name=row.council_name, roles=["buyer"])

    # No ABN -- use name-based org ID so the pipeline can resolve later
    supplier_id = (
        f"au-name-{hashlib.sha1(row.awarded_to.encode(), usedforsecurity=False).hexdigest()[:10]}"
    )
    supplier = Organization(
        id=supplier_id,
        name=row.awarded_to.strip() or "Unknown Supplier",
        identifier=None,  # resolved post-ingest via ABR search_by_name
        roles=["supplier"],
    )

    period = (
        Period(startDate=row.start_date, endDate=row.end_date)
        if row.start_date or row.end_date
        else None
    )
    value = Value(amount=row.value_aud) if row.value_aud is not None else None

    award = Award(
        id=f"{release_id}-a1",
        title=row.title or row.description,
        description=row.description,
        status="active",
        date=row.award_date,
        value=value,
        suppliers=[supplier] if row.awarded_to.strip() else [],
        contractPeriod=period,
    )

    contract = Contract(
        id=f"{release_id}-c1",
        awardID=award.id,
        title=row.title or row.description,
        status="active",
        value=value,
        dateSigned=row.award_date,
        period=period,
    )

    tender = Tender(
        id=f"{ocid}-tender",
        title=row.title or row.description,
        status="complete",
        procurementMethod=normalize_method(row.procurement_method),
        procurementMethodDetails=row.procurement_method,
        value=value,
        contractPeriod=period,
    )

    source: dict = {"_jurisdiction": row.council_key, "_abn_source": "name_match"}
    if row.reference:
        source["contractReference"] = row.reference

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

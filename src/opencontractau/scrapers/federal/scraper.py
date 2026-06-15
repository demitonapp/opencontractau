"""
AusTender Federal Contracts scraper.

Source:    api.tenders.gov.au/ocds (AusTender OCDS API v1.1)
Publisher: Department of Finance (AusTender)
Format:    OCDS v1.1 JSON, date-windowed contractPublished endpoint
Threshold: $10,000 (Commonwealth Procurement Rules 2023)
Updates:   Near real-time - contracts published within days of award
Coverage:  Federal government contracts only (not state/territory)

Pagination:
    The API returns exactly 100 releases per page. The next-page cursor
    is in ``links.next`` as an absolute URL. We follow it until either
    the page is partial (< 100 records), links.next is absent, or the
    max_pages cap is reached.

ABN:
    Supplier ABNs are in ``parties[].additionalIdentifiers`` where
    ``scheme == "AU-ABN"``. The awards[].suppliers[] array holds IDs
    that reference parties[], so we cross-reference to enrich each
    supplier with their ABN before building the Release object.

Amendments:
    AusTender models amendments as separate releases with tag
    ``["awardUpdate"]`` and a new ocid. Both originals and amendments
    are included in the output - the caller can filter by tag if needed.

Known gaps:
    OCP research found ~30% of contracts missing from the OCDS API vs
    the AusTender frontend. ABN coverage is ~70-80%. Both limitations
    are reported in the platform data quality scoreboard.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from opencontractau.models.ocds import (
    Award,
    Contract,
    Identifier,
    Organization,
    Period,
    Publisher,
    Release,
    ReleasePackage,
    Tender,
    Value,
)
from opencontractau.scrapers.base import OPENCONTRACTAU_UA, RateLimitedClient

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tenders.gov.au"
DEFAULT_LOOKBACK_DAYS = 90
PAGE_SIZE = 100  # AusTender returns exactly 100 releases per page

# AusTender errorCode 100 = "no records in the requested date range"
EMPTY_RANGE_ERROR_CODE = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_iso_z(dt: datetime) -> str:
    """Format a UTC datetime as AusTender expects: YYYY-MM-DDTHH:MM:SSZ."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_datetime(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if not raw:
        return None
    s = str(raw).strip().rstrip("Z")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    logger.debug("AusTender: cannot parse datetime %r", raw)
    return None


def _parse_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError):
        return None


def _extract_abn(entity: dict[str, Any]) -> str | None:
    """Extract ABN digits from additionalIdentifiers or identifier."""
    for ident in entity.get("additionalIdentifiers") or []:
        if str(ident.get("scheme") or "").upper() == "AU-ABN":
            raw = (ident.get("id") or "").strip()
            return raw if raw else None
    identifier = entity.get("identifier") or {}
    if str(identifier.get("scheme") or "").upper() == "AU-ABN":
        raw = (identifier.get("id") or "").strip()
        return raw if raw else None
    return None


# ---------------------------------------------------------------------------
# OCDS raw-dict -> library model conversion
# ---------------------------------------------------------------------------


def _raw_to_release(raw: dict[str, Any]) -> Release | None:
    """Convert one raw AusTender OCDS release dict into a library Release."""
    ocid = (raw.get("ocid") or "").strip()
    release_id = (raw.get("id") or "").strip()
    if not ocid or not release_id:
        return None

    release_date = _parse_datetime(raw.get("date")) or datetime.utcnow()
    tag: list[str] = raw.get("tag") or ["award"]
    if isinstance(tag, str):
        tag = [tag]

    # Build parties lookup: id -> party dict for cross-referencing
    parties_by_id: dict[str, dict[str, Any]] = {
        p["id"]: p for p in (raw.get("parties") or []) if p.get("id")
    }

    # ---- buyer ----
    buyer: Organization | None = None
    raw_buyer = raw.get("buyer") or {}
    if raw_buyer:
        buyer_id = raw_buyer.get("id") or ""
        buyer_party = parties_by_id.get(buyer_id) or raw_buyer
        buyer_name = buyer_party.get("name") or raw_buyer.get("name") or "Unknown Agency"
        buyer = Organization(
            id=buyer_id or f"au-federal-buyer-{abs(hash(buyer_name)):08x}"[:32],
            name=buyer_name,
            roles=["buyer"],
        )

    # ---- tender ----
    tender: Tender | None = None
    raw_tender = raw.get("tender") or {}
    if raw_tender:
        t_value_raw = raw_tender.get("value") or {}
        t_amount = _parse_decimal(t_value_raw.get("amount"))
        tender = Tender(
            id=raw_tender.get("id") or f"{ocid}-tender",
            title=raw_tender.get("title"),
            status=raw_tender.get("status") or "complete",
            procurementMethod=raw_tender.get("procurementMethod"),
            procurementMethodDetails=raw_tender.get("procurementMethodDetails"),
            procurementMethodRationale=raw_tender.get("procurementMethodRationale"),
            numberOfTenderers=raw_tender.get("numberOfTenderers"),
            value=Value(amount=t_amount) if t_amount is not None else None,
        )

    # ---- contracts lookup (awardID -> contract dict) ----
    contracts_by_award_id: dict[str, dict[str, Any]] = {
        c["awardID"]: c
        for c in (raw.get("contracts") or [])
        if c.get("awardID")
    }

    # ---- awards and contracts ----
    awards: list[Award] = []
    contracts: list[Contract] = []
    classification: dict[str, Any] = {}

    for raw_award in raw.get("awards") or []:
        award_id = (raw_award.get("id") or "").strip()
        if not award_id:
            continue

        a_value_raw = raw_award.get("value") or {}
        a_amount = _parse_decimal(a_value_raw.get("amount"))

        a_period_raw = raw_award.get("contractPeriod") or {}
        a_period = (
            Period(
                startDate=_parse_datetime(a_period_raw.get("startDate")),
                endDate=_parse_datetime(a_period_raw.get("endDate")),
            )
            if a_period_raw
            else None
        )

        # Enrich suppliers: cross-reference parties[] to get ABN
        suppliers: list[Organization] = []
        for sup_ref in raw_award.get("suppliers") or []:
            sup_id = sup_ref.get("id") or ""
            party = parties_by_id.get(sup_id) or sup_ref
            sup_name = party.get("name") or sup_ref.get("name") or "Unknown Supplier"
            abn = _extract_abn(party) or _extract_abn(sup_ref)
            identifier = (
                Identifier(scheme="AU-ABN", id=abn, legalName=sup_name)
                if abn
                else None
            )
            suppliers.append(
                Organization(
                    id=sup_id or f"au-federal-sup-{abs(hash(sup_name)):08x}"[:32],
                    name=sup_name,
                    identifier=identifier,
                    roles=["supplier"],
                )
            )

        award = Award(
            id=award_id,
            title=raw_award.get("title"),
            description=raw_award.get("description"),
            status=raw_award.get("status") or "active",
            date=_parse_datetime(raw_award.get("date")),
            value=Value(amount=a_amount) if a_amount is not None else None,
            suppliers=suppliers,
            contractPeriod=a_period,
        )
        awards.append(award)

        # Match contract by awardID; extract UNSPSC classification if present
        raw_contract = contracts_by_award_id.get(award_id) or {}
        classification: dict[str, Any] = {}
        if raw_contract:
            c_value_raw = raw_contract.get("value") or {}
            c_amount = _parse_decimal(c_value_raw.get("amount"))
            c_period_raw = raw_contract.get("period") or {}
            c_period = (
                Period(
                    startDate=_parse_datetime(c_period_raw.get("startDate")),
                    endDate=_parse_datetime(c_period_raw.get("endDate")),
                )
                if c_period_raw
                else None
            )
            # UNSPSC classification: contracts[].items[].classification
            for item in raw_contract.get("items") or []:
                clf = item.get("classification") or {}
                if clf.get("id"):
                    classification = clf
                    break

            contracts.append(
                Contract(
                    id=raw_contract.get("id") or f"{award_id}-c",
                    awardID=award_id,
                    title=raw_contract.get("title"),
                    status=raw_contract.get("status") or "active",
                    value=Value(amount=c_amount) if c_amount is not None else None,
                    dateSigned=_parse_datetime(raw_contract.get("dateSigned")),
                    period=c_period,
                )
            )

    source: dict[str, Any] = {"_jurisdiction": "AUSTENDER"}
    if classification:
        source["_unspsc_id"] = classification.get("id")
        source["_unspsc_description"] = classification.get("description")

    return Release(
        ocid=ocid,
        id=release_id,
        date=release_date,
        tag=tag,
        buyer=buyer,
        tender=tender,
        awards=awards,
        contracts=contracts,
        source=source,
    )


# ---------------------------------------------------------------------------
# Page fetcher
# ---------------------------------------------------------------------------


async def _fetch_page(
    client: RateLimitedClient,
    url: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Fetch one page from the AusTender OCDS API.

    Returns (releases, next_url_or_None).
    Handles AusTender's errorCode 100 (empty range) as an empty response.
    """
    try:
        response = await client.get(url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            try:
                payload = exc.response.json()
                if isinstance(payload, dict) and payload.get("errorCode") == EMPTY_RANGE_ERROR_CODE:
                    logger.info("AusTender: empty date range at %s", url)
                    return [], None
            except Exception:
                pass
        raise

    payload = response.json()
    page_releases: list[dict[str, Any]] = payload.get("releases") or []
    next_url: str | None = (payload.get("links") or {}).get("next")
    return page_releases, next_url


# ---------------------------------------------------------------------------
# Public scrape entry point
# ---------------------------------------------------------------------------


async def scrape(
    from_date: date | None = None,
    to_date: date | None = None,
    max_pages: int = 500,
    min_interval_s: float = 0.5,
) -> ReleasePackage:
    """
    Scrape AusTender federal contracts via the OCDS contractPublished endpoint.

    Args:
        from_date: start of the published date window (inclusive). Defaults
                   to DEFAULT_LOOKBACK_DAYS (90) days ago.
        to_date:   end of the published date window (inclusive). Defaults to now.
        max_pages: page cap (100 releases per page). Default 500 = 50,000
                   releases. Narrow the date window if you hit the cap.
        min_interval_s: seconds between requests. AusTender tolerates 2/sec.
    """
    now = datetime.now(timezone.utc)

    from_dt = (
        datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)
        if from_date
        else now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    )
    to_dt = (
        datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)
        if to_date
        else now
    )

    from_iso = _to_iso_z(from_dt)
    to_iso = _to_iso_z(to_dt)

    initial_path = (
        f"/ocds/findByDates/contractPublished"
        f"/{quote(from_iso, safe=':')}/{quote(to_iso, safe=':')}"
    )
    current_url = BASE_URL + initial_path

    logger.info(
        "AusTender scrape: from=%s to=%s max_pages=%d",
        from_iso, to_iso, max_pages,
    )

    all_raw_releases: list[dict[str, Any]] = []
    page = 0

    async with RateLimitedClient(
        base_url=BASE_URL,
        min_interval_s=min_interval_s,
        user_agent=OPENCONTRACTAU_UA,
        check_robots=False,  # api.tenders.gov.au is a public API endpoint, not a website
    ) as client:
        while current_url and page < max_pages:
            logger.debug("AusTender page=%d url=%s", page + 1, current_url)
            page_releases, next_url = await _fetch_page(client, current_url)
            all_raw_releases.extend(page_releases)

            logger.info(
                "AusTender page=%d fetched=%d total=%d",
                page + 1, len(page_releases), len(all_raw_releases),
            )

            if not page_releases or len(page_releases) < PAGE_SIZE:
                break
            if not next_url:
                break

            current_url = next_url
            page += 1

    if page >= max_pages:
        logger.warning(
            "AusTender: hit page cap (%d pages = %d releases). "
            "Narrow the date window to get complete coverage.",
            max_pages, len(all_raw_releases),
        )

    logger.info("AusTender: converting %d raw releases to OCDS models", len(all_raw_releases))

    releases: list[Release] = []
    for seq, raw in enumerate(all_raw_releases, start=1):
        try:
            release = _raw_to_release(raw)
        except Exception as exc:
            logger.warning("Skip malformed AusTender release seq=%d: %s", seq, exc)
            continue
        if release is not None:
            releases.append(release)

    logger.info("AusTender: %d releases ready", len(releases))

    return ReleasePackage(
        uri="https://github.com/demitonapp/opencontractau/releases/federal/austender",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

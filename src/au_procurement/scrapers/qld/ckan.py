"""
Queensland multi-agency contract disclosure harvester.

Discovers and ingests contract disclosure CSVs from data.qld.gov.au across
every Queensland agency that publishes under the Queensland Procurement
Policy mandate.

The Procurement Policy specifies a common set of disclosure fields but
agencies export from different tools (Excel, Power BI, custom dashboards),
so column headers vary. Schema reconciliation happens in
``transformers/qld_generic.py``.

TMR is excluded from this harvester because it has a dedicated scraper at
``scrapers/qld/tmr.py``. Use ``au-procurement qld tmr`` for TMR data.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime

from au_procurement.models.ocds import Publisher, Release, ReleasePackage
from au_procurement.scrapers.base import OPENCONTRACTSAU_UA, RateLimitedClient
from au_procurement.transformers.qld_generic import build_column_map, row_to_release

logger = logging.getLogger(__name__)

CKAN_BASE = "https://www.data.qld.gov.au"
PACKAGE_SHOW = "/api/3/action/package_show"


@dataclass(frozen=True)
class AgencyConfig:
    package_id: str
    agency_code: str
    display_name: str


AGENCIES: list[AgencyConfig] = [
    AgencyConfig(
        package_id="contract-disclosure-qfd",
        agency_code="qfd",
        display_name="Queensland Fire Department",
    ),
    AgencyConfig(
        package_id="desbt-contract-disclosure",
        agency_code="desbt",
        display_name="Trade, Employment and Training",
    ),
    AgencyConfig(
        package_id="mnhhs-contract-disclosure",
        agency_code="mnhhs",
        display_name="Metro North Hospital and Health Service",
    ),
    AgencyConfig(
        package_id="contract-disclosure-qps-igem",
        agency_code="qps-igem",
        display_name="Queensland Police Service / IGEM",
    ),
    AgencyConfig(
        package_id="metrosouthhealth_contract-disclosure-report_2025-26",
        agency_code="msh",
        display_name="Metro South Hospital and Health Service",
    ),
    AgencyConfig(
        package_id="contract-disclosure-report-department-of-housing",
        agency_code="housing",
        display_name="Department of Housing",
    ),
    AgencyConfig(
        package_id="queensland-treasury-contract-disclosure",
        agency_code="treasury",
        display_name="Queensland Treasury",
    ),
    AgencyConfig(
        package_id="queensland-audit-office-contract-disclosure-2025-26",
        agency_code="qao",
        display_name="Queensland Audit Office",
    ),
    AgencyConfig(
        package_id="fy-2025-26_qbcc_contract-disclosure-report",
        agency_code="qbcc",
        display_name="Queensland Building and Construction Commission",
    ),
    AgencyConfig(
        package_id="rshq-contract-disclosure",
        agency_code="rshq",
        display_name="Resources Safety and Health Queensland",
    ),
    AgencyConfig(
        package_id="contract-disclosure-reports",
        agency_code="sport-olympic",
        display_name="Sport, Racing and Olympic and Paralympic Games",
    ),
]


@dataclass
class ResourceRef:
    resource_id: str
    name: str
    url: str
    format: str


async def _fetch_package_resources(
    client: RateLimitedClient,
    package_id: str,
) -> list[ResourceRef]:
    url = f"{CKAN_BASE}{PACKAGE_SHOW}?id={package_id}"
    try:
        payload = await client.get_json(url)
    except Exception as exc:
        logger.warning("Failed to fetch package %s: %s", package_id, exc)
        return []
    result = payload.get("result", {})
    out: list[ResourceRef] = []
    for r in result.get("resources", []):
        fmt = (r.get("format") or "").upper()
        if fmt != "CSV":
            continue
        out.append(
            ResourceRef(
                resource_id=r["id"],
                name=r.get("name") or "",
                url=r["url"],
                format=fmt,
            )
        )
    return out


def _is_most_recent(resources: list[ResourceRef]) -> ResourceRef | None:
    """Pick the resource whose name looks most recent (highest FY tag)."""
    if not resources:
        return None
    import re

    def _fy_key(r: ResourceRef) -> tuple[int, int, str]:
        # Look for FY-YYYY or YYYY-YYYY or YYYY-YY patterns
        m = re.search(r"(20\d{2})[-_/]?(20\d{2}|\d{2})", r.name)
        if m:
            try:
                return (int(m.group(1)), int(m.group(2)[-2:]), r.name)
            except ValueError:
                pass
        return (0, 0, r.name)

    return max(resources, key=_fy_key)


async def _fetch_csv_releases(
    client: RateLimitedClient,
    agency: AgencyConfig,
    resource: ResourceRef,
) -> list[Release]:
    logger.info("[%s] downloading %s", agency.agency_code, resource.name)
    try:
        raw = await client.get_bytes(resource.url)
    except Exception as exc:
        logger.error("[%s] download failed: %s", agency.agency_code, exc)
        return []

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    column_map = build_column_map(headers)

    if not column_map.get("supplier_name") or not column_map.get("value"):
        logger.warning(
            "[%s] resource %s missing core columns; map=%s",
            agency.agency_code,
            resource.name,
            list(column_map.keys()),
        )

    releases: list[Release] = []
    for seq, row in enumerate(reader, start=1):
        try:
            release = row_to_release(
                row=row,
                column_map=column_map,
                agency_code=agency.agency_code,
                default_agency_name=agency.display_name,
                seq=seq,
            )
        except Exception as exc:
            logger.debug("[%s] row %d parse error: %s", agency.agency_code, seq, exc)
            continue
        if release is not None:
            releases.append(release)

    logger.info(
        "[%s] parsed %d releases from %s",
        agency.agency_code,
        len(releases),
        resource.name,
    )
    return releases


async def scrape(
    only_agencies: list[str] | None = None,
    skip_agencies: list[str] | None = None,
    most_recent_only: bool = False,
    min_interval_s: float = 1.0,
) -> ReleasePackage:
    """
    Harvest QLD multi-agency contract disclosure data.

    Args:
        only_agencies: restrict to these agency codes (e.g. ["qfd", "treasury"])
        skip_agencies: skip these agency codes
        most_recent_only: per agency, only fetch the most recent FY CSV
        min_interval_s: seconds between requests
    """
    only_set = set(only_agencies) if only_agencies else None
    skip_set = set(skip_agencies) if skip_agencies else set()

    selected = [
        a for a in AGENCIES
        if (only_set is None or a.agency_code in only_set) and a.agency_code not in skip_set
    ]
    logger.info("Selected %d/%d agencies", len(selected), len(AGENCIES))

    all_releases: list[Release] = []

    async with RateLimitedClient(
        base_url=CKAN_BASE,
        min_interval_s=min_interval_s,
        user_agent=OPENCONTRACTSAU_UA,
        check_robots=False,  # data.qld.gov.au/robots.txt disallows /api/ but this is a public open-data API
    ) as client:
        for agency in selected:
            resources = await _fetch_package_resources(client, agency.package_id)
            if not resources:
                logger.info("[%s] no CSV resources", agency.agency_code)
                continue

            if most_recent_only:
                recent = _is_most_recent(resources)
                resources = [recent] if recent else []

            for resource in resources:
                releases = await _fetch_csv_releases(client, agency, resource)
                all_releases.extend(releases)
                await asyncio.sleep(min_interval_s)

    return ReleasePackage(
        uri="https://github.com/demitonapp/opencontractau/releases/qld/ckan",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=all_releases,
    )

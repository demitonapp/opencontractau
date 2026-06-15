"""
NSW historical OCDS archive importer.

Source: NSW Treasury / NSW eTendering OCDS feed, archived by the Open
        Contracting Partnership (OCP) Data Registry.
        https://data.open-contracting.org/en/publication/11

Status: The feed was discontinued in February 2025 when NSW migrated from
        NSW eTendering to buy.nsw.gov.au. The archive covers 2005-02-2025.

The archived data is already in OCDS v1.1 release-package format. This
importer fetches the bulk download, validates structure, and re-packages
with OpenContractsAU publisher metadata.
"""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers.base import OPENCONTRACTAU_UA, RateLimitedClient

logger = logging.getLogger(__name__)

OCP_REGISTRY_URL = "https://data.open-contracting.org/en/publication/11"

# The OCP registry stores bulk archives as zip files. The exact URL must be
# obtained from the registry metadata; this placeholder documents the source.
# Run `opencontractau nsw historical --list` to discover available downloads.
OCP_NSW_PUBLISHER_ID = "11"


async def _discover_bulk_download_url(client: RateLimitedClient) -> str | None:
    """
    Attempt to discover the bulk download URL from the OCP registry entry.

    The registry uses a JSON-LD structure. Returns the first .zip or .json
    download URL found, or None if discovery fails.
    """
    try:
        text = await client.get_text(
            f"https://data.open-contracting.org/api/publication/{OCP_NSW_PUBLISHER_ID}/",
        )
        data = json.loads(text)
        downloads = data.get("downloads") or data.get("files") or []
        for d in downloads:
            url = d.get("url") or d.get("downloadURL") or ""
            if url.endswith(".zip") or url.endswith(".json"):
                return url
    except Exception as exc:
        logger.debug("OCP registry API discovery failed: %s", exc)
    return None


async def fetch_from_zip(zip_bytes: bytes) -> list[Release]:
    """Extract OCDS releases from a zip archive of release-package JSON files."""
    releases: list[Release] = []
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        json_files = [n for n in zf.namelist() if n.endswith(".json")]
        logger.info("Archive contains %d JSON files", len(json_files))
        for name in json_files:
            try:
                data = json.loads(zf.read(name))
                pkg_releases = data.get("releases") or []
                for r in pkg_releases:
                    try:
                        releases.append(Release.model_validate(r))
                    except Exception as exc:
                        logger.debug("Skip malformed release in %s: %s", name, exc)
            except Exception as exc:
                logger.warning("Could not parse %s: %s", name, exc)
    return releases


async def fetch_from_local(archive_path: Path) -> list[Release]:
    """Import from a locally-downloaded archive (zip or JSON)."""
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    if archive_path.suffix == ".zip":
        return await fetch_from_zip(archive_path.read_bytes())

    if archive_path.suffix == ".json":
        data = json.loads(archive_path.read_text(encoding="utf-8"))
        raw_releases = data.get("releases") or (data if isinstance(data, list) else [])
        releases: list[Release] = []
        for r in raw_releases:
            try:
                releases.append(Release.model_validate(r))
            except Exception as exc:
                logger.debug("Skip malformed release: %s", exc)
        return releases

    raise ValueError(f"Unsupported archive format: {archive_path.suffix}")


async def scrape(
    local_path: Path | None = None,
    min_interval_s: float = 3.0,
) -> ReleasePackage:
    """
    Import NSW historical OCDS data.

    Prefer local_path if provided (faster, no network). If None, attempts
    to discover and download the bulk archive from the OCP registry.

    The OCP archive does not have a stable bulk-download URL. If auto-discovery
    fails, download manually from:
      https://data.open-contracting.org/en/publication/11
    and pass the path to --local-path.
    """
    if local_path:
        logger.info("Importing NSW historical data from %s", local_path)
        releases = await fetch_from_local(local_path)
    else:
        logger.info("Attempting auto-discovery of NSW OCP archive...")
        async with RateLimitedClient(
            min_interval_s=min_interval_s,
            user_agent=OPENCONTRACTAU_UA,
        ) as client:
            bulk_url = await _discover_bulk_download_url(client)
            if not bulk_url:
                raise RuntimeError(
                    "Could not auto-discover NSW OCP bulk download URL. "
                    "Download the archive manually from "
                    "https://data.open-contracting.org/en/publication/11 "
                    "and re-run with --local-path /path/to/archive.zip"
                )
            logger.info("Downloading NSW historical archive from %s", bulk_url)
            zip_bytes = await client.get_bytes(bulk_url)
        releases = await fetch_from_zip(zip_bytes)

    logger.info("Imported %d NSW historical releases", len(releases))
    return ReleasePackage(
        uri="https://github.com/demitonapp/opencontractau/releases/nsw/historical",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

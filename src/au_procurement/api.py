"""
au-procurement public Python API.

Programmatic facade over the per-jurisdiction scrapers. Intended for
consumers that import the library (e.g. Demiton) rather than using the CLI.

Usage::

    from au_procurement import fetch_releases, list_jurisdictions

    package = await fetch_releases("ACT")
    print(f"{len(package.releases)} releases")

    for key in list_jurisdictions():
        print(key)
"""

from __future__ import annotations

import importlib
import logging
from datetime import date, datetime
from typing import Any

from au_procurement.models.ocds import ReleasePackage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jurisdiction registry
# ---------------------------------------------------------------------------
# Maps a stable jurisdiction key to the scraper module path.
# The module must expose an async ``scrape(**kwargs) -> ReleasePackage``.

_JURISDICTION_SCRAPERS: dict[str, str] = {
    "ACT":            "au_procurement.scrapers.act.scraper",
    "QLD_TMR":        "au_procurement.scrapers.qld.tmr",
    "QLD_MULTI":      "au_procurement.scrapers.qld.ckan",
    "NSW_LIVE":       "au_procurement.scrapers.nsw.live",
    "NSW_HISTORICAL": "au_procurement.scrapers.nsw.historical",
    "NT":             "au_procurement.scrapers.nt.scraper",
    "TAS":            "au_procurement.scrapers.tas.scraper",
    "VIC":            "au_procurement.scrapers.vic.scraper",
    # Federal
    "AUSTENDER":      "au_procurement.scrapers.federal.scraper",
}


def list_jurisdictions() -> list[str]:
    """Return the list of supported jurisdiction keys."""
    return list(_JURISDICTION_SCRAPERS)


async def fetch_releases(
    jurisdiction: str,
    since: date | None = None,
    **kwargs: Any,
) -> ReleasePackage:
    """
    Fetch OCDS releases for one jurisdiction.

    Parameters
    ----------
    jurisdiction:
        One of ``list_jurisdictions()`` (e.g. ``"ACT"``, ``"QLD_TMR"``).
        Case-insensitive.
    since:
        Optional date filter. Supported on scrapers that expose a date
        parameter (ACT via SoQL WHERE, NSW_LIVE via from_date). For
        other scrapers the parameter is silently ignored and the scraper
        uses its default recency window (usually "recent" mode / current
        financial year).
    **kwargs:
        Forwarded verbatim to the underlying ``scrape()`` function.
        Useful for advanced callers: ``max_records``, ``max_pages``,
        ``only_agencies``, etc.

    Returns
    -------
    ReleasePackage
        OCDS v1.1 release package with ``releases`` list.

    Raises
    ------
    ValueError
        Unknown jurisdiction key.
    Exception
        Any scraper-level error (network, parse, Cloudflare block, etc.)
        propagates unchanged so callers can decide on retry / fallback.
    """
    key = jurisdiction.upper()
    module_path = _JURISDICTION_SCRAPERS.get(key)
    if not module_path:
        raise ValueError(
            f"Unknown jurisdiction '{jurisdiction}'. "
            f"Supported: {list_jurisdictions()}"
        )

    module = importlib.import_module(module_path)
    scrape_fn = getattr(module, "scrape", None)
    if scrape_fn is None:
        raise RuntimeError(
            f"Scraper module '{module_path}' does not expose a 'scrape' function."
        )

    # Translate the generic `since` date into each scraper's native parameter.
    if since is not None:
        if key == "ACT":
            # SoQL WHERE clause; Socrata date literals use ISO 8601.
            kwargs.setdefault("where", f"execution_date > '{since.isoformat()}'")
        elif key == "NSW_LIVE":
            # NSW live scraper accepts a datetime.
            kwargs.setdefault("from_date", datetime(since.year, since.month, since.day))
        elif key == "AUSTENDER":
            # Federal OCDS endpoint uses from_date / to_date date objects.
            kwargs.setdefault("from_date", since)
        # QLD_TMR, QLD_MULTI, NT, TAS, VIC: use their built-in recency windows.

    logger.info("au_procurement.fetch_releases: jurisdiction=%s since=%s", key, since)
    return await scrape_fn(**kwargs)

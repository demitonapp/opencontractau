"""au-procurement: OC4IDS-compliant scraper for Australian government procurement data."""

__version__ = "0.3.0"

from opencontractau.api import fetch_releases, list_jurisdictions

__all__ = ["fetch_releases", "list_jurisdictions", "__version__"]

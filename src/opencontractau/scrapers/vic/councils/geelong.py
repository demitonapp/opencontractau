"""GEELONG awarded-contract register. See _registers.py for the parser."""

from __future__ import annotations

from opencontractau.models.ocds import ReleasePackage
from opencontractau.scrapers.vic.councils._registers import scrape_register


async def scrape(**kwargs) -> ReleasePackage:
    return await scrape_register("GEELONG", **kwargs)

"""
Northern Beaches Council awarded contract register scraper.

Source:    pubapp.northernbeaches.nsw.gov.au/contracts/contractdata.ashx
Format:    JSON (jQuery AJAX endpoint behind the public register page)
Threshold: AU$150,000 (NSW GIPA Act 2009)
ABN:       Not disclosed
Updates:   Ongoing (regularly maintained)

The council's own contracts-register page (northernbeaches.nsw.gov.au/
council/tenders/contracts-register) renders no table at all: the register
is embedded via an <iframe id="contracts-iframe"> pointing at a separate
subdomain (pubapp.northernbeaches.nsw.gov.au/contracts/contracts.ashx),
and that shell page in turn loads its data with a jQuery
``$.ajax({url: 'contractdata.ashx', dataType: 'json'})`` call (see its
js/contracts.js). Verified live (2026-08-13): the JSON endpoint itself is
open, needs no iframe or JS execution, and returns every contract in one
response - simpler and more reliable than the HTML-table shape this
scraper originally assumed.

Verified JSON shape (one entry of ``response["Contract"]``):
  {"Class": "CLASS 1", "ID": 924, "Number": "000924",
   "Name": "...", "SuccessfulTenderer": "...", "TendererAddress": "...",
   "StartDate": "02/01/2026", "StartDateSort": "2026-01-02",
   "EndDate": "...", "EndDateSort": "...", "AmountPayable": 959219.8, ...}

``AmountPayable`` is already a JSON number, and ``StartDateSort`` is
ISO-formatted - both are used directly rather than re-parsing the
locale-formatted ``AmountPayable``-adjacent string fields the HTML
version of this scraper used to scrape out of table cells.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

import httpx

from opencontractau.models.ocds import Publisher, Release, ReleasePackage
from opencontractau.scrapers.base import BROWSER_UA
from opencontractau.transformers.council import CouncilContractRow, row_to_release

logger = logging.getLogger(__name__)

DATA_URL = "https://pubapp.northernbeaches.nsw.gov.au/contracts/contractdata.ashx"

COUNCIL_KEY = "NORTHERN_BEACHES"
COUNCIL_NAME = "Northern Beaches Council"


def _parse_contracts(payload: dict) -> list[CouncilContractRow]:
    error = payload.get("Error")
    if error:
        logger.error("NORTHERN_BEACHES: API returned an error: %s", error)
        return []

    rows: list[CouncilContractRow] = []
    for contract in payload.get("Contract", []):
        supplier = (contract.get("SuccessfulTenderer") or "").strip()
        if not supplier:
            continue

        amount = contract.get("AmountPayable")
        value_aud = Decimal(str(amount)) if isinstance(amount, (int, float)) else None

        award_date: datetime | None = None
        start_sort = contract.get("StartDateSort")
        if start_sort:
            try:
                award_date = datetime.strptime(start_sort, "%Y-%m-%d")
            except ValueError:
                pass

        end_date: datetime | None = None
        end_sort = contract.get("EndDateSort")
        if end_sort:
            try:
                end_date = datetime.strptime(end_sort, "%Y-%m-%d")
            except ValueError:
                pass

        number = (contract.get("Number") or "").strip() or None
        name = (contract.get("Name") or "").strip()

        rows.append(CouncilContractRow(
            council_key=COUNCIL_KEY,
            council_name=COUNCIL_NAME,
            reference=number,
            title=name or f"Northern Beaches Contract - {supplier}",
            awarded_to=supplier,
            value_aud=value_aud,
            award_date=award_date,
            start_date=award_date,
            end_date=end_date,
            procurement_method=(contract.get("TenderMethod") or "").strip() or None,
        ))

    logger.info("NORTHERN_BEACHES: parsed %d rows", len(rows))
    return rows


async def scrape(**kwargs) -> ReleasePackage:
    """Fetch and parse the Northern Beaches Council contracts JSON API."""
    uri = f"https://github.com/demitonapp/opencontractau/releases/{COUNCIL_KEY}"

    async with httpx.AsyncClient(timeout=60.0, headers={"User-Agent": BROWSER_UA}) as client:
        try:
            resp = await client.get(DATA_URL)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.error("NORTHERN_BEACHES: contractdata.ashx fetch failed: %s", exc)
            return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=[])

    rows = _parse_contracts(payload)
    releases: list[Release] = [r for seq, row in enumerate(rows, 1) if (r := row_to_release(row, seq=seq))]
    logger.info("NORTHERN_BEACHES: %d releases ready", len(releases))
    return ReleasePackage(uri=uri, publishedDate=datetime.utcnow(), publisher=Publisher(), releases=releases)

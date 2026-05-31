"""Unit tests for the South Australia Playwright scraper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opencontractau.models.ocds import ReleasePackage
from opencontractau.scrapers.sa.scraper import scrape

MOCK_LIST_HTML = """
<html>
<body>
  <table>
    <tr id="contractRow123">
      <td><a href="/contract/view?id=123">Contract 123</a></td>
    </tr>
  </table>
</body>
</html>
"""

MOCK_DETAIL_HTML = """
<html>
<body>
  <div class="row">
    <span class="LIST_TITLE">Public Body</span>
    <div class="col-sm-8">SA Treasury</div>
  </div>
  <div class="row">
    <span class="LIST_TITLE">Contract Number</span>
    <div class="col-sm-8">SA-12345</div>
  </div>
  <div class="row">
    <span class="LIST_TITLE">Title</span>
    <div class="col-sm-8">Consulting Services</div>
  </div>
  <div class="row">
    <span class="LIST_TITLE">Starting Date</span>
    <div class="col-sm-8">10 May 2026</div>
  </div>
  <div class="row">
    <span class="LIST_TITLE">Expiry Date</span>
    <div class="col-sm-8">10 May 2027</div>
  </div>
  <div class="row">
    <span class="LIST_TITLE">Total Value of the Contract</span>
    <div class="col-sm-8">$150,000.00</div>
  </div>
  <table>
    <tr class="contractor">
      <td><b>Acme Consulting</b></td>
      <td>
        <table>
          <tr>
            <td><strong>ABN</strong></td>
            <td>12345678901</td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_sa_scrape_end_to_end():
    mock_page = AsyncMock()
    mock_page.content.return_value = MOCK_LIST_HTML
    mock_page.query_selector_all.return_value = ["some_row"]

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_client_instance = AsyncMock()
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance._context = mock_context
    mock_client_instance.get_html.return_value = MOCK_DETAIL_HTML

    with patch(
        "opencontractau.scrapers.sa.scraper.PlaywrightClient",
        return_value=mock_client_instance,
    ):
        package: ReleasePackage = await scrape(max_pages=1, min_interval_s=0.1)

        assert package is not None
        assert len(package.releases) == 1

        release = package.releases[0]
        assert release.ocid == "ocau-sa-SA-12345"
        assert release.buyer.name == "SA Treasury"
        assert release.tender.title == "Consulting Services"
        assert len(release.awards) == 1
        assert release.awards[0].value.amount == 150000
        assert release.awards[0].suppliers[0].name == "Acme Consulting"
        assert release.awards[0].suppliers[0].identifier.id == "12345678901"


@pytest.mark.asyncio
async def test_sa_scrape_live():
    package = await scrape(max_pages=1)
    assert package is not None
    print(f"\n[LIVE TEST] Scraped {len(package.releases)} releases from South Australia!")
    for r in package.releases[:3]:
        print(f"  - OCID: {r.ocid}")
        print(f"    Buyer: {r.buyer.name}")
        print(f"    Tender: {r.tender.title}")
        if r.awards:
            print(f"    Amount: {r.awards[0].value.amount}")


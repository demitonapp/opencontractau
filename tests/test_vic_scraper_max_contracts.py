"""VIC's max_contracts budget cap.

A full VIC scrape walks ~20 pages and detail-fetches every one of the ~470
live contracts - about 25 minutes at the 3s rate limit. A caller with a
tight per-jurisdiction time budget (Demiton's harvest gives each 90s) needs
a way to get SOME current contracts rather than none; max_contracts caps
both list-page walking and the detail-fetch loop so the run actually
finishes inside a small budget.

_enumerate_ids makes real HTTP calls, so it's exercised here with a fake
client rather than mocked at the network layer - keeps the test fast and
independent of the live site.
"""

import pytest

from opencontractau.scrapers.vic.scraper import _enumerate_ids

# Three contract IDs per page, distinct across pages - matches the real
# `<tr id="contractRowN">` shape parse_contract_ids reads from a page.
def _rows(*ids: int) -> str:
    return "".join(f'<tr id="contractRow{i}"><td>x</td></tr>' for i in ids)


_PAGE_HTML = {1: _rows(1, 2, 3), 2: _rows(4, 5, 6), 3: _rows(7, 8, 9)}


class _FakeClient:
    def __init__(self):
        self.pages_requested: list[int] = []

    async def get_text(self, path, **params):
        page = params.get("page", 1)
        self.pages_requested.append(page)
        return _PAGE_HTML.get(page, "")


@pytest.mark.asyncio
class TestEnumerateIdsMaxIds:
    async def test_no_cap_walks_every_page(self):
        client = _FakeClient()
        ids = await _enumerate_ids(client, preset="recentlyAwarded", max_pages=5)
        assert ids == [1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert client.pages_requested == [1, 2, 3, 4]  # page 4 is empty -> stops

    async def test_cap_stops_after_enough_ids_gathered(self):
        client = _FakeClient()
        ids = await _enumerate_ids(client, preset="recentlyAwarded", max_pages=5, max_ids=4)
        # Stops as soon as the running total reaches the cap - does not walk
        # every declared page just because max_pages allows it.
        assert len(ids) >= 4
        assert client.pages_requested == [1, 2]

    async def test_cap_does_not_request_pages_beyond_what_is_needed(self):
        client = _FakeClient()
        await _enumerate_ids(client, preset="recentlyAwarded", max_pages=20, max_ids=2)
        # A cap of 2 is satisfied by page 1 alone (3 ids) - page 2 must
        # never be requested, or the whole point of capping is defeated.
        assert client.pages_requested == [1]

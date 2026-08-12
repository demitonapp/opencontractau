"""buy.nsw notice-list parsing.

Regression: the parser looked for class="notice-item" and per-field classes
that buy.nsw does not use, so it matched nothing and every scrape reported a
successful harvest of zero records. Fixture is a real page saved 2026-08-12.
"""
from pathlib import Path

import pytest

from opencontractau.scrapers.nsw.live import _NoticeListParser, _notice_to_release

FIXTURE = Path(__file__).parent / "fixtures" / "buy_nsw_can_search.html"


@pytest.fixture(scope="module")
def notices():
    parser = _NoticeListParser()
    parser.feed(FIXTURE.read_text())
    parser.close()
    return parser.notices


def test_finds_every_notice_on_the_page(notices):
    assert len(notices) == 10


def test_core_fields_populated(notices):
    for n in notices:
        assert n.get("notice-guid")
        assert n.get("notice-title")
        assert n.get("agency-name")


def test_contractor_name_is_extracted(notices):
    """The whole point: award data needs the supplier."""
    named = [n for n in notices if n.get("supplier-name")]
    assert len(named) == len(notices)


def test_known_notice_parsed_correctly(notices):
    n = next(x for x in notices if x.get("notice-id") == "CAN-122526")
    assert n["agency-name"] == "Homes NSW"
    assert n["category"] == "Construction - Residential"
    assert "Acquisition Upgrades" in n["notice-title"]


def test_releases_carry_supplier_and_ocid(notices):
    releases = [r for r in (_notice_to_release(n, i) for i, n in enumerate(notices, 1)) if r]
    assert len(releases) == 10
    assert all(r.ocid.startswith("ocau-nsw-live-") for r in releases)
    assert all(r.awards and r.awards[0].suppliers for r in releases)
    # ocid must key off the stable GUID, not the loop counter
    assert len({r.ocid for r in releases}) == 10

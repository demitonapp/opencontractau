"""NSW live (buy.nsw.gov.au) scraper.

Card/list parsing is exercised against markup trimmed from the real site
(fetched 2026-08-13). The WAF-retry behaviour in scrape() is exercised
against a fake CouncilClient (patched in) rather than the network, both
because a real test must not depend on the WAF's actual (observed
non-deterministic) behaviour, and because scrape()'s own docstring explains
why a real retry-and-wait test would be slow and still flaky.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opencontractau.scrapers.nsw.live import (
    card_to_release,
    is_waf_challenge,
    parse_notice_cards,
    parse_total_results,
    scrape,
)

WAF_CHALLENGE_HTML = """
<html><body>
<script>
    AwsWafIntegration.saveReferrer();
    AwsWafIntegration.checkForceRefresh().then((forceRefresh) => {});
</script>
<noscript><h1>JavaScript is disabled</h1></noscript>
</body></html>
"""

# One real card, trimmed from the live results list.
LIST_PAGE_HTML = """
<h2>Displaying 1-10 of 15,654 results</h2>
<ul class="cards profiles ls-n borders-0 c-links nsw-wysiwyg-content">
<li>
<h3><a href="/notices/1702D003-B07F-4421-908A3B87FD25FF92">Residential Design and Construct at 27 Runcorn Avenue</a></h3>
<dl class="details">
<dt>CAN ID</dt><dd>CAN-RFT-2015099</dd>
<dt>Agency</dt><dd>Aboriginal Housing Office</dd>
<dt>Category</dt><dd>Construction - Residential</dd>
<dt>Publish date</dt><dd>13-Aug-2026</dd>
<dt>Contract period</dt><dd>11-Aug-2026 to 15-Apr-2027</dd>
<dt>Estimated amount payable to the contractor (including GST)</dt><dd>$770,165.00 (Goods or services supplied)</dd>
<dt>Contractor name</dt><dd> WINSMAN GROUP PTY LTD<br> </dd>
<dt>Is an Aboriginal-owned business</dt><dd>No</dd>
<dt>Last updated</dt><dd>13-Aug-2026</dd>
</dl>
</li>
</ul>
"""

EMPTY_PAGE_HTML = '<h2>Displaying 0-0 of 15,654 results</h2><ul class="cards"></ul>'


class TestIsWafChallenge:
    def test_detects_the_challenge_marker(self):
        assert is_waf_challenge(WAF_CHALLENGE_HTML) is True

    def test_real_content_is_not_a_false_positive(self):
        assert is_waf_challenge(LIST_PAGE_HTML) is False


class TestParseTotalResults:
    def test_extracts_the_comma_formatted_total(self):
        assert parse_total_results(LIST_PAGE_HTML) == 15654

    def test_no_match_returns_none(self):
        assert parse_total_results("<p>nothing here</p>") is None


class TestParseNoticeCards:
    def test_extracts_every_field(self):
        cards = parse_notice_cards(LIST_PAGE_HTML)
        assert len(cards) == 1
        card = cards[0]
        assert card["CAN ID"] == "CAN-RFT-2015099"
        assert card["Agency"] == "Aboriginal Housing Office"
        assert card["Category"] == "Construction - Residential"
        assert card["Contractor name"] == "WINSMAN GROUP PTY LTD"
        assert card["href"] == "/notices/1702D003-B07F-4421-908A3B87FD25FF92"

    def test_an_empty_page_yields_no_cards(self):
        assert parse_notice_cards(EMPTY_PAGE_HTML) == []

    def test_html_entities_are_decoded_not_left_literal(self):
        # "BG&amp;E Pty Limited" and "CAN-LAHC 2026&#x2f;176" are both real,
        # verified live 2026-08-13 - a supplier called "BG&amp;E" or a CAN ID
        # with a literal "&#x2f;" in it would be wrong on the page, not just
        # ugly.
        html = LIST_PAGE_HTML.replace(
            "WINSMAN GROUP PTY LTD", "BG&amp;E Pty Limited",
        ).replace(
            "CAN-RFT-2015099", "CAN-LAHC 2026&#x2f;176",
        )
        card = parse_notice_cards(html)[0]
        assert card["Contractor name"] == "BG&E Pty Limited"
        assert card["CAN ID"] == "CAN-LAHC 2026/176"


class TestCardToRelease:
    def _card(self):
        return parse_notice_cards(LIST_PAGE_HTML)[0]

    def test_full_release(self):
        release = card_to_release(self._card(), seq=1)
        assert release is not None
        assert release.ocid == "ocau-nsw-live-1702D003-B07F-4421-908A3B87FD25FF92"
        assert release.buyer.name == "Aboriginal Housing Office"
        award = release.awards[0]
        assert award.suppliers[0].name == "WINSMAN GROUP PTY LTD"
        assert award.value.amount == 770165.0
        assert award.contract_period.start_date.strftime("%Y-%m-%d") == "2026-08-11"
        assert award.contract_period.end_date.strftime("%Y-%m-%d") == "2027-04-15"
        assert release.source["category"] == "Construction - Residential"
        assert release.source["isAboriginalOwnedBusiness"] is False

    def test_value_parenthetical_suffix_does_not_break_parsing(self):
        # "$770,165.00 (Goods or services supplied)" - the classification
        # after the dollar figure isn't part of the number.
        release = card_to_release(self._card(), seq=1)
        assert release.awards[0].value.amount == 770165.0

    def test_a_card_with_no_contractor_name_yields_no_release(self):
        card = self._card()
        card["Contractor name"] = ""
        assert card_to_release(card, seq=1) is None

    def test_a_card_with_no_href_yields_no_release(self):
        card = self._card()
        card["href"] = ""
        assert card_to_release(card, seq=1) is None


def _fake_client(pages_by_call):
    """A CouncilClient stand-in whose get_text returns pages_by_call[i] on
    the i-th call (0-indexed), raising IndexError (caught as a plain
    exception by scrape()) once exhausted - matching "no more configured
    responses" rather than a real failure mode."""
    client = MagicMock()
    calls = {"n": 0}

    async def _get_text(*args, **kwargs):
        i = calls["n"]
        calls["n"] += 1
        if i >= len(pages_by_call):
            raise RuntimeError("no more fake pages configured")
        return pages_by_call[i]

    client.get_text = AsyncMock(side_effect=_get_text)
    client.close = AsyncMock()
    return client, calls


@pytest.mark.asyncio
class TestScrapeWafRetry:
    async def test_a_waf_challenge_that_clears_on_retry_still_yields_releases(self):
        client, calls = _fake_client([WAF_CHALLENGE_HTML, LIST_PAGE_HTML, EMPTY_PAGE_HTML])
        with patch("opencontractau.scrapers.nsw.live.CouncilClient", return_value=client), \
             patch("opencontractau.scrapers.nsw.live.asyncio.sleep", new=AsyncMock()):
            package = await scrape(max_pages=5)
        assert len(package.releases) == 1
        assert calls["n"] == 3  # challenge, retry succeeds, then the empty page stops it

    async def test_exhausted_retries_stops_but_keeps_releases_from_earlier_pages(self):
        # Page 1 succeeds; page 2 is WAF-challenged on every attempt.
        client, calls = _fake_client([
            LIST_PAGE_HTML, WAF_CHALLENGE_HTML, WAF_CHALLENGE_HTML, WAF_CHALLENGE_HTML,
        ])
        with patch("opencontractau.scrapers.nsw.live.CouncilClient", return_value=client), \
             patch("opencontractau.scrapers.nsw.live.asyncio.sleep", new=AsyncMock()) as sleep:
            package = await scrape(max_pages=5)
        # Page 1's release survives even though page 2 never recovers - the
        # whole point of stopping cleanly rather than raising.
        assert len(package.releases) == 1
        assert sleep.await_count == 2  # the two configured retry delays

    async def test_close_is_always_called_even_when_every_page_is_blocked(self):
        client, _ = _fake_client([WAF_CHALLENGE_HTML] * 3)
        with patch("opencontractau.scrapers.nsw.live.CouncilClient", return_value=client), \
             patch("opencontractau.scrapers.nsw.live.asyncio.sleep", new=AsyncMock()):
            package = await scrape(max_pages=5)
        assert package.releases == []
        client.close.assert_awaited_once()

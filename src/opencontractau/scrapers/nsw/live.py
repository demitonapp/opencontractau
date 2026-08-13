"""
NSW live contract award scraper - buy.nsw.gov.au.

buy.nsw replaced NSW eTendering in 2025. Contract Award Notices (CANs) are
published at https://buy.nsw.gov.au/notices/search?noticeTypes=can - publicly,
with no login and no per-notice fetch needed: verified live 2026-08-13 at
15,654 notices, sorted newest-updated-first by default. Every field this
scraper reads - agency, category, contract period, value, contractor name -
is already on the search-results card; only ABN needs the per-notice detail
page, and is intentionally not fetched here (see module note below).

This DID need bypassing a bot check, just not the one the previous version
of this file assumed: plain httpx gets `202 Accepted` with an empty body (a
soft block, not the "AWS WAF JS challenge" the old docstring named), and a
Chrome User-Agent header alone does not change that - confirmed by sending
one over plain httpx and still getting 202/empty. curl_cffi's Chrome TLS
fingerprint impersonation (CouncilClient, shared with the QLD/NSW council
scrapers behind the same kind of check) gets a normal 200 with full content.
The previous version's other assumption - three guessed JSON API endpoints
- also 404s; deleted rather than kept as dead fallback code.

ABN: the search-results card does not carry it, but the notice detail page
does (`/notices/{guid}`, alongside ACN and a supplier profile link). Left
out of this scraper deliberately: fetching it means one detail request per
notice, which turns a "300 records for one page-walk" source back into the
same per-item rate-limited cost that makes VIC/QLD_MULTI too slow to
complete inside a harvest's time budget. Worth a follow-up as a dedicated
enrichment pass (the same shape as the LGP/QBCC/Austroads readers), not
baked into the base fetch.

Pagination: `?page=N` (1-indexed; page 0 is the disabled "back" link's
target, not a real page) with 10 notices per page.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from html import unescape

from opencontractau.models.ocds import (
    Award,
    Contract,
    Organization,
    Period,
    Publisher,
    Release,
    ReleasePackage,
    Tender,
    Value,
)
from opencontractau.scrapers.qld.councils._client import CouncilClient
from opencontractau.transformers.council import parse_au_date, parse_value

logger = logging.getLogger(__name__)

BASE_URL = "https://buy.nsw.gov.au"
NOTICES_PATH = "/notices/search"
OCID_PREFIX = "ocau-nsw-live"

_TOTAL_RESULTS_RE = re.compile(r"Displaying\s+[\d,]+-[\d,]+\s+of\s+([\d,]+)\s+results", re.IGNORECASE)

# buy.nsw.gov.au sits behind AWS WAF, and it does not challenge every request
# the same way: the identical request succeeded, then got this exact
# challenge page 15 seconds later, then succeeded again 5 MINUTES after that
# - verified live 2026-08-13, three requests, three different outcomes, no
# discernible fixed rate window.
#
# That measured recovery time (minutes) rules out an in-process retry
# cascade as the fix: this scrape() runs under Demiton's per-jurisdiction
# harvest budget, dispatched via asyncio.wait_for with a hard cutoff that
# CANCELS the call on timeout - every release collected so far is lost, not
# returned partially, since cancellation happens before the final `return`
# is ever reached. A backoff cascade long enough to reliably outlast a
# multi-minute block would blow that budget and lose the whole run; one
# short enough to fit the budget doesn't reliably clear the block anyway, per
# what was actually measured. So retries here are short - for a genuine
# transient blip, not a bet that in-process waiting solves the WAF pattern
# above. The real recovery mechanism is the next scheduled harvest run: a
# WAF-degraded run returns whatever it collected before stopping rather than
# either hanging indefinitely or (via the cancellation above) discarding it.
_WAF_CHALLENGE_MARKER = "AwsWafIntegration"
_WAF_RETRY_DELAYS_S = (3.0, 8.0)


def is_waf_challenge(html: str) -> bool:
    return _WAF_CHALLENGE_MARKER in html

_CARD_RE = re.compile(
    r'<li>\s*<h3><a href="(?P<href>/notices/[^"]+)">(?P<title>.*?)</a></h3>'
    r'.*?<dl class="details"[^>]*>(?P<fields>.*?)</dl>',
    re.IGNORECASE | re.DOTALL,
)
_FIELD_RE = re.compile(r"<dt>(?P<label>.*?)</dt>\s*<dd>(?P<value>.*?)</dd>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_PERIOD_RE = re.compile(r"^\s*(.+?)\s+to\s+(.+?)\s*$", re.IGNORECASE)


def _clean(raw: str) -> str:
    # Real page content is HTML-entity-encoded ("BG&amp;E Pty Limited",
    # "CAN-LAHC 2026&#x2f;176" for the literal slash) - verified live
    # 2026-08-13. Unescape AFTER stripping tags, so a decoded "&lt;" is
    # never mistaken for a real tag by a pass running the other way round.
    return unescape(re.sub(r"\s+", " ", _TAG_RE.sub(" ", raw))).strip()


def _parse_fields(fields_html: str) -> dict[str, str]:
    return {
        _clean(m.group("label")): _clean(m.group("value"))
        for m in _FIELD_RE.finditer(fields_html)
    }


def parse_total_results(html: str) -> int | None:
    match = _TOTAL_RESULTS_RE.search(html)
    return int(match.group(1).replace(",", "")) if match else None


def parse_notice_cards(html: str) -> list[dict]:
    """Parse one search-results page into a list of field dicts.

    Pure - no network. Each dict carries `href`, `title`, and every dt/dd
    pair from the card verbatim (label text -> cleaned value text); callers
    look up the specific labels they need, so an unrecognised or reordered
    label on the source's end degrades gracefully instead of raising.
    """
    cards = []
    for match in _CARD_RE.finditer(html):
        fields = _parse_fields(match.group("fields"))
        fields["href"] = match.group("href")
        fields["title"] = _clean(match.group("title"))
        cards.append(fields)
    return cards


def card_to_release(card: dict, seq: int) -> Release | None:
    """Convert one parsed notice card into an OCDS Release."""
    supplier_name = card.get("Contractor name", "").strip()
    if not supplier_name:
        return None

    href = card.get("href", "")
    notice_guid = href.rsplit("/", 1)[-1] if href else None
    if not notice_guid:
        return None

    can_id = card.get("CAN ID")
    ocid = f"{OCID_PREFIX}-{notice_guid}"
    release_id = f"{ocid}-award-1"

    agency_name = card.get("Agency") or "NSW Government"
    agency_slug = re.sub(r"[^a-z0-9]+", "-", agency_name.lower()).strip("-")
    buyer = Organization(id=f"au-nsw-agency-{agency_slug}", name=agency_name, roles=["buyer"])

    period = None
    start_date = end_date = None
    period_match = _PERIOD_RE.match(card.get("Contract period", ""))
    if period_match:
        start_date = parse_au_date(period_match.group(1))
        end_date = parse_au_date(period_match.group(2))
        if start_date or end_date:
            period = Period(startDate=start_date, endDate=end_date)

    publish_date = parse_au_date(card.get("Publish date"))
    award_date = start_date or publish_date

    # "$770,165.00 (Goods or services supplied)" - the trailing parenthetical
    # (goods/services/construction classification) isn't part of the number
    # and defeats parse_value's Decimal conversion if left in.
    value_raw = card.get("Estimated amount payable to the contractor (including GST)", "")
    value_raw = value_raw.split("(")[0].strip()
    value = Value(amount=amount) if (amount := parse_value(value_raw)) is not None else None

    supplier = Organization(
        id=f"au-nsw-supplier-{re.sub(r'[^a-z0-9]+', '-', supplier_name.lower()).strip('-')[:60]}",
        name=supplier_name,
        roles=["supplier"],
    )

    award = Award(
        id=f"{release_id}-a1",
        title=card.get("title") or None,
        status="active",
        date=award_date,
        value=value,
        suppliers=[supplier],
        contractPeriod=period,
    )
    contract = Contract(
        id=f"{release_id}-c1",
        awardID=award.id,
        title=card.get("title") or None,
        status="active",
        value=value,
        dateSigned=award_date,
        period=period,
    )
    tender = Tender(
        id=f"{ocid}-tender",
        title=card.get("title") or None,
        status="complete",
        value=value,
        contractPeriod=period,
    )

    source: dict = {"noticeGuid": notice_guid}
    if can_id:
        source["canId"] = can_id
    if card.get("Category"):
        source["category"] = card["Category"]
    if card.get("Is an Aboriginal-owned business"):
        source["isAboriginalOwnedBusiness"] = card["Is an Aboriginal-owned business"].strip().lower() == "yes"

    return Release(
        ocid=ocid,
        id=release_id,
        date=award_date or publish_date or datetime.utcnow(),
        tag=["award"],
        buyer=buyer,
        tender=tender,
        awards=[award],
        contracts=[contract],
        source=source,
    )


async def scrape(
    max_pages: int = 20,
    min_interval_s: float = 3.0,
) -> ReleasePackage:
    """
    Scrape NSW live contract award notices from buy.nsw.gov.au.

    Args:
        max_pages: pages to walk, 10 notices each. Default 20 = 200 notices,
            ~60s nominal at this source's ~3s/request cost (list only, no
            detail fetch) - still ~5x VIC's equivalent budget-capped
            throughput despite the smaller page count, since each request
            here returns 10 full records instead of one. Deliberately short
            of the 90s per-jurisdiction harvest budget, not right up against
            it: Demiton dispatches this under asyncio.wait_for, which
            CANCELS (and loses) everything collected so far on timeout - the
            margin exists so one retried page (see _WAF_RETRY_DELAYS_S)
            doesn't push the total over that cliff. The source sorts
            newest-updated-first by default, so a capped run still surfaces
            the most current notices, not an arbitrary slice; a full
            backfill of all ~15,650 needs max_pages=1566+ and a much longer
            budget, run separately from the recurring harvest.
        min_interval_s: seconds between requests (minimum 3.0).
    """
    min_interval_s = max(min_interval_s, 3.0)
    client = CouncilClient(base_url=BASE_URL, min_interval_s=min_interval_s)

    releases: list[Release] = []
    seq = 1
    waf_blocked_pages = 0
    try:
        for page in range(1, max_pages + 1):
            html: str | None = None
            last_error: Exception | None = None

            for attempt, delay in enumerate((0.0, *_WAF_RETRY_DELAYS_S)):
                if delay:
                    logger.warning("[nsw_live] page %d: retry %d in %.0fs", page, attempt, delay)
                    await asyncio.sleep(delay)
                try:
                    candidate = await client.get_text(NOTICES_PATH, noticeTypes="can", page=page, mode="regular")
                except Exception as exc:
                    last_error = exc
                    continue
                if is_waf_challenge(candidate):
                    last_error = None
                    continue
                html = candidate
                break

            if html is None:
                waf_blocked_pages += 1
                if last_error is not None:
                    logger.warning("[nsw_live] page %d failed after retries: %s", page, last_error)
                else:
                    logger.warning(
                        "[nsw_live] page %d: still WAF-challenged after %d retries - stopping this run",
                        page, len(_WAF_RETRY_DELAYS_S),
                    )
                break

            cards = parse_notice_cards(html)
            if not cards:
                logger.info("[nsw_live] page %d: no cards - stopping", page)
                break

            for card in cards:
                release = card_to_release(card, seq)
                if release is not None:
                    releases.append(release)
                    seq += 1

            if page == 1:
                total = parse_total_results(html)
                if total is not None:
                    logger.info("[nsw_live] %d notices available in total", total)
    finally:
        await client.close()

    if waf_blocked_pages:
        logger.warning(
            "[nsw_live] run ended WAF-blocked, not exhausted: %d releases from an "
            "incomplete page walk. Not the same as a genuinely quiet source - "
            "re-running later may recover more.",
            len(releases),
        )
    logger.info("Produced %d NSW live releases", len(releases))
    return ReleasePackage(
        uri="https://github.com/demitonapp/opencontractau/releases/nsw/live",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

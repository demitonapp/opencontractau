"""
NSW live contract award scraper - buy.nsw.gov.au.

buy.nsw replaced NSW eTendering in 2025. Contract Award Notices (CANs) are
published at https://buy.nsw.gov.au/notices/search?noticeTypes=can.

The platform blocks non-browser user-agents with HTTP 403. This scraper uses
a browser-style UA and request headers. If 403 persists in production, use
the Playwright driver (au_procurement.drivers.playwright_nsw) instead.

Rate limit: 1 request per 3 seconds per the OpenContractAU contributing guide.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from html.parser import HTMLParser

from opencontractau.models.ocds import (
    Address,
    Award,
    Contract,
    Identifier,
    Organization,
    Period,
    Release,
    ReleasePackage,
    Tender,
    Value,
    Publisher,
)
from opencontractau.scrapers.base import BROWSER_UA, RateLimitedClient
from opencontractau.transformers.qld import _clean_abn, _parse_au_date, _parse_value

logger = logging.getLogger(__name__)

BASE_URL = "https://buy.nsw.gov.au"
NOTICES_PATH = "/notices/search"
OCID_PREFIX = "ocau-nsw-live"

# buy.nsw may expose a JSON API behind its React frontend.
# These candidate endpoints are probed in order; the first 200 response wins.
_JSON_CANDIDATES = [
    "/api/v1/contract_awards.json",
    "/api/notices.json",
    "/api/v2/notices",
]


class _NoticeListParser(HTMLParser):
    """
    Minimal HTML parser for buy.nsw contract award notice listings.

    CSS selectors would be cleaner but HTMLParser avoids a BeautifulSoup
    dependency. Adjust _TARGET_CLASS and field extraction if buy.nsw
    changes its markup.
    """

    _TARGET_CLASS = "notice-item"

    def __init__(self) -> None:
        super().__init__()
        self._in_notice = False
        self._depth = 0
        self._notice_depth = 0
        self._current: dict[str, str] = {}
        self.notices: list[dict[str, str]] = []
        self._current_field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()
        self._depth += 1

        if self._TARGET_CLASS in classes:
            self._in_notice = True
            self._notice_depth = self._depth
            self._current = {}

        if self._in_notice:
            for cls in classes:
                if cls in ("notice-title", "supplier-name", "contract-value",
                           "award-date", "agency-name", "notice-id"):
                    self._current_field = cls

    def handle_endtag(self, tag: str) -> None:
        if self._in_notice and self._depth == self._notice_depth:
            if self._current:
                self.notices.append(dict(self._current))
            self._in_notice = False
            self._current = {}
        self._depth -= 1
        self._current_field = None

    def handle_data(self, data: str) -> None:
        if self._in_notice and self._current_field:
            existing = self._current.get(self._current_field, "")
            self._current[self._current_field] = (existing + data).strip()


def _notice_to_release(notice: dict[str, str], seq: int = 1) -> Release | None:
    title = notice.get("notice-title", "").strip()
    supplier = notice.get("supplier-name", "").strip()
    value_raw = notice.get("contract-value", "")
    date_raw = notice.get("award-date", "")
    agency = notice.get("agency-name", "").strip()
    notice_id = notice.get("notice-id", "").strip()

    if not title and not supplier and not notice_id:
        return None

    ocid = f"{OCID_PREFIX}-{re.sub(r'[^a-zA-Z0-9]', '-', notice_id) if notice_id else seq}"
    award_date = _parse_au_date(date_raw)
    contract_value = _parse_value(value_raw)
    release_date = award_date or datetime.utcnow()
    release_id = f"{ocid}-award-{release_date.strftime('%Y%m%d')}-{seq}"

    buyer = Organization(
        id="au-nsw-government",
        name=agency or "NSW Government",
        roles=["buyer"],
    )
    supplier_org = Organization(
        id=f"au-nsw-supplier-{seq}",
        name=supplier or "Unknown supplier",
        roles=["supplier"],
    )
    award = Award(
        id=f"{release_id}-a1",
        title=title or None,
        status="active",
        date=award_date,
        value=Value(amount=contract_value) if contract_value else None,
        suppliers=[supplier_org] if supplier else [],
    )
    contract = Contract(
        id=f"{release_id}-c1",
        awardID=award.id,
        title=title or None,
        status="active",
        value=award.value,
        dateSigned=award_date,
    )
    tender = Tender(
        id=f"{ocid}-tender",
        title=title or None,
        status="complete",
    )
    return Release(
        ocid=ocid,
        id=release_id,
        date=release_date,
        tag=["award"],
        buyer=buyer,
        tender=tender,
        awards=[award],
        contracts=[contract],
        source={"noticeId": notice_id} if notice_id else {},
    )


async def _probe_json_api(client: RateLimitedClient) -> list[dict] | None:
    """Try undocumented JSON endpoints before falling back to HTML parsing."""
    for path in _JSON_CANDIDATES:
        try:
            data = await client.get_json(BASE_URL + path)
            if isinstance(data, list) and data:
                return data
            if isinstance(data, dict):
                for key in ("results", "notices", "data", "items"):
                    if isinstance(data.get(key), list):
                        return data[key]
        except Exception:
            continue
    return None


async def _scrape_html_page(client: RateLimitedClient, page: int) -> tuple[list[dict], bool]:
    """Fetch one page of HTML notices. Returns (notices, has_next_page)."""
    url = f"{BASE_URL}{NOTICES_PATH}?noticeTypes=can&page={page}"
    try:
        html = await client.get_text(url)
    except Exception as exc:
        logger.warning("Failed to fetch page %d: %s", page, exc)
        return [], False

    parser = _NoticeListParser()
    parser.feed(html)

    has_next = "page=" + str(page + 1) in html or f">{page + 1}<" in html
    return parser.notices, has_next and bool(parser.notices)


async def scrape(
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    max_pages: int = 50,
    min_interval_s: float = 3.0,
) -> ReleasePackage:
    """
    Scrape live NSW contract award notices from buy.nsw.gov.au.

    buy.nsw blocks non-browser UAs with 403. This scraper uses a browser-style
    UA. If 403 errors persist, consider using Playwright for JavaScript
    rendering.

    Args:
        from_date: filter notices awarded on or after this date.
        to_date: filter notices awarded on or before this date.
        max_pages: safety cap on paginated requests.
        min_interval_s: seconds between requests (minimum 3.0).
    """
    min_interval_s = max(min_interval_s, 3.0)

    async with RateLimitedClient(
        min_interval_s=min_interval_s,
        user_agent=BROWSER_UA,
        extra_headers={
            "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.9",
            "Accept-Language": "en-AU,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    ) as client:
        raw_notices = await _probe_json_api(client)
        if raw_notices is not None:
            logger.info("buy.nsw JSON API responded with %d notices", len(raw_notices))
            notices = raw_notices
        else:
            logger.info("Falling back to HTML parsing of buy.nsw notices")
            notices = []
            for page in range(1, max_pages + 1):
                page_notices, has_next = await _scrape_html_page(client, page)
                notices.extend(page_notices)
                logger.info("Page %d: %d notices (total %d)", page, len(page_notices), len(notices))
                if not has_next:
                    break

    releases: list[Release] = []
    for seq, notice in enumerate(notices, start=1):
        release = _notice_to_release(notice, seq)
        if release is None:
            continue
        if from_date and release.date and release.date < from_date:
            continue
        if to_date and release.date and release.date > to_date:
            continue
        releases.append(release)

    logger.info("Produced %d NSW live releases", len(releases))
    return ReleasePackage(
        uri="https://github.com/demitonapp/opencontractau/releases/nsw/live",
        publishedDate=datetime.utcnow(),
        publisher=Publisher(),
        releases=releases,
    )

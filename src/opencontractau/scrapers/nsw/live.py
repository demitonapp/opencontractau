"""
NSW live contract award scraper - buy.nsw.gov.au.

buy.nsw replaced NSW eTendering in 2025. Contract Award Notices (CANs) are
published at https://buy.nsw.gov.au/notices/search?noticeTypes=can.

The platform blocks non-browser user-agents with HTTP 403. This scraper uses
a browser-style UA and request headers. If 403 persists in production, use
the Playwright driver (opencontractau.drivers.playwright_nsw) instead.

Rate limit: 1 request per 3 seconds per the OpenContractsAU contributing guide.
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

# buy.nsw has no JSON API. /api/v1/contract_awards.json, /api/notices.json
# and /api/v2/notices were speculative and all answer 404 (verified
# 2026-08-12); the notice search is plain server-rendered HTML, not a React
# frontend. The probe is gone rather than left to burn three requests per run
# against a WAF that rate-limits (see scrape() note).

# dt label -> internal field name. Labels are taken verbatim from the
# <dl class="details"> block buy.nsw renders per notice.
_FIELD_BY_LABEL = {
    "CAN ID": "notice-id",
    "Agency": "agency-name",
    "Category": "category",
    "Publish date": "award-date",
    "Contract period": "contract-period",
    "Estimated amount payable to the contractor (including GST)": "contract-value",
    "Contractor name": "supplier-name",
    "Is an Aboriginal-owned business": "aboriginal-owned",
    "Last updated": "last-updated",
}

_NOTICE_HREF = re.compile(r"^/notices/([0-9A-Fa-f-]{20,})$")


class _NoticeListParser(HTMLParser):
    """Parse the buy.nsw contract-award notice listing.

    Markup per notice (verified against a live page 2026-08-12):

        <li>
          <h3><a href="/notices/{GUID}">TITLE</a></h3>
          <dl class="details">
            <dt>CAN ID</dt><dd>CAN-122526</dd>
            <dt>Agency</dt><dd>Homes NSW</dd>
            ...
          </dl>
        </li>

    The previous implementation looked for `class="notice-item"` and per-field
    classes (`notice-title`, `supplier-name`, ...). None of those exist on
    buy.nsw, so it matched nothing and every run reported a successful harvest
    of zero records. Everything needed is on the listing page, so no detail
    fetch is required for the award record.
    """

    def __init__(self) -> None:
        super().__init__()
        self.notices: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._in_h3 = False
        self._capturing: str | None = None   # "title" | "dt" | "dd"
        self._buf: list[str] = []
        self._label: str | None = None

    def _flush(self) -> None:
        if self._current:
            self.notices.append(self._current)
        self._current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self._in_h3 = True
            return
        if tag == "a" and self._in_h3:
            href = dict(attrs).get("href") or ""
            m = _NOTICE_HREF.match(href)
            if m:
                self._flush()
                self._current = {"notice-guid": m.group(1)}
                self._capturing = "title"
                self._buf = []
            return
        if self._current is not None and tag in ("dt", "dd"):
            self._capturing = tag
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._in_h3 = False
        text = "".join(self._buf).strip()
        if tag == "a" and self._capturing == "title":
            if self._current is not None:
                self._current["notice-title"] = text
        elif tag == "dt" and self._capturing == "dt":
            self._label = text
        elif tag == "dd" and self._capturing == "dd":
            field = _FIELD_BY_LABEL.get(self._label or "")
            if field and self._current is not None:
                self._current[field] = text
            self._label = None
        else:
            return
        self._capturing = None
        self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _notice_to_release(notice: dict[str, str], seq: int = 1) -> Release | None:
    title = notice.get("notice-title", "").strip()
    supplier = notice.get("supplier-name", "").strip()
    value_raw = notice.get("contract-value", "")
    date_raw = notice.get("award-date", "")
    agency = notice.get("agency-name", "").strip()
    notice_id = notice.get("notice-id", "").strip()

    if not title and not supplier and not notice_id:
        return None

    # GUID first: it is buy.nsw's own stable notice key and survives a CAN ID
    # being reissued. CAN ID second, sequence only as a last resort.
    key = notice.get("notice-guid") or notice_id or str(seq)
    ocid = f"{OCID_PREFIX}-{re.sub(r'[^a-zA-Z0-9]', '-', key)}"
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
        source={
            k: v for k, v in {
                "noticeId": notice_id,
                "noticeGuid": notice.get("notice-guid"),
                "category": notice.get("category"),
                "contractPeriod": notice.get("contract-period"),
                "aboriginalOwned": notice.get("aboriginal-owned"),
            }.items() if v
        },
    )


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

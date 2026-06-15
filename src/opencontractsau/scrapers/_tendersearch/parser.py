"""
HTML parser for TenderSearch platform list and detail pages.

List page format
----------------
- Each contract is a ``<tr id="contractRow{id}">`` element
- Detail link is ``<a href="/contract/view?id={id}">``
- Pagination uses ``?page=N`` query parameter

Detail page format
------------------
- Fields are ``<span class="LIST_TITLE">Label</span>`` followed by
  ``<div class="col-sm-...">Value</div>`` in the same ``<div class="row">``
- Supplier table is ``<tr class="contractor">`` with:
  - Supplier name in ``<b>...</b>``
  - ABN/ACN in nested ``<table>`` with rows like
    ``<tr><td><strong>ABN</strong></td><td>12345678901</td></tr>``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ROW_ID_PATTERN = re.compile(r'<tr id="contractRow(\d+)"', re.IGNORECASE)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_WS_PATTERN = re.compile(r"\s+")

# Captures (label, value_inner_html) for every LIST_TITLE row.
_FIELD_PATTERN = re.compile(
    r'<span\s+class="LIST_TITLE"\s*>(.*?)</span>'
    r'.*?'
    r'<div\s+class="col-sm-\d+[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

_CONTRACTOR_BLOCK_PATTERN = re.compile(
    r'<tr class="contractor"[^>]*>(.*?)(?=<tr class="contractor"|</table>\s*</div>|\Z)',
    re.IGNORECASE | re.DOTALL,
)
_CONTRACTOR_NAME_PATTERN = re.compile(r"<b>(.*?)</b>", re.IGNORECASE | re.DOTALL)
_CONTRACTOR_FIELD_PATTERN = re.compile(
    r'<strong>(ABN|ACN)</strong>\s*</td>\s*<td[^>]*>([^<]+)</td>',
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(html: str) -> str:
    text = _TAG_PATTERN.sub(" ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#160;", " ")
    )
    return _WS_PATTERN.sub(" ", text).strip()


def parse_contract_ids(html: str) -> list[int]:
    """Return contract IDs in document order from a list page."""
    return [int(m.group(1)) for m in _ROW_ID_PATTERN.finditer(html)]


@dataclass
class Supplier:
    name: str
    abn: str | None = None
    acn: str | None = None
    address: str | None = None


@dataclass
class DetailFields:
    fields: dict[str, str] = field(default_factory=dict)
    suppliers: list[Supplier] = field(default_factory=list)

    def get(self, *labels: str) -> str | None:
        """First non-empty match among the given labels (case-insensitive)."""
        for label in labels:
            for k, v in self.fields.items():
                if k.strip().lower() == label.strip().lower() and v:
                    return v
        return None


def parse_detail_html(html: str) -> DetailFields:
    fields: dict[str, str] = {}
    for match in _FIELD_PATTERN.finditer(html):
        label = _strip_html(match.group(1))
        value = _strip_html(match.group(2))
        if label and value and label not in fields:
            fields[label] = value

    suppliers: list[Supplier] = []
    for block_match in _CONTRACTOR_BLOCK_PATTERN.finditer(html):
        block = block_match.group(1)
        name_match = _CONTRACTOR_NAME_PATTERN.search(block)
        if not name_match:
            continue
        name = _strip_html(name_match.group(1))
        if not name:
            continue
        supplier = Supplier(name=name)
        for field_match in _CONTRACTOR_FIELD_PATTERN.finditer(block):
            label = field_match.group(1).strip().upper()
            value = field_match.group(2).strip()
            if label == "ABN":
                supplier.abn = value
            elif label == "ACN":
                supplier.acn = value
        suppliers.append(supplier)

    return DetailFields(fields=fields, suppliers=suppliers)

"""Sutherland Shire Council HTML register parsing.

The supplier name lives only in the "Contract Name" link's title
attribute - not in any visible cell text - so this can't reuse the shared
extract_tables() helper (it discards attributes). Fixture mirrors the
real markup shape (2026-08-13).
"""

from decimal import Decimal

from opencontractau.scrapers.nsw.councils.sutherland_shire import _parse_rows

_HEADER = "<tr><th>Reference</th><th>Contract Name</th><th>Start Date</th><th>End Date</th><th>Estimated Contract Value</th></tr>"


def _table(*rows: str) -> str:
    return f"<table>{_HEADER}{''.join(rows)}</table>"


def _row(ref, supplier, title, start, end, value):
    link = f'<a href="https://example.com/{ref}.pdf" title="{supplier}" target="_blank">{title}</a>' if supplier else \
           f'<a href="https://example.com/{ref}.pdf" target="_blank">{title}</a>'
    return f"<tr><td>{ref}</td><td>{link}</td><td>{start}</td><td>{end}</td><td>{value}</td></tr>"


class TestParseRows:
    def test_supplier_comes_from_the_link_title_not_the_link_text(self):
        html = _table(_row("201110", "JASMAX PTY LIMITED", "Lead Design Consultant", "22-Jul-2026", "21-Jul-2028", "$1,518,251.99"))
        rows = _parse_rows(html)
        assert len(rows) == 1
        row = rows[0]
        assert row.awarded_to == "JASMAX PTY LIMITED"
        assert row.title == "Lead Design Consultant"
        assert row.reference == "201110"
        assert row.value_aud == Decimal("1518251.99")

    def test_trailing_plus_on_value_is_stripped(self):
        html = _table(_row("201083", "Acme Pty Ltd", "Supply of Caddies", "22-Jul-2026", "21-Jul-2033", "$1,383,548.10+"))
        row = _parse_rows(html)[0]
        assert row.value_aud == Decimal("1383548.10")

    def test_rows_with_no_title_attribute_are_skipped_not_fabricated(self):
        # ~18% of live rows are recent contracts whose per-contract PDF
        # hasn't been annotated with a company name yet.
        html = _table(_row("201147", None, "Stormwater Asset Condition Assessment", "22-Jun-2026", "21-Jun-2028", "$189,152.92"))
        assert _parse_rows(html) == []

    def test_a_second_table_on_the_page_is_also_parsed(self):
        html = (
            _table(_row("201110", "JASMAX PTY LIMITED", "Lead Design Consultant", "22-Jul-2026", "21-Jul-2028", "$1,518,251.99"))
            + _table(_row("200974", "Acme Software Pty Ltd", "Privileged Access Management", "09-Jan-2026", "08-Jan-2029", "$412,316.49"))
        )
        rows = _parse_rows(html)
        assert {r.awarded_to for r in rows} == {"JASMAX PTY LIMITED", "Acme Software Pty Ltd"}

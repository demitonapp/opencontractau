"""Parsers for the Victorian council awarded-contract registers.

Markup below is trimmed from the live pages (2026-08-13). Network-free: these
exercise parse_register only.
"""

import pytest

from opencontractau.scrapers.vic.councils._registers import (
    REGISTERS,
    _split_company_numbers,
    parse_register,
)
from opencontractau.transformers.council import parse_au_date, row_to_release

# --- Wyndham: one row per contract, several tables, mixed date formats ------
WYNDHAM_HTML = """
<table>
  <tr><th>Contract Number</th><th>Contract Name</th><th>Date Awarded</th><th>Supplier</th></tr>
  <tr><td>CT001250</td><td>K ROAD RECONSTRUCTION</td><td>13/05/2025</td><td>BILD Infrastructure Pty Ltd</td></tr>
</table>
<table>
  <tr><th>Contract Number</th><th>Contract Name</th><th>Date Awarded</th><th>Supplier</th></tr>
  <tr><td>N400104</td><td>Truganina Elements Community Centre</td><td>2/16/2021</td><td>Canvas Projects Pty Ltd</td></tr>
</table>
<table>
  <tr><th>Heading</th><th>Body</th></tr>
  <tr><td>Not a contract table</td><td>ignore me</td></tr>
</table>
"""

# --- Boroondara: one label/value table per contract, title in the <h3> ------
BOROONDARA_HTML = """
<h3 class="accordion-header-title">Kew Library Redevelopment - Minor Works</h3>
<table><tbody>
  <tr><th>Contract number</th><td>2025/215</td></tr>
  <tr><th>Date awarded by Council</th><td>23 February 2026</td></tr>
  <tr><th>Name of successful supplier</th><td>Bowden Corporation Pty Ltd</td></tr>
  <tr><th>Contract start date</th><td>16 March 2026</td></tr>
  <tr><th>Contract end date</th><td>16 March 2028 (including Defects Liability Period)</td></tr>
  <tr><th>Contract value excluding GST</th><td>$4,214,169</td></tr>
</tbody></table>
"""

# --- Geelong: prose blocks, the only VIC register disclosing ABN/ACN --------
GEELONG_HTML = """
<p><strong>C2600041 &#8211; Coastside Drive Recreation Reserve Master Plan</strong><br>
Awarded 1 June 2026 to:<br>James O. Millar Pty Ltd (ACN 007 406 206)<br>
Type of Contract: Services<br>Length of Contract: 7 months</p><hr>
<p><strong>C2600018 - Supply &amp; Delivery of Pool Chemicals</strong><br>
Awarded 3 July 2026 to:<br>Chemprod Nominees Proprietary Limited (ACN 005 032 744)
ATF Fried Family Chemical Trust (ABN 32 982 143 022) t/a Omega Chemicals<br>
Schedule of Rates</p>
"""


class TestWyndhamWideTable:
    def _rows(self):
        return parse_register(WYNDHAM_HTML, REGISTERS["WYNDHAM"])

    def test_every_contract_table_is_parsed_not_just_the_first(self):
        # The register publishes one table per financial year. Taking only the
        # largest/first table would silently drop every other year.
        assert len(self._rows()) == 2

    def test_non_contract_tables_are_skipped(self):
        assert all("ignore me" not in row.awarded_to for row in self._rows())

    def test_day_first_and_month_first_dates_both_land_correctly(self):
        by_ref = {row.reference: row for row in self._rows()}
        assert by_ref["CT001250"].award_date.strftime("%Y-%m-%d") == "2025-05-13"
        # 2/16/2021 is unambiguously month-first: there is no 16th month.
        assert by_ref["N400104"].award_date.strftime("%Y-%m-%d") == "2021-02-16"


class TestBoroondaraKeyValueTable:
    def _row(self):
        rows = parse_register(BOROONDARA_HTML, REGISTERS["BOROONDARA"])
        assert len(rows) == 1
        return rows[0]

    def test_title_comes_from_the_heading_above_the_table(self):
        # The table carries no title field; without the heading the release
        # would be titled "City of Boroondara Contract - <supplier>".
        assert self._row().title == "Kew Library Redevelopment - Minor Works"

    def test_fields_are_read_off_their_labels(self):
        row = self._row()
        assert row.reference == "2025/215"
        assert row.awarded_to == "Bowden Corporation Pty Ltd"
        assert int(row.value_aud) == 4_214_169
        assert row.award_date.strftime("%Y-%m-%d") == "2026-02-23"

    def test_start_and_end_dates_beat_the_generic_award_date_label(self):
        row = self._row()
        assert row.start_date.strftime("%Y-%m-%d") == "2026-03-16"
        # Trailing prose must not defeat the parse.
        assert row.end_date.strftime("%Y-%m-%d") == "2028-03-16"


class TestGeelongParagraphBlock:
    def _rows(self):
        return parse_register(GEELONG_HTML, REGISTERS["GEELONG"])

    def test_reference_and_title_split_on_hyphen_or_en_dash(self):
        rows = self._rows()
        assert [r.reference for r in rows] == ["C2600041", "C2600018"]
        assert rows[0].title == "Coastside Drive Recreation Reserve Master Plan"

    def test_company_number_is_captured_and_stripped_from_the_name(self):
        rows = self._rows()
        assert rows[0].awarded_to == "James O. Millar Pty Ltd"
        assert rows[0].supplier_acn == "007 406 206"

    def test_abn_wins_over_acn_on_the_emitted_identifier(self):
        # An ABN resolves onto the identity spine directly; an ACN does not.
        release = row_to_release(self._rows()[1])
        identifier = release.awards[0].suppliers[0].identifier
        assert identifier.scheme == "AU-ABN"
        assert identifier.id == "32982143022"

    def test_name_only_supplier_still_gets_no_identifier(self):
        release = row_to_release(parse_register(WYNDHAM_HTML, REGISTERS["WYNDHAM"])[0])
        assert release.awards[0].suppliers[0].identifier is None


class TestCompanyNumberSplit:
    @pytest.mark.parametrize("raw,name,abn,acn", [
        ("Acme Pty Ltd (ACN 007 406 206)", "Acme Pty Ltd", None, "007 406 206"),
        ("Acme Pty Ltd (ABN 32 982 143 022)", "Acme Pty Ltd", "32 982 143 022", None),
        ("Acme Pty Ltd", "Acme Pty Ltd", None, None),
    ])
    def test_split(self, raw, name, abn, acn):
        assert _split_company_numbers(raw) == (name, abn, acn)


class TestSharedDateParser:
    """parse_au_date is shared by all fourteen council scrapers."""

    @pytest.mark.parametrize("raw,expected", [
        ("13/05/2025", "2025-05-13"),   # day-first, unambiguous
        ("2/16/2021", "2021-02-16"),    # month-first, unambiguous
        ("4/11/2024", "2024-11-04"),    # ambiguous -> day-first (AU source)
        ("23 February 2026", "2026-02-23"),
        ("2026-02-23", "2026-02-23"),
    ])
    def test_parses(self, raw, expected):
        assert parse_au_date(raw).strftime("%Y-%m-%d") == expected

    @pytest.mark.parametrize("raw", ["", None, "not a date", "32/13/2025"])
    def test_returns_none_rather_than_guessing(self, raw):
        # A None here becomes "no award date". A wrong guess becomes a
        # fabricated one, which is worse.
        assert parse_au_date(raw) is None

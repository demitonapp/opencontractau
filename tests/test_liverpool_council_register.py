"""Liverpool City Council PDF register parsing.

Fixtures below are trimmed from the real 2026-07 PDF (fetched 2026-08-13):
values, column layout and the RCL3233 / C4000 reference numbers are real.
"""

import pytest

from opencontractau.scrapers.nsw.councils.liverpool import pdf_tables_to_rows

# Index-aligned with _row() below - real header cell text at each position
# the parser actually reads (col 6 = date, col 12 = method).
_COMMON_TAIL = ["Contract Start\nDate", "Initial Term", "Initial Term\nDuration",
                "Extension\nTerms Available", "Extension\nTerm Duration",
                "Final End Date if\nall Extensions", "Method of Tendering"]
HEADER_PAYABLE = ["Contract", "Description", "Contractor", "Contractor Address",
                   "Awarded Payable", "Lump Sum or\nSchedule of Rates", *_COMMON_TAIL]
HEADER_AMOUNT = ["Contract", "Description", "Contractor", "Contractor Address",
                  "Awarded Amount", "Lump Sum or\nSchedule of Rates", *_COMMON_TAIL]


def _row(ref, title, supplier, value="$ -", date="01/08/2026"):
    return [ref, title, supplier, "SOME ADDRESS", value, "Lump Sum",
            date, "5", "Year", "5", "Year", "01/08/2031", "Public Tender"]


class TestHeaderCarriesForwardAcrossPages:
    """The register's header repeats only at the start of each section - most
    pages are pure continuation data with no header row at all."""

    TABLES = [
        # Page 1: title rows, blank row, header, one data row.
        [
            ["GIPA Register - Class 1 Contracts", "", "", "", "", ""],
            ["This register records information...", "", "", "", "", ""],
            ["", "", "", "", "", ""],
            HEADER_PAYABLE,
            _row("ST3425", "Lease Management", "BLUEFIT PTY LTD"),
        ],
        # Page 2: continuation - no header at all.
        [
            _row("PQ3424", "Employee Assistance", "CONVERGE INTERNATIONAL"),
        ],
        # Page 3: a new section starts, with its own divider + a DIFFERENT
        # header label ("Awarded Amount" not "Awarded Payable").
        [
            ["Class 2 Contracts", "", "", "", "", ""],
            ["", "", "", "", "", ""],
            HEADER_AMOUNT,
            _row("VP3063", "Endpoint Hardware", "LENOVO PTY LTD"),
        ],
    ]

    def _rows(self):
        return pdf_tables_to_rows(self.TABLES)

    def test_continuation_page_data_is_not_dropped(self):
        suppliers = {r.awarded_to for r in self._rows()}
        assert "CONVERGE INTERNATIONAL" in suppliers

    def test_continuation_page_data_is_not_mistaken_for_a_header(self):
        # A naive per-page header scan sees no header on page 2 and, lacking
        # one, would treat PQ3424's own row as the header and drop it as data.
        assert len(self._rows()) == 3

    def test_a_second_header_label_variant_is_still_recognised(self):
        by_ref = {r.reference: r for r in self._rows()}
        assert by_ref["VP3063"].awarded_to == "LENOVO PTY LTD"

    def test_divider_and_blank_rows_produce_no_contract_row(self):
        assert all("GIPA Register" not in r.title for r in self._rows())
        assert all("Class 2" not in r.title for r in self._rows())


class TestPanelDisambiguation:
    """One reference can cover many suppliers (a standing-offer panel) or the
    same supplier under two unrelated contracts. Both must not collide onto
    one OCID-bearing reference, or every row past the first silently vanishes
    on ingest - 183 of 308 real rows would be lost without this."""

    def test_one_reference_many_suppliers_gets_a_distinct_reference_each(self):
        tables = [[
            HEADER_PAYABLE,
            _row("RCL3233", "Consulting Panel", "FIRM A"),
            _row("RCL3233", "Consulting Panel", "FIRM B"),
            _row("RCL3233", "Consulting Panel", "FIRM C"),
        ]]
        rows = pdf_tables_to_rows(tables)
        refs = {r.reference for r in rows}
        assert len(refs) == 3
        assert all(r.startswith("RCL3233-") for r in refs)

    def test_same_supplier_different_title_under_one_reference_still_disambiguates(self):
        # The real case: Origin Energy holds reference C4000 for two separate
        # gas contracts. Keying disambiguation on supplier alone would give
        # both rows the identical tag and they would still collide.
        tables = [[
            HEADER_PAYABLE,
            _row("C4000", "GAS Whitlam Centre - Large Site", "ORIGIN ENERGY", date="01/08/2022"),
            _row("C4000", "Retail Supply of Natural Gas (Small Sites)", "ORIGIN ENERGY", date="01/07/2020"),
        ]]
        rows = pdf_tables_to_rows(tables)
        assert len({r.reference for r in rows}) == 2

    def test_an_ordinary_single_contract_reference_is_left_exactly_as_printed(self):
        # No collision risk, no reason to mangle a human-readable reference.
        tables = [[HEADER_PAYABLE, _row("ST3425", "Lease Management", "BLUEFIT PTY LTD")]]
        rows = pdf_tables_to_rows(tables)
        assert rows[0].reference == "ST3425"

    def test_disambiguated_rows_still_produce_distinct_ocids_downstream(self):
        from opencontractau.transformers.council import row_to_release
        tables = [[
            HEADER_PAYABLE,
            _row("RCL3233", "Consulting Panel", "FIRM A"),
            _row("RCL3233", "Consulting Panel", "FIRM B"),
        ]]
        rows = pdf_tables_to_rows(tables)
        ocids = {row_to_release(r, seq=i).ocid for i, r in enumerate(rows, 1)}
        assert len(ocids) == 2


class TestValueDateAndMethodAreCaptured:
    def test_a_real_dollar_value_parses(self):
        tables = [[HEADER_PAYABLE, _row("PQ3392", "Elevator Maintenance", "KONE ELEVATORS", value="$ 152,544.00")]]
        row = pdf_tables_to_rows(tables)[0]
        assert int(row.value_aud) == 152_544

    def test_an_undisclosed_value_is_none_not_zero(self):
        tables = [[HEADER_PAYABLE, _row("ST3425", "Lease Management", "BLUEFIT", value="$ -")]]
        row = pdf_tables_to_rows(tables)[0]
        assert row.value_aud is None

    def test_procurement_method_is_captured(self):
        row = pdf_tables_to_rows([[HEADER_PAYABLE, _row("ST3425", "x", "y")]])[0]
        assert row.procurement_method == "Public Tender"


class TestNoHeaderSeenYet:
    def test_data_before_any_header_is_skipped_not_crashed(self):
        tables = [[_row("ST3425", "Lease Management", "BLUEFIT PTY LTD")]]
        assert pdf_tables_to_rows(tables) == []

    def test_a_row_with_no_supplier_is_skipped(self):
        tables = [[HEADER_PAYABLE, _row("ST3425", "Lease Management", "")]]
        assert pdf_tables_to_rows(tables) == []

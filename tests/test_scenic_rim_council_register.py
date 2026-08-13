"""Scenic Rim Regional Council PDF register parsing.

The register moved from an HTML table to a PDF (see the scraper's module
docstring); this covers the 4-column Date | Contractor | Description |
Value shape, header repeated on every page, no reference column.
"""

from opencontractau.scrapers.qld.councils.scenic_rim import pdf_tables_to_rows

HEADER = ["Date", "Contractor", "Description of Goods or Services", "Value (GST\nInclusive)"]


class TestPdfTablesToRows:
    def test_parses_a_data_row_under_its_page_header(self):
        tables = [[
            HEADER,
            ["", "", "", ""],  # blank divider row seen in the real PDF
            ["4/12/2025", "AGS Civil Pty Ltd", "Footpath Construction", "$468,985"],
        ]]
        rows = pdf_tables_to_rows(tables)
        assert len(rows) == 1
        row = rows[0]
        assert row.awarded_to == "AGS Civil Pty Ltd"
        assert row.title == "Footpath Construction"
        assert row.value_aud == 468985
        assert (row.award_date.year, row.award_date.month, row.award_date.day) == (2025, 12, 4)
        assert row.reference is None

    def test_header_repeats_on_every_page(self):
        tables = [
            [HEADER, ["4/12/2025", "AGS Civil Pty Ltd", "Footpath Construction", "$468,985"]],
            [HEADER, ["16/12/2025", "ARO Industries Pty Ltd", "Landslip Remediation", "$957,735"]],
        ]
        rows = pdf_tables_to_rows(tables)
        assert {r.awarded_to for r in rows} == {"AGS Civil Pty Ltd", "ARO Industries Pty Ltd"}

    def test_rows_without_a_contractor_are_skipped(self):
        tables = [[HEADER, ["4/12/2025", "", "Footpath Construction", "$468,985"]]]
        assert pdf_tables_to_rows(tables) == []

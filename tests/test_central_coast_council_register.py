"""Central Coast Council PDF register parsing.

Header does not repeat on every page (unlike Scenic Rim), so this reuses
Liverpool's carry-forward shape. ABN is disclosed here, unusually for an
NSW council register - worth its own assertion since it feeds the
supplier identity spine directly instead of a post-ingest name match.
"""

from opencontractau.scrapers.nsw.councils.central_coast import pdf_tables_to_rows

HEADER = [
    "Contract\nNumber", "Contract Title", "Contract Description", "Contractor", "ABN",
    "Organisation Business Address", "Contract Value\n(Ex GST)", "Variation Value\n(Ex GST)",
    "Contract Start Date", "Contract End Date", "Extension Options\nAvailable",
    "GIPA\nClassification", "GIPA Withhold\nValue", "Sourcing\nMethod",
]


def _row(ref, title, supplier, abn, value, start, end="", method="Open Tender"):
    return [ref, title, title, supplier, abn, "SOME ADDRESS", value, "$0",
            start, end, "", "Class 1", "No", method]


class TestPdfTablesToRows:
    def test_parses_a_data_row_including_abn(self):
        tables = [[
            HEADER,
            _row("CPA1432", "Consolidated Printer Services", "Colourworks Australia Pty Ltd",
                 "18159795507", "$3,102,000", "01/12/2019", "08/12/2025"),
        ]]
        rows = pdf_tables_to_rows(tables)
        assert len(rows) == 1
        row = rows[0]
        assert row.reference == "CPA1432"
        assert row.awarded_to == "Colourworks Australia Pty Ltd"
        assert row.supplier_abn == "18159795507"
        assert row.value_aud == 3102000
        assert (row.award_date.year, row.award_date.month, row.award_date.day) == (2019, 12, 1)
        assert (row.end_date.year, row.end_date.month, row.end_date.day) == (2025, 12, 8)

    def test_header_does_not_repeat_but_data_still_carries_forward(self):
        # Real PDF: only the first page of a section has the header row.
        tables = [
            [HEADER, _row("CPA1432", "Printer Services", "Colourworks", "18159795507", "$3,102,000", "01/12/2019")],
            [_row("CPA6470", "CRM Licence", "Acme Software Pty Ltd", "11122233344", "$120,000", "01/01/2023")],
        ]
        rows = pdf_tables_to_rows(tables)
        assert {r.awarded_to for r in rows} == {"Colourworks", "Acme Software Pty Ltd"}

    def test_data_row_before_any_header_is_dropped_not_misread(self):
        tables = [[_row("CPA1432", "Printer Services", "Colourworks", "18159795507", "$3,102,000", "01/12/2019")]]
        assert pdf_tables_to_rows(tables) == []

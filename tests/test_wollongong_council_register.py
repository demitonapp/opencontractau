"""Wollongong City Council register parsing.

Wollongong splits its GIPA register into three classes, each a different
shape: Class 1 is a Funnelback CSV export (no per-contract page needed);
Classes 2 and 3 have no bulk export and are scraped from a list page (link
only, no supplier/value) plus one detail page per contract.

Fixtures below are trimmed from the real live data (2026-08-13): CSV column
names, and the doubled sr-only markup on the detail page, are copied
verbatim.
"""

import pytest

from opencontractau.scrapers.nsw.councils.wollongong import (
    csv_text_to_rows,
    detail_fields_to_row,
    discover_detail_links,
)

CSV_HEADER = (
    "Contract,Description,Narration,Start_Date,End_Date,Total_Length_Days,"
    "Contract_Type,Contractor_Name,Contractor_Address,Contractor_Suburb,"
    "Contractor_State,Contractor_Postcode,"
    "Related_Companies_which_the_contractor_has_an_interestRelated_Companies_which_the_contractor_has_an_interest,"
    "Method_of_Tendering,"
    "Estimated_Contract_Amount_Payable_inc_of_GST_over_the_life_of_contract,"
    "Particulars__Project__Goods_"
)


def _csv_row(contract, narration, contractor, start="01-Aug-2016", end="31-Jul-2026",
             amount="1705000", method="Open Tender", particulars="Lease of Real Property"):
    return (
        f'{contract},E4557,"{narration}",{start},{end},3651,'
        f'"Class 1 - Procurement greater than $150K",{contractor},'
        f'"PO Box 1",WOLLONGONG,NSW,2500,NA,{method},{amount},"{particulars}"'
    )


class TestClass1Csv:
    def _rows(self, *data_rows):
        return csv_text_to_rows("\n".join([CSV_HEADER, *data_rows]))

    def test_a_real_row_parses_with_every_field(self):
        row = self._rows(_csv_row("CN100265", "Waste Collection Services", "PBLB Pty Ltd"))[0]
        assert row.reference == "CN100265"
        assert row.awarded_to == "PBLB Pty Ltd"
        assert row.title == "Waste Collection Services"
        assert int(row.value_aud) == 1_705_000
        assert row.award_date.strftime("%Y-%m-%d") == "2016-08-01"
        assert row.procurement_method == "Open Tender"

    def test_dash_abbreviated_month_dates_parse(self):
        # This is Wollongong's own export format, not covered before this
        # scraper - "%d-%b-%Y" is distinct from the existing "%d-%m-%Y".
        row = self._rows(_csv_row("CN1", "x", "y", start="27-Sep-2032", end="27-Sep-2032"))[0]
        assert row.award_date.year == 2032
        assert row.end_date.month == 9

    def test_a_row_missing_a_contractor_is_skipped(self):
        assert self._rows(_csv_row("CN1", "x", "")) == []

    def test_narration_is_preferred_over_the_internal_description_code(self):
        # Description ("E4557") is an internal reference code, not a title -
        # Narration carries the real subject line.
        row = self._rows(_csv_row("CN1", "Organics Processing Contract", "Acme"))[0]
        assert row.title == "Organics Processing Contract"


DETAIL_HTML = """
<table class="register">
<thead><tr><th><span class="sr-only">Contract Number</span>Contract Number</th>
<td><span class="sr-only">Contract Number</span>N1000043</td></tr></thead>
<thead><tr><th><span class="sr-only">Contract Description</span>Contract Description</th>
<td><span class="sr-only">Contract Description</span>128623 - Wongawilli Hall Refurbishment</td></tr></thead>
<thead><tr><th><span class="sr-only">Contractor</span>Contractor</th>
<td><span class="sr-only">Contractor</span>Batmac Constructions Pty Ltd</td></tr></thead>
<thead><tr><th><span class="sr-only">Start Date</span>Start Date</th>
<td><span class="sr-only">Start Date</span>31-Jul-2026</td></tr></thead>
<thead><tr><th><span class="sr-only">End Date</span>End Date</th>
<td><span class="sr-only">End Date</span>30-Oct-2026</td></tr></thead>
<thead><tr><th><span class="sr-only">Estimated contract amount (incl. GST)</span>Estimated contract amount (incl. GST)</th>
<td><span class="sr-only">Estimated contract amount (incl. GST)</span>$788,000.00</td></tr></thead>
<thead><tr><th><span class="sr-only">Method of Tendering</span>Method of Tendering</th>
<td><span class="sr-only">Method of Tendering</span>Negotiated Contract</td></tr></thead>
<thead><tr><th><span class="sr-only">Particulars of Project / Goods / Services Provided, or Real Property to be Leased or Transferred Under Contract</span>Particulars of Project / Goods / Services Provided, or Real Property to be Leased or Transferred Under Contract</th>
<td><span class="sr-only">Particulars of Project / Goods / Services Provided, or Real Property to be Leased or Transferred Under Contract</span>Wongawilli Hall Refurbishment</td></tr></thead>
</table>
"""

DETAIL_HTML_NO_PARTICULARS = DETAIL_HTML.replace(">Wongawilli Hall Refurbishment<", ">N/A<")


class TestClass2Or3DetailPage:
    def test_every_field_is_captured(self):
        row = detail_fields_to_row(DETAIL_HTML)
        assert row.reference == "N1000043"
        assert row.awarded_to == "Batmac Constructions Pty Ltd"
        assert int(row.value_aud) == 788_000
        assert row.start_date.strftime("%Y-%m-%d") == "2026-07-31"
        assert row.end_date.strftime("%Y-%m-%d") == "2026-10-30"
        assert row.procurement_method == "Negotiated Contract"

    def test_the_particulars_field_wins_over_the_reference_stuffed_description(self):
        # "Contract Description" is "128623 - Wongawilli Hall Refurbishment" -
        # a reference number glued to the title. Particulars carries the
        # clean subject line and is preferred when present.
        assert detail_fields_to_row(DETAIL_HTML).title == "Wongawilli Hall Refurbishment"

    def test_falls_back_to_contract_description_when_particulars_is_not_disclosed(self):
        row = detail_fields_to_row(DETAIL_HTML_NO_PARTICULARS)
        assert row.title == "128623 - Wongawilli Hall Refurbishment"

    def test_sr_only_label_duplication_does_not_leak_into_values(self):
        # Without stripping the sr-only span, awarded_to would come back as
        # "Contractor Batmac Constructions Pty Ltd", not the clean name.
        assert detail_fields_to_row(DETAIL_HTML).awarded_to == "Batmac Constructions Pty Ltd"

    def test_a_page_missing_the_contractor_field_yields_no_row(self):
        broken = DETAIL_HTML.replace("Batmac Constructions Pty Ltd", "")
        assert detail_fields_to_row(broken) is None


class TestDiscoverDetailLinks:
    def test_distinct_links_are_found_in_order(self):
        base = "/council/.../contracts-register-class-2/contracts-register-class-2-details"
        html = (
            f'<a href="{base}?contract=N1">a</a>'
            f'<a href="{base}?contract=N2">b</a>'
            f'<a href="{base}?contract=N1">a again</a>'
        )
        assert discover_detail_links(html) == [f"{base}?contract=N1", f"{base}?contract=N2"]

    def test_no_links_yields_an_empty_list_not_an_error(self):
        assert discover_detail_links("<p>nothing here</p>") == []

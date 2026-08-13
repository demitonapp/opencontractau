"""Northern Beaches Council JSON register parsing.

The register page renders no table at all - the real data comes from the
contractdata.ashx JSON endpoint behind its iframe (see the scraper's
module docstring). Fixture below mirrors the real response shape.
"""

from decimal import Decimal

from opencontractau.scrapers.nsw.councils.northern_beaches import _parse_contracts


def _payload(**overrides) -> dict:
    contract = {
        "Class": "CLASS 1", "ID": 924, "Number": "000924",
        "Name": "Supply and maintenance of unattended parking fee",
        "SuccessfulTenderer": "Easypark ANZ Pty Ltd",
        "TendererAddress": "Level 7, Melbourne VIC",
        "StartDate": "02/01/2026", "StartDateSort": "2026-01-02",
        "EndDate": "02/01/2029", "EndDateSort": "2029-01-02",
        "AmountPayable": 959219.8,
        "TenderMethod": "Request for Tender",
        "Show": True,
    }
    contract.update(overrides)
    return {"Error": "", "Contract": [contract]}


class TestParseContracts:
    def test_maps_the_real_field_shape(self):
        rows = _parse_contracts(_payload())
        assert len(rows) == 1
        row = rows[0]
        assert row.reference == "000924"
        assert row.awarded_to == "Easypark ANZ Pty Ltd"
        assert row.value_aud == Decimal("959219.8")
        assert row.award_date.isoformat().startswith("2026-01-02")
        assert row.end_date.isoformat().startswith("2029-01-02")

    def test_uses_start_date_sort_not_the_locale_formatted_start_date(self):
        # StartDate is dd/mm/yyyy (locale-ambiguous); StartDateSort is ISO -
        # a row with a day > 12 would silently mis-parse if the wrong field
        # were used.
        row = _parse_contracts(_payload(StartDate="25/03/2026", StartDateSort="2026-03-25"))[0]
        assert (row.award_date.year, row.award_date.month, row.award_date.day) == (2026, 3, 25)

    def test_rows_without_a_successful_tenderer_are_skipped(self):
        assert _parse_contracts(_payload(SuccessfulTenderer="")) == []

    def test_an_api_error_yields_no_rows(self):
        assert _parse_contracts({"Error": "boom", "Contract": []}) == []

"""Unit tests for the TAS contract award transformer."""

from decimal import Decimal
from pathlib import Path

from opencontractsau.transformers.tas import (
    _extract_fields,
    _extract_period,
    _extract_suppliers,
    _normalise_method,
    is_not_found,
    parse_detail_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tas_detail_14000.html"
SAMPLE_HTML = FIXTURE.read_text(encoding="utf-8")


class TestNormaliseMethod:
    def test_rft(self):
        assert _normalise_method("Request for Tender") == "open"

    def test_rfq(self):
        assert _normalise_method("Request for Quote") == "selective"

    def test_direct(self):
        assert _normalise_method("Direct Source") == "limited"


class TestIsNotFound:
    def test_real_page_is_not_404(self):
        assert is_not_found(SAMPLE_HTML) is False

    def test_404_marker_detected(self):
        assert is_not_found("<html>The contract has not been found</html>") is True


class TestExtractFields:
    def test_finds_procurement_title(self):
        fields = _extract_fields(SAMPLE_HTML)
        assert "ProcurementTitle" in fields
        assert fields["ProcurementTitle"][0] == "89 Cambridge Park Road Fitout"

    def test_finds_unique_tender_id(self):
        fields = _extract_fields(SAMPLE_HTML)
        assert fields["UniqueTenderId"][0] == "DPOJ0160"

    def test_finds_awarded_date(self):
        fields = _extract_fields(SAMPLE_HTML)
        assert fields["AwardedDate"][0] == "20/11/2025"

    def test_finds_procurement_method(self):
        fields = _extract_fields(SAMPLE_HTML)
        assert fields["ProcurementMethod"][0] == "Request for Tender"

    def test_agency_is_multi_line(self):
        fields = _extract_fields(SAMPLE_HTML)
        agency = fields.get("Agency", [])
        assert len(agency) >= 2
        assert "Department of Justice" in agency[0]


class TestExtractPeriod:
    def test_period_parsed(self):
        start, end = _extract_period(SAMPLE_HTML)
        assert start is not None
        assert start.year == 2025
        assert start.month == 11
        assert start.day == 8
        assert end is not None
        assert end.year == 2027


class TestExtractSuppliers:
    def test_supplier_name_and_address(self):
        suppliers = _extract_suppliers(SAMPLE_HTML)
        assert len(suppliers) >= 1
        s = suppliers[0]
        assert s["name"] == "Tascon Constructions Pty Ltd"
        assert s["tasmanian"] == "Yes"
        assert "Albert Road" in s["address"]
        assert s["amount"] == "$3,025,440"


class TestParseDetailHtml:
    def test_full_release(self):
        release = parse_detail_html(SAMPLE_HTML, contract_id=14000)
        assert release is not None
        assert release.ocid == "ocau-tas-DPOJ0160"
        assert release.buyer.name == "Department of Justice"
        assert release.awards[0].value.amount == Decimal("3025440")
        suppliers = release.awards[0].suppliers
        assert len(suppliers) == 1
        assert suppliers[0].name == "Tascon Constructions Pty Ltd"
        # ABN is not published by TAS - confirm we tolerate that gracefully
        assert suppliers[0].identifier is None

    def test_period_in_release(self):
        release = parse_detail_html(SAMPLE_HTML, contract_id=14000)
        assert release is not None
        period = release.awards[0].contract_period
        assert period is not None
        assert period.start_date.year == 2025
        assert period.end_date.year == 2027

    def test_source_metadata(self):
        release = parse_detail_html(SAMPLE_HTML, contract_id=14000)
        assert release is not None
        assert release.source["tasContractId"] == 14000
        assert release.source["uniqueTenderId"] == "DPOJ0160"
        assert release.source.get("hasTasmanianSupplier") is True

    def test_not_found_returns_none(self):
        assert parse_detail_html("The contract has not been found", contract_id=99999) is None

"""Unit tests for the TenderSearch parser and transformer (VIC fixture)."""

from decimal import Decimal
from pathlib import Path

from au_procurement.scrapers._tendersearch.parser import (
    parse_contract_ids,
    parse_detail_html,
)
from au_procurement.scrapers._tendersearch.transformer import (
    _clean_abn,
    _normalise_method,
    _parse_date,
    _parse_value,
    detail_to_release,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vic_detail_229501.html"
SAMPLE_HTML = FIXTURE.read_text(encoding="utf-8")


class TestParseContractIds:
    def test_finds_row_ids(self):
        sample = '<tr id="contractRow123"><td>x</td></tr><tr id="contractRow456">'
        assert parse_contract_ids(sample) == [123, 456]

    def test_no_matches(self):
        assert parse_contract_ids("<html><body>no rows</body></html>") == []


class TestParseDate:
    def test_tender_search_format(self):
        dt = _parse_date("7 Mar 2027")
        assert dt is not None
        assert dt.year == 2027
        assert dt.month == 3
        assert dt.day == 7

    def test_full_month(self):
        dt = _parse_date("30 July 2027")
        assert dt is not None
        assert dt.month == 7

    def test_none(self):
        assert _parse_date(None) is None


class TestParseValue:
    def test_dollar_amount(self):
        assert _parse_value("$176,385.00") == Decimal("176385.00")

    def test_estimate_suffix_stripped(self):
        assert _parse_value("$176,385.00 (Estimate)") == Decimal("176385.00")

    def test_empty(self):
        assert _parse_value("") is None


class TestCleanAbn:
    def test_eleven_digits(self):
        assert _clean_abn("56607067925") == "56607067925"

    def test_with_spaces(self):
        assert _clean_abn("56 607 067 925") == "56607067925"

    def test_invalid(self):
        assert _clean_abn("123") is None


class TestNormaliseMethod:
    def test_rft(self):
        assert _normalise_method("Request for Tender") == "open"

    def test_selective(self):
        assert _normalise_method("Standing offer panel") == "selective"

    def test_direct(self):
        assert _normalise_method("Direct sourced") == "limited"

    def test_unknown(self):
        assert _normalise_method("Mystery") is None


class TestParseDetailHtmlAndTransform:
    def test_fields_extracted(self):
        detail = parse_detail_html(SAMPLE_HTML)
        assert detail.fields.get("Public Body") == "Department of Education"
        assert detail.fields.get("Contract Number") == "CWF_25-26_00159-CWF_25-26_00658-CWF_25-26_0081"
        assert detail.fields.get("Starting Date") == "7 Mar 2027"
        assert detail.fields.get("Expiry Date") == "30 July 2027"
        assert "Total Value of the Contract" in detail.fields

    def test_supplier_with_abn(self):
        detail = parse_detail_html(SAMPLE_HTML)
        assert len(detail.suppliers) >= 1
        s = detail.suppliers[0]
        assert s.name == "Architecture Caisson"
        assert s.abn == "56607067925"
        assert s.acn == "607067925"

    def test_release_mapping(self):
        detail = parse_detail_html(SAMPLE_HTML)
        release = detail_to_release(detail, contract_id=229501, jurisdiction_code="vic")
        assert release is not None
        assert release.ocid.startswith("ocau-vic-")
        assert release.buyer.name == "Department of Education"
        award = release.awards[0]
        assert award.value is not None
        assert award.value.amount == Decimal("176385.00")
        assert release.source.get("valueIsEstimate") is True
        assert len(award.suppliers) == 1
        assert award.suppliers[0].identifier.scheme == "AU-ABN"
        assert award.suppliers[0].identifier.id == "56607067925"

    def test_period_in_release(self):
        detail = parse_detail_html(SAMPLE_HTML)
        release = detail_to_release(detail, contract_id=229501, jurisdiction_code="vic")
        assert release is not None
        period = release.awards[0].contract_period
        assert period is not None
        assert period.start_date.year == 2027
        assert period.end_date.year == 2027

    def test_empty_detail_returns_none(self):
        from au_procurement.scrapers._tendersearch.parser import DetailFields
        empty = DetailFields()
        release = detail_to_release(empty, contract_id=1, jurisdiction_code="vic")
        assert release is None

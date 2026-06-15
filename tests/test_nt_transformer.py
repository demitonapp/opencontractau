"""Unit tests for the NT QTOL transformer."""

from decimal import Decimal
from pathlib import Path

from opencontractsau.transformers.nt import (
    _normalise_method,
    is_not_found,
    parse_detail_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "nt_detail_25992.html"
SAMPLE_HTML = FIXTURE.read_text(encoding="utf-8")


class TestNormaliseMethod:
    def test_public(self):
        assert _normalise_method("Public") == "open"

    def test_select(self):
        assert _normalise_method("Select Tender") == "selective"

    def test_limited(self):
        assert _normalise_method("Limited tendering") == "limited"

    def test_none(self):
        assert _normalise_method(None) is None


class TestIsNotFound:
    def test_real_page(self):
        assert is_not_found(SAMPLE_HTML) is False

    def test_marker(self):
        assert is_not_found("Tender not found on this server") is True


class TestParseDetailHtml:
    def test_full_release(self):
        release = parse_detail_html(SAMPLE_HTML, contract_id=25992)
        assert release is not None
        assert release.ocid == "ocau-nt-RFT0661"
        assert release.buyer.name == "Power and Water Corporation"
        award = release.awards[0]
        assert "Cybersecurity Risk Platform" in (award.title or "")
        assert award.value is not None
        assert award.value.amount == Decimal("446425.94")
        assert len(award.suppliers) == 1
        assert award.suppliers[0].name == "UPGUARD, INC"
        # NT does not publish ABN
        assert award.suppliers[0].identifier is None

    def test_source_metadata(self):
        release = parse_detail_html(SAMPLE_HTML, contract_id=25992)
        assert release is not None
        assert release.source["ntContractId"] == 25992
        assert release.source["tenderNumber"] == "RFT0661"
        assert release.source["category"] == "Information Technology"
        assert release.source.get("isTerritoryEnterprise") is False
        assert release.source.get("isAboriginalEnterprise") is False

    def test_not_found_returns_none(self):
        assert parse_detail_html("Tender not found", contract_id=99999) is None

    def test_tender_status_complete(self):
        release = parse_detail_html(SAMPLE_HTML, contract_id=25992)
        assert release is not None
        assert release.tender.procurement_method == "open"

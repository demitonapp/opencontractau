"""Unit tests for the NT QTOL transformer."""

from decimal import Decimal
from pathlib import Path

from opencontractau.transformers.nt import (
    _extract_supplier,
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
        # This specific supplier is a US company with no ABN - not "NT
        # doesn't publish ABN" as a blanket rule, which was never true (see
        # TestExtractSupplierAbn below) and cost 40/40 Australian suppliers
        # their identifier until 2026-08-13.
        assert award.suppliers[0].identifier is None
        assert award.suppliers[0].address.street_address == (
            "650 Castro St, Se 120-387, MOUNTAIN VIEW CALIFORNIA 94041 UNITED STATES"
        )

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


def _award_body(*lines: str) -> str:
    """Build a minimal awarded-contractors block. First line is the bold
    legal name; remaining lines are supporting-2 (real markup shape)."""
    name_p = f'<p class="fw-bold m-0">{lines[0]}</p>'
    rest = "".join(f'<p class="m-0 font-color-supporting-2">{l}</p>' for l in lines[1:])
    return name_p + rest


class TestExtractSupplierAbn:
    """Real shapes, verified live 2026-08-13 against 40 awarded NT tenders.

    All three CSS-class-distinguishable variants: with a trading name, with-
    out one, and the foreign-supplier case with no ABN at all.
    """

    def test_abn_and_address_with_no_trading_name(self):
        # HCS Constructions NT Pty Ltd, contract 26094.
        body = _award_body(
            "HCS Constructions NT Pty Ltd",
            "ABN: 34 651 169 907",
            "PO Box 2068, COOLALINGA NT 0839",
            "Territory Enterprise: Yes",
        )
        info = _extract_supplier(body)
        assert info["abn"] == "34651169907"
        assert info["address"] == "PO Box 2068, COOLALINGA NT 0839"

    def test_abn_and_address_with_a_trading_name_present(self):
        # Remote Civil Pty Ltd t/a HiQA Road Asset Management, contract 26020.
        # The bug this guards: a position-only rule read the trading name as
        # the address and then dropped the ABN and the real address both,
        # since its "address" slot was already (wrongly) filled.
        body = "".join([
            '<p class="fw-bold m-0">Remote Civil Pty Ltd</p>',
            '<p class="m-0">HiQA Road Asset Management</p>',
            '<p class="m-0 font-color-supporting-2">ABN: 48 145 594 252</p>',
            '<p class="m-0 font-color-supporting-2">PO Box 35964, WINNELLIE NT 0821</p>',
        ])
        info = _extract_supplier(body)
        assert info["abn"] == "48145594252"
        assert info["address"] == "PO Box 35964, WINNELLIE NT 0821"

    def test_no_abn_line_at_all_still_finds_the_address(self):
        # UpGuard, Inc - a foreign supplier the source never assigns an ABN.
        body = "".join([
            '<p class="fw-bold m-0">UPGUARD, INC</p>',
            '<p class="m-0">UpGuard</p>',
            '<p class="m-0 font-color-supporting-2">650 Castro St, MOUNTAIN VIEW CA</p>',
        ])
        info = _extract_supplier(body)
        assert info["abn"] is None
        assert info["address"] == "650 Castro St, MOUNTAIN VIEW CA"

    def test_a_malformed_abn_line_is_dropped_not_guessed(self):
        body = _award_body("Acme Pty Ltd", "ABN: 123", "PO Box 1, DARWIN NT 0800")
        info = _extract_supplier(body)
        assert info["abn"] is None
        # The malformed ABN line must not be mistaken for the address either.
        assert info["address"] == "PO Box 1, DARWIN NT 0800"

    def test_flags_are_still_captured(self):
        body = _award_body(
            "Acme Pty Ltd", "ABN: 34 651 169 907", "PO Box 1, DARWIN NT 0800",
            "Territory Enterprise: Yes", "Aboriginal Enterprise: No",
            "Women Owned: Information not available",
        )
        info = _extract_supplier(body)
        assert info["flags"] == {"isTerritoryEnterprise": True, "isAboriginalEnterprise": False}

"""Unit tests for the ACT Contracts Register transformer."""

from decimal import Decimal

from opencontractau.transformers.act import (
    _clean_abn,
    _normalise_method,
    _parse_amount,
    _parse_socrata_datetime,
    _parse_unspsc,
    _agency_id,
    _make_ocid,
    record_to_release,
)


def _sample_record(**overrides) -> dict:
    base = {
        "contract_number": "M16/43",
        "contract_title": "Computer Information Systems Audit Services 2016",
        "brief_description_of_contract": "Computer Information Systems A",
        "contract_type": "contract",
        "procurement_methodology": "select",
        "social_procurement": "No",
        "procurement_type": "consultancy",
        "contractor_name": "Axiom Associates",
        "abn": "98121216662",
        "original_amount": "101745.00",
        "contract_amount": "101745.00",
        "gst": "Inclusive",
        "execution_date": "2016-06-20T00:00:00.000",
        "expiry_date": "2016-09-30T00:00:00.000",
        "confidential_text": "Yes",
        "confidential_text_brief": "price",
        "unspsc_classification": "Management and Business Professionals and Administrative Services(UNSPSC:80000000)",
        "directorate": "ACT Audit Office",
        "prequalification_requested": "No",
        "infrastructure_or_goods_services": "Goods & Services",
        "small_to_medium_enterprise": "Yes",
        "whole_of_government_contract": "No",
        "exemption_from_quotation": "No",
        "active_certification": "No",
        "ire_obligations": "No",
    }
    base.update(overrides)
    return base


class TestNormaliseMethod:
    def test_select(self):
        assert _normalise_method("select") == "selective"

    def test_open(self):
        assert _normalise_method("Open Tender") == "open"

    def test_single(self):
        assert _normalise_method("Single Select") == "limited"

    def test_exemption(self):
        assert _normalise_method("Exemption from Quotation") == "limited"

    def test_panel(self):
        assert _normalise_method("Standing Offer Panel") == "selective"

    def test_none(self):
        assert _normalise_method(None) is None

    def test_unknown_defaults_to_limited(self):
        assert _normalise_method("Some Unknown Method") == "limited"


class TestParseSocrataDatetime:
    def test_with_milliseconds(self):
        dt = _parse_socrata_datetime("2016-06-20T00:00:00.000")
        assert dt is not None
        assert dt.year == 2016
        assert dt.month == 6
        assert dt.day == 20

    def test_without_milliseconds(self):
        dt = _parse_socrata_datetime("2016-06-20T00:00:00")
        assert dt is not None
        assert dt.year == 2016

    def test_date_only(self):
        dt = _parse_socrata_datetime("2016-06-20")
        assert dt is not None

    def test_garbage_returns_none(self):
        assert _parse_socrata_datetime("nope") is None

    def test_none(self):
        assert _parse_socrata_datetime(None) is None


class TestParseAmount:
    def test_with_decimals(self):
        assert _parse_amount("101745.00") == Decimal("101745.00")

    def test_integer(self):
        assert _parse_amount("50000") == Decimal("50000")

    def test_none(self):
        assert _parse_amount(None) is None

    def test_empty(self):
        assert _parse_amount("") is None


class TestParseUnspsc:
    def test_extracts_code_and_label(self):
        code, label = _parse_unspsc(
            "Management and Business Professionals and Administrative Services(UNSPSC:80000000)"
        )
        assert code == "80000000"
        assert label == "Management and Business Professionals and Administrative Services"

    def test_handles_spaces(self):
        code, _ = _parse_unspsc("Foo (UNSPSC: 12345678)")
        assert code == "12345678"

    def test_no_unspsc_marker(self):
        code, label = _parse_unspsc("Just a label")
        assert code is None
        assert label == "Just a label"

    def test_none(self):
        code, label = _parse_unspsc(None)
        assert code is None
        assert label is None


class TestCleanAbn:
    def test_eleven_digits(self):
        assert _clean_abn("98121216662") == "98121216662"

    def test_with_spaces(self):
        assert _clean_abn("98 121 216 662") == "98121216662"

    def test_invalid_length(self):
        assert _clean_abn("123") is None


class TestAgencyId:
    def test_slugifies_directorate(self):
        assert _agency_id("ACT Audit Office") == "au-act-directorate-act-audit-office"

    def test_collapses_special_chars(self):
        assert _agency_id("Chief Minister, Treasury & Economic Development Directorate") == \
            "au-act-directorate-chief-minister-treasury-economic-development-directorate"


class TestMakeOcid:
    def test_uses_contract_number(self):
        assert _make_ocid("M16/43", "fallback") == "ocau-act-M16-43"

    def test_sanitises_special_chars(self):
        ocid = _make_ocid("M16/43 (var.1)", "fallback")
        assert "/" not in ocid
        assert "(" not in ocid

    def test_falls_back_when_empty(self):
        ocid = _make_ocid(None, "Agency|Title|Supplier")
        assert ocid.startswith("ocau-act-")


class TestRecordToRelease:
    def test_full_record(self):
        release = record_to_release(_sample_record(), seq=1)
        assert release is not None
        assert release.ocid == "ocau-act-M16-43"
        assert release.buyer.name == "ACT Audit Office"
        assert len(release.awards) == 1
        award = release.awards[0]
        assert award.value.amount == Decimal("101745.00")
        assert award.suppliers[0].name == "Axiom Associates"
        assert award.suppliers[0].identifier.scheme == "AU-ABN"
        assert award.suppliers[0].identifier.id == "98121216662"

    def test_unspsc_in_source(self):
        release = record_to_release(_sample_record())
        assert release is not None
        assert release.source.get("unspscCode") == "80000000"

    def test_sme_flag_in_source(self):
        release = record_to_release(_sample_record(small_to_medium_enterprise="Yes"))
        assert release is not None
        assert release.source.get("isSME") is True

    def test_variation_tag(self):
        release = record_to_release(_sample_record(contract_type="variation"))
        assert release is not None
        assert release.tag == ["awardUpdate"]
        assert release.source.get("isVariation") is True

    def test_missing_abn_still_produces_release(self):
        release = record_to_release(_sample_record(abn=""))
        assert release is not None
        assert release.awards[0].suppliers[0].identifier is None

    def test_empty_record_returns_none(self):
        empty = {k: "" for k in _sample_record().keys()}
        assert record_to_release(empty) is None

    def test_contract_period(self):
        release = record_to_release(_sample_record())
        assert release is not None
        period = release.awards[0].contract_period
        assert period is not None
        assert period.start_date is not None
        assert period.end_date is not None

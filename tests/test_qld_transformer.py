"""Unit tests for the QLD TMR CSV transformer."""

from decimal import Decimal

import pytest

from au_procurement.transformers.qld import (
    _clean_abn,
    _make_ocid,
    _normalize_procurement_method,
    _parse_au_date,
    _parse_value,
    row_to_release,
)


def _sample_row(**overrides) -> dict[str, str]:
    base = {
        "Agency (Dept or Stat Body)": "Transport and Main Roads",
        "Agency address": "PO Box 1412 Brisbane QLD 4001",
        "Contract description/name": "North Coast Region - Tractor Slashing",
        "Award contract date": "21/05/2019",
        "Contract value": "875619",
        "Supplier name": "TUFF YARDS PTY LTD",
        "Supplier address": "4017",
        "Variation": "Yes",
        "Specific confidentiality provision used": "No",
        "Procurement method": "Selective",
        "Justification": "",
        "Form of contract": "",
        "Number of offers sought": "4",
        "Evaluation criteria and weightings": "",
        "Deliverables": "",
        "Contract milestones": "",
        "Contract performance management": "",
        "Contract reference number": "TMR-2019-001",
        "Contract category group": "Maintenance",
        "Parent contract number/SOA": "",
        "Commence date": "01/07/2019",
        "Supplier ABN": "12 345 678 901",
        "Finish date": "30/06/2024",
    }
    base.update(overrides)
    return base


class TestCleanAbn:
    def test_strips_spaces(self):
        assert _clean_abn("12 345 678 901") == "12345678901"

    def test_passthrough_plain(self):
        assert _clean_abn("12345678901") == "12345678901"

    def test_rejects_short(self):
        assert _clean_abn("1234567890") is None

    def test_rejects_empty(self):
        assert _clean_abn("") is None

    def test_rejects_none(self):
        assert _clean_abn(None) is None


class TestParseAuDate:
    def test_ddmmyyyy(self):
        dt = _parse_au_date("21/05/2019")
        assert dt is not None
        assert dt.year == 2019
        assert dt.month == 5
        assert dt.day == 21

    def test_iso(self):
        dt = _parse_au_date("2024-01-15")
        assert dt is not None
        assert dt.year == 2024

    def test_empty_returns_none(self):
        assert _parse_au_date("") is None

    def test_none_returns_none(self):
        assert _parse_au_date(None) is None

    def test_garbage_returns_none(self):
        assert _parse_au_date("not-a-date") is None


class TestParseValue:
    def test_plain_integer(self):
        assert _parse_value("875619") == Decimal("875619")

    def test_with_commas(self):
        assert _parse_value("1,234,567") == Decimal("1234567")

    def test_with_dollar(self):
        assert _parse_value("$1234.50") == Decimal("1234.50")

    def test_empty_returns_none(self):
        assert _parse_value("") is None


class TestNormalizeProcurementMethod:
    def test_selective(self):
        assert _normalize_procurement_method("Selective") == "selective"

    def test_open(self):
        assert _normalize_procurement_method("Open Tender") == "open"

    def test_limited(self):
        assert _normalize_procurement_method("Direct Source") == "limited"

    def test_none(self):
        assert _normalize_procurement_method(None) is None


class TestMakeOcid:
    def test_uses_contract_ref_when_present(self):
        ocid = _make_ocid("TMR-2019-001", "Agency", "Description")
        assert ocid == "ocau-qld-tmr-TMR-2019-001"

    def test_sanitizes_special_chars(self):
        ocid = _make_ocid("TMR/2019/001 A", "Agency", "Description")
        assert "/" not in ocid
        assert " " not in ocid

    def test_falls_back_to_fingerprint_when_no_ref(self):
        ocid = _make_ocid(None, "Agency X", "Description Y")
        assert ocid.startswith("ocau-qld-tmr-")
        assert len(ocid) > len("ocau-qld-tmr-")


class TestRowToRelease:
    def test_full_row(self):
        row = _sample_row()
        release = row_to_release(row, seq=1)
        assert release is not None
        assert release.ocid == "ocau-qld-tmr-TMR-2019-001"
        assert release.buyer is not None
        assert release.buyer.name == "Transport and Main Roads"
        assert len(release.awards) == 1
        award = release.awards[0]
        assert award.value is not None
        assert award.value.amount == Decimal("875619")
        assert award.value.currency == "AUD"
        assert len(award.suppliers) == 1
        supplier = award.suppliers[0]
        assert supplier.name == "TUFF YARDS PTY LTD"
        assert supplier.identifier is not None
        assert supplier.identifier.scheme == "AU-ABN"
        assert supplier.identifier.id == "12345678901"

    def test_variation_flag_in_source(self):
        row = _sample_row(**{"Variation": "Yes"})
        release = row_to_release(row)
        assert release is not None
        assert release.source.get("isVariation") is True

    def test_empty_row_returns_none(self):
        row = {k: "" for k in _sample_row().keys()}
        release = row_to_release(row)
        assert release is None

    def test_contract_period(self):
        row = _sample_row()
        release = row_to_release(row)
        assert release is not None
        award = release.awards[0]
        assert award.contract_period is not None
        assert award.contract_period.start_date is not None
        assert award.contract_period.end_date is not None

    def test_missing_abn_still_produces_release(self):
        row = _sample_row(**{"Supplier ABN": ""})
        release = row_to_release(row)
        assert release is not None
        assert release.awards[0].suppliers[0].identifier is None

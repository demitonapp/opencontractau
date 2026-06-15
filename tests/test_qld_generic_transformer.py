"""Unit tests for the QLD generic (multi-agency) transformer."""

from decimal import Decimal

from opencontractau.transformers.qld_generic import (
    _normalise_header,
    build_column_map,
    row_to_release,
)


class TestNormaliseHeader:
    def test_lowercase(self):
        assert _normalise_header("Agency (Dept or Stat Body)") == "agency (dept or stat body)"

    def test_strips_quotes(self):
        assert _normalise_header("'Contract value'") == "contract value"

    def test_collapses_whitespace(self):
        assert _normalise_header("  Contract   value  ") == "contract value"


class TestBuildColumnMap:
    def test_tmr_headers(self):
        headers = [
            "Agency (Dept or Stat Body)",
            "Contract description/name",
            "Award contract date",
            "Contract value",
            "Supplier name",
            "Supplier ABN",
        ]
        m = build_column_map(headers)
        assert m["agency"] == "Agency (Dept or Stat Body)"
        assert m["description"] == "Contract description/name"
        assert m["value"] == "Contract value"
        assert m["supplier_name"] == "Supplier name"
        assert m["supplier_abn"] == "Supplier ABN"

    def test_qfd_power_bi_headers(self):
        """QFD exports from Power BI with quoted Measures column."""
        headers = [
            "Description (Previously Used or Cost Element Category)",
            "Agency (Dept or Stat Body)2",
            "Award contract date",
            " '_Measures'[Contract value] ",
            "Supplier name",
            "Supplier ABN",
            "Variation to contract (Yes/No)",
        ]
        m = build_column_map(headers)
        assert m["agency"] == "Agency (Dept or Stat Body)2"
        assert m["description"] == "Description (Previously Used or Cost Element Category)"
        assert m["value"] == " '_Measures'[Contract value] "
        assert m["variation"] == "Variation to contract (Yes/No)"

    def test_missing_columns_omitted(self):
        m = build_column_map(["Some Random Header"])
        assert "supplier_name" not in m
        assert "value" not in m


class TestRowToReleaseGeneric:
    def _column_map(self):
        return {
            "agency": "Agency (Dept or Stat Body)",
            "supplier_name": "Supplier name",
            "supplier_abn": "Supplier ABN",
            "value": "Contract value",
            "description": "Contract description/name",
            "award_date": "Award contract date",
            "commence_date": "Commence date",
            "finish_date": "Finish date",
            "contract_ref": "Contract reference number",
            "procurement_method": "Procurement method",
            "variation": "Variation",
        }

    def _row(self, **overrides):
        base = {
            "Agency (Dept or Stat Body)": "Queensland Treasury",
            "Supplier name": "Acme Pty Ltd",
            "Supplier ABN": "12 345 678 901",
            "Contract value": "150000",
            "Contract description/name": "Treasury advisory services",
            "Award contract date": "15/03/2025",
            "Commence date": "01/04/2025",
            "Finish date": "31/03/2026",
            "Contract reference number": "QT-2025-001",
            "Procurement method": "Open",
            "Variation": "No",
        }
        base.update(overrides)
        return base

    def test_basic_mapping(self):
        release = row_to_release(
            row=self._row(),
            column_map=self._column_map(),
            agency_code="treasury",
            default_agency_name="Queensland Treasury",
            seq=1,
        )
        assert release is not None
        assert release.ocid == "ocau-qld-treasury-QT-2025-001"
        assert release.buyer.name == "Queensland Treasury"
        assert release.awards[0].value.amount == Decimal("150000")
        assert release.awards[0].suppliers[0].identifier.id == "12345678901"

    def test_variation_yes(self):
        release = row_to_release(
            row=self._row(Variation="Yes"),
            column_map=self._column_map(),
            agency_code="treasury",
            default_agency_name="Queensland Treasury",
        )
        assert release is not None
        assert release.source.get("isVariation") is True
        assert release.tag == ["awardUpdate"]

    def test_missing_contract_ref_uses_fingerprint(self):
        release = row_to_release(
            row=self._row(**{"Contract reference number": ""}),
            column_map=self._column_map(),
            agency_code="treasury",
            default_agency_name="Queensland Treasury",
        )
        assert release is not None
        assert release.ocid.startswith("ocau-qld-treasury-")
        assert "QT-2025-001" not in release.ocid

    def test_empty_row_returns_none(self):
        empty = {k: "" for k in self._row().keys()}
        release = row_to_release(
            row=empty,
            column_map=self._column_map(),
            agency_code="treasury",
            default_agency_name="Queensland Treasury",
        )
        assert release is None

"""Unit tests for the AusTender federal scraper (model conversion layer only - no network)."""

from datetime import date, datetime
from decimal import Decimal

from opencontractau.scrapers.federal.scraper import (
    _extract_abn,
    _parse_datetime,
    _parse_decimal,
    _raw_to_release,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_release(**overrides) -> dict:
    base = {
        "ocid": "ocds-n38h2q-ABC123",
        "id": "ocds-n38h2q-ABC123-award-20240601",
        "date": "2024-06-01T00:00:00Z",
        "tag": ["award"],
        "buyer": {"id": "gov-dot", "name": "Dept of Transport"},
        "parties": [
            {
                "id": "gov-dot",
                "name": "Dept of Transport",
                "roles": ["procuringEntity"],
            },
            {
                "id": "sup-001",
                "name": "Acme Civil Pty Ltd",
                "roles": ["supplier"],
                "additionalIdentifiers": [
                    {"scheme": "AU-ABN", "id": "12345678901"},
                ],
            },
        ],
        "awards": [
            {
                "id": "award-001",
                "title": "Bridge maintenance",
                "status": "active",
                "date": "2024-05-30T00:00:00Z",
                "value": {"amount": 500000, "currency": "AUD"},
                "suppliers": [{"id": "sup-001", "name": "Acme Civil Pty Ltd"}],
            }
        ],
        "contracts": [
            {
                "id": "contract-001",
                "awardID": "award-001",
                "title": "Bridge maintenance contract",
                "status": "active",
                "value": {"amount": 500000, "currency": "AUD"},
                "period": {
                    "startDate": "2024-06-01T00:00:00Z",
                    "endDate": "2025-06-01T00:00:00Z",
                },
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _extract_abn
# ---------------------------------------------------------------------------


class TestExtractAbn:
    def test_from_additional_identifiers(self):
        entity = {"additionalIdentifiers": [{"scheme": "AU-ABN", "id": "12345678901"}]}
        assert _extract_abn(entity) == "12345678901"

    def test_scheme_case_insensitive(self):
        entity = {"additionalIdentifiers": [{"scheme": "au-abn", "id": "98765432100"}]}
        assert _extract_abn(entity) == "98765432100"

    def test_falls_back_to_identifier(self):
        entity = {"identifier": {"scheme": "AU-ABN", "id": "11223344556"}}
        assert _extract_abn(entity) == "11223344556"

    def test_wrong_scheme_returns_none(self):
        entity = {"identifier": {"scheme": "AU-ACN", "id": "123456789"}}
        assert _extract_abn(entity) is None

    def test_empty_entity_returns_none(self):
        assert _extract_abn({}) is None

    def test_empty_id_returns_none(self):
        entity = {"additionalIdentifiers": [{"scheme": "AU-ABN", "id": ""}]}
        assert _extract_abn(entity) is None


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------


class TestParseDatetime:
    def test_iso_with_z(self):
        dt = _parse_datetime("2024-06-01T12:00:00Z")
        assert dt == datetime(2024, 6, 1, 12, 0, 0)

    def test_iso_without_z(self):
        dt = _parse_datetime("2024-06-01T12:00:00")
        assert dt == datetime(2024, 6, 1, 12, 0, 0)

    def test_date_only(self):
        dt = _parse_datetime("2024-06-01")
        assert dt == datetime(2024, 6, 1, 0, 0, 0)

    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_empty_returns_none(self):
        assert _parse_datetime("") is None


# ---------------------------------------------------------------------------
# _parse_decimal
# ---------------------------------------------------------------------------


class TestParseDecimal:
    def test_integer(self):
        assert _parse_decimal(500000) == Decimal("500000")

    def test_float(self):
        assert _parse_decimal(123.45) == Decimal("123.45")

    def test_string(self):
        assert _parse_decimal("750000.00") == Decimal("750000.00")

    def test_none_returns_none(self):
        assert _parse_decimal(None) is None


# ---------------------------------------------------------------------------
# _raw_to_release
# ---------------------------------------------------------------------------


class TestRawToRelease:
    def test_minimal_release_parses(self):
        release = _raw_to_release(_minimal_release())
        assert release is not None
        assert release.ocid == "ocds-n38h2q-ABC123"
        assert release.id == "ocds-n38h2q-ABC123-award-20240601"

    def test_buyer_populated(self):
        release = _raw_to_release(_minimal_release())
        assert release.buyer is not None
        assert release.buyer.name == "Dept of Transport"

    def test_award_count(self):
        release = _raw_to_release(_minimal_release())
        assert len(release.awards) == 1
        assert release.awards[0].value.amount == Decimal("500000")

    def test_contract_count(self):
        release = _raw_to_release(_minimal_release())
        assert len(release.contracts) == 1
        assert release.contracts[0].award_id == "award-001"

    def test_supplier_abn_enriched_from_parties(self):
        """Supplier ABN must be extracted from parties[], not just awards[].suppliers."""
        release = _raw_to_release(_minimal_release())
        supplier = release.awards[0].suppliers[0]
        assert supplier.identifier is not None
        assert supplier.identifier.scheme == "AU-ABN"
        assert supplier.identifier.id == "12345678901"

    def test_supplier_name_enriched_from_parties(self):
        release = _raw_to_release(_minimal_release())
        assert release.awards[0].suppliers[0].name == "Acme Civil Pty Ltd"

    def test_jurisdiction_source_tag(self):
        release = _raw_to_release(_minimal_release())
        assert release.source.get("_jurisdiction") == "AUSTENDER"

    def test_missing_ocid_returns_none(self):
        raw = _minimal_release(ocid="")
        assert _raw_to_release(raw) is None

    def test_missing_id_returns_none(self):
        raw = _minimal_release(id="")
        assert _raw_to_release(raw) is None

    def test_no_parties_supplier_still_parses(self):
        """If parties[] is empty, supplier info comes from awards[].suppliers directly."""
        raw = _minimal_release()
        raw["parties"] = []
        release = _raw_to_release(raw)
        assert release is not None
        assert release.awards[0].suppliers[0].name == "Acme Civil Pty Ltd"
        # ABN will be None since no party to cross-reference
        assert release.awards[0].suppliers[0].identifier is None

    def test_amendment_tag_preserved(self):
        raw = _minimal_release(tag=["awardUpdate"])
        release = _raw_to_release(raw)
        assert release.tag == ["awardUpdate"]

    def test_period_dates_on_contract(self):
        release = _raw_to_release(_minimal_release())
        c = release.contracts[0]
        assert c.period is not None
        assert c.period.start_date == datetime(2024, 6, 1, 0, 0, 0)
        assert c.period.end_date == datetime(2025, 6, 1, 0, 0, 0)

    def test_no_awards_returns_empty_lists(self):
        raw = _minimal_release(awards=[], contracts=[])
        release = _raw_to_release(raw)
        assert release.awards == []
        assert release.contracts == []

    def test_multiple_suppliers_per_award(self):
        raw = _minimal_release()
        raw["parties"].append({
            "id": "sup-002",
            "name": "Beta Constructions",
            "roles": ["supplier"],
            "additionalIdentifiers": [{"scheme": "AU-ABN", "id": "99887766554"}],
        })
        raw["awards"][0]["suppliers"].append({"id": "sup-002", "name": "Beta Constructions"})
        release = _raw_to_release(raw)
        assert len(release.awards[0].suppliers) == 2
        abns = {s.identifier.id for s in release.awards[0].suppliers if s.identifier}
        assert "12345678901" in abns
        assert "99887766554" in abns

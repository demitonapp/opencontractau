"""City of Stirling per-tender PDF register parsing.

Fixtures are the extract_text() output shape for three real templates
seen live (2026-08-13): awarded, not-yet-awarded ("To be advised"), and
the third template whose combined value label reads back with the value
interleaved between the label's two halves.
"""

from decimal import Decimal

from opencontractau.scrapers.wa.councils.stirling import parse_tender_pdf

_AWARDED = [
    "Tender Register\nTender No: 78986\nTender Title: Disposal Construction and Inert Waste\n"
    "Some description text.\nTenderers\n1 Someone Pty Ltd\n78986 Tender Register Page 1",
    "Tender Register\nAdvertisement Notice Tender awarded by:\nCouncil\n"
    "Date of Council Meeting / CEO Award:\n7 July 2026\nCouncil Resolution:\n0726/021\n"
    "Successful Tenderer: Farfield Holdings Pty Ltd ATF The R. Gullotto Family Trust\n"
    "T/As Capital Recycling\nEstimated Annual Contract Value: $800,000\n78986 Tender Register Page 2",
]

_NOT_YET_AWARDED = [
    "Tender Register\nTender No: 79337\nTender Title: Intersection Upgrade\n"
    "Tenderers\n1 TBA\n79337 Tender Register Page 1",
    "Tender Register\nAdvertisement Notice Tender awarded by:\nTo be advised\n"
    "Date of Council Meeting / CEO Award:\nTo be advised\nCouncil Resolution:\nTo be advised\n"
    "Successful Tenderer: To be advised\nValue of Successful Tenderer: To be advised\n"
    "79337 Tender Register Page 2",
]

# The combined-label template: the value sits between the label's two
# halves, not after both of them.
_COMBINED_LABEL_UNAWARDED = [
    "Tender Register\nTender No: 79414\nTender Title: Gutter Cleaning Services\n"
    "Tenderers\n1 Someone Pty Ltd\n79414 Tender Register Page 1",
    "Tender Register\nAdvertisement Notice Tender awarded by:\nTo be advised\n"
    "Date of Council Meeting / CEO Award:\nTo be advised\nCouncil Resolution:\nXxxxx/xxxx\n"
    "N/A (CEO awarded)\nSuccessful Tenderer(s): To be advised\n"
    "Value of Successful Tenderer(s) or\nTo be advised\nEstimated Annual Contract Value:\n"
    "79414 Tender Register Page 2",
]


class TestParseTenderPdf:
    def test_awarded_tender_extracts_all_fields(self):
        row = parse_tender_pdf(_AWARDED)
        assert row is not None
        assert row.reference == "78986"
        assert row.title == "Disposal Construction and Inert Waste"
        assert row.awarded_to == "Farfield Holdings Pty Ltd ATF The R. Gullotto Family Trust T/As Capital Recycling"
        assert row.value_aud == Decimal("800000")
        assert (row.award_date.year, row.award_date.month, row.award_date.day) == (2026, 7, 7)

    def test_not_yet_awarded_tender_is_skipped(self):
        assert parse_tender_pdf(_NOT_YET_AWARDED) is None

    def test_combined_value_label_does_not_leak_into_the_supplier_name(self):
        # Regression: this used to return awarded_to == "To be advised
        # Value of Successful Tenderer(s) or To be advised" because the
        # boundary regex didn't recognise the label's first half.
        assert parse_tender_pdf(_COMBINED_LABEL_UNAWARDED) is None

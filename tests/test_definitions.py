"""Tests for preextract definition term extraction."""

from preextract.definitions import _extract_term


def test_extract_term_accepts_straight_single_quotes():
    term, pattern = _extract_term(
        "'The Initial Rent' means the sum of TWENTY SEVEN THOUSAND AND "
        "FIVE HUNDRED POUNDS (£27,500.00)."
    )
    assert term == "The Initial Rent"
    assert pattern == "quoted"


def test_extract_term_annexed_rent_with_double_quotes():
    term, pattern = _extract_term('1.16. "Rent" means £28,260 per annum')
    assert term == "Rent"
    assert pattern == "quoted"


def test_extract_term_annual_rent_title_case_colon():
    term, pattern = _extract_term(
        "Annual Rent: rent at a rate of £33,000.00 per annum and any interim rent "
        "determined under the LTA - 1954."
    )
    assert term == "Annual Rent"
    assert pattern == "title_case_colon"

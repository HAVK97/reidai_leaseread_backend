"""Tests for extract field normalizers."""

from __future__ import annotations

from extract.normalizers import (
    build_empty_pack_field_results,
    build_omv_not_applicable_field_results,
    normalize_insurance_fields,
    normalize_rent_review_header_fields,
    normalize_third_party_fields,
    normalize_alterations_fields,
)
from extract.resolver import FieldResult


def _field(value: str, status: str, **kwargs) -> FieldResult:
    return FieldResult(
        field_id=kwargs.pop("field_id", "x"),
        value=value,
        status=status,
        quotes=kwargs.pop("quotes", []),
        reasoning=kwargs.pop("reasoning", ""),
        confidence=kwargs.pop("confidence", "low"),
        **kwargs,
    )


def test_normalize_insurance_responsibility_when_coverage_no():
    fields = {
        "insurance_plate_glass": _field("No", "not_present", field_id="insurance_plate_glass"),
        "insurance_plate_glass_responsibility": _field(
            "",
            "unclear",
            field_id="insurance_plate_glass_responsibility",
            reasoning="No clear obligation",
        ),
    }
    normalize_insurance_fields(fields)
    resp = fields["insurance_plate_glass_responsibility"]
    assert resp.status == "not_present"
    assert resp.value == ""
    assert "not applicable" in resp.reasoning.lower()
    assert resp.needs_review is False


def test_normalize_insurance_responsibility_unchanged_when_coverage_yes():
    fields = {
        "insurance_engineering": _field("Yes", "found", field_id="insurance_engineering"),
        "insurance_engineering_responsibility": _field(
            "",
            "unclear",
            field_id="insurance_engineering_responsibility",
        ),
    }
    normalize_insurance_fields(fields)
    assert fields["insurance_engineering_responsibility"].status == "unclear"


def test_normalize_insurance_responsibility_when_coverage_found_no():
    fields = {
        "insurance_building": _field("No", "found", field_id="insurance_building"),
        "insurance_building_responsibility": _field(
            "",
            "unclear",
            field_id="insurance_building_responsibility",
        ),
    }
    normalize_insurance_fields(fields)
    assert fields["insurance_building_responsibility"].status == "not_present"


def test_normalize_rent_review_header_no_mechanism():
    fields = {
        "rent_review_basis_of_valuation": _field(
            "No rent review",
            "not_present",
            field_id="rent_review_basis_of_valuation",
            confidence="medium",
        ),
        "rent_review_first_date": _field(
            "",
            "unclear",
            field_id="rent_review_first_date",
            confidence="low",
        ),
        "rent_review_frequency": _field(
            "",
            "unclear",
            field_id="rent_review_frequency",
            confidence="low",
        ),
        "rent_review_time_of_essence": _field(
            "No",
            "found",
            field_id="rent_review_time_of_essence",
            confidence="medium",
        ),
    }
    normalize_rent_review_header_fields(fields)
    assert fields["rent_review_first_date"].status == "not_present"
    assert fields["rent_review_first_date"].confidence == "high"
    assert fields["rent_review_basis_of_valuation"].confidence == "high"
    assert fields["rent_review_time_of_essence"].confidence == "high"


def test_build_empty_pack_field_results_yes_no_na():
    from extract.groups import load_group_config

    group = load_group_config("omv_disregards")
    fields = build_empty_pack_field_results(group)
    assert len(fields) == len(group.fields)
    for fs in group.fields:
        fr = fields[fs.field_id]
        assert fr.value == "N/A"
        assert fr.status == "not_present"
        assert fr.confidence == "high"
        assert fr.quotes == []


def test_build_empty_pack_field_results_text_and_yes_no():
    from extract.groups import load_group_config

    group = load_group_config("break_rights")
    fields = build_empty_pack_field_results(group)
    for fs in group.fields:
        fr = fields[fs.field_id]
        assert fr.value == ""
        assert fr.status == "not_present"
        assert fr.confidence == "high"


def test_build_omv_not_applicable_field_results_sets_all_fields_to_na():
    from extract.groups import load_group_config

    group = load_group_config("omv_assumptions")
    fields = build_omv_not_applicable_field_results(group)

    assert len(fields) == len(group.fields)
    for fs in group.fields:
        fr = fields[fs.field_id]
        assert fr.value == "N/A"
        assert fr.status == "found"
        assert fr.confidence == "high"
        assert fr.quotes == []
        assert "not Open Market Value" in fr.reasoning


def test_normalize_third_party_no_operative_evidence():
    fields = {
        "third_party_determination": _field(
            "No mention",
            "unclear",
            field_id="third_party_determination",
            confidence="medium",
        ),
        "third_party_application_by": _field(
            "",
            "unclear",
            field_id="third_party_application_by",
            confidence="low",
        ),
    }
    normalize_third_party_fields(fields, block_counts={"definitions_old": 1})
    assert fields["third_party_determination"].status == "not_present"
    assert fields["third_party_determination"].confidence == "high"
    assert fields["third_party_application_by"].status == "not_present"


def test_normalize_third_party_no_mention_with_operative_evidence():
    fields = {
        "third_party_determination": _field(
            "No mention",
            "unclear",
            field_id="third_party_determination",
            confidence="medium",
        ),
    }
    normalize_third_party_fields(fields, block_counts={"operative_current": 12})
    assert fields["third_party_determination"].status == "found"
    assert fields["third_party_determination"].value == "No mention"
    assert fields["third_party_determination"].confidence == "high"


def test_normalize_alterations_silent_electrics():
    fields = {
        "alterations_electrics": _field(
            "",
            "unclear",
            field_id="alterations_electrics",
            confidence="low",
        ),
        "alterations_internal": _field(
            "Internal alterations require landlord consent.",
            "found",
            field_id="alterations_internal",
            confidence="high",
        ),
    }
    normalize_alterations_fields(fields, block_counts={"operative_current": 8})
    assert fields["alterations_electrics"].status == "not_present"
    assert fields["alterations_electrics"].confidence == "high"
    assert fields["alterations_internal"].status == "found"

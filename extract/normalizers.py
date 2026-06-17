"""Deterministic post-processing for extracted field results."""

from __future__ import annotations

from .groups import GroupConfig
from .resolver import FieldResult

_EMPTY_PACK_REASONING = "No relevant evidence assembled for this extraction group."
_OMV_NOT_APPLICABLE_REASONING = (
    "Rent review basis is not Open Market Value; OMV assumptions and disregards "
    "are not applicable."
)


def _empty_pack_value_for_format(field_format: str) -> str:
    if field_format == "yes_no_na":
        return "N/A"
    return ""


def build_empty_pack_field_results(group: GroupConfig) -> dict[str, FieldResult]:
    """Build not_present field results when the evidence pack is empty."""
    fields: dict[str, FieldResult] = {}
    for fs in group.fields:
        fields[fs.field_id] = FieldResult(
            field_id=fs.field_id,
            value=_empty_pack_value_for_format(fs.format),
            status="not_present",
            quotes=[],
            reasoning=_EMPTY_PACK_REASONING,
            confidence="high",
        )
    return fields


def build_omv_not_applicable_field_results(group: GroupConfig) -> dict[str, FieldResult]:
    """Build deterministic N/A results for OMV-only groups when basis is not OMV."""
    fields: dict[str, FieldResult] = {}
    for fs in group.fields:
        fields[fs.field_id] = FieldResult(
            field_id=fs.field_id,
            value="N/A",
            status="found",
            quotes=[],
            reasoning=_OMV_NOT_APPLICABLE_REASONING,
            confidence="high",
        )
    return fields

# Parent yes/no coverage field -> responsibility enum field.
_INSURANCE_COVERAGE_RESPONSIBILITY_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("insurance_building", "insurance_building_responsibility", "building insurance"),
    ("insurance_plate_glass", "insurance_plate_glass_responsibility", "plate glass insurance"),
    ("insurance_engineering", "insurance_engineering_responsibility", "engineering insurance"),
)


def _coverage_not_required(field: FieldResult) -> bool:
    """True when the lease has no obligation for this insurance category."""
    if field.status == "not_present":
        return True
    return field.status == "found" and field.value == "No"


def normalize_insurance_fields(fields: dict[str, FieldResult]) -> None:
    """Apply insurance group field-pair rules in place.

    When a coverage field is No/not_present, the matching responsibility field
    is not applicable and should be not_present (not unclear).
    """
    for coverage_id, resp_id, label in _INSURANCE_COVERAGE_RESPONSIBILITY_PAIRS:
        coverage = fields.get(coverage_id)
        resp = fields.get(resp_id)
        if not coverage or not resp:
            continue
        if not _coverage_not_required(coverage):
            continue

        resp.value = ""
        resp.status = "not_present"
        resp.reasoning = (
            f"No {label} obligation in the lease, so responsibility is not applicable."
        )
        if resp.confidence == "low":
            resp.confidence = "medium"
        resp.needs_review = False
        resp.review_reason = None


_RENT_REVIEW_ABSENT_FIELD_IDS = (
    "rent_review_first_date",
    "rent_review_frequency",
    "rent_review_upwards_only",
    "rent_review_notice_provisions",
)


def normalize_rent_review_header_fields(fields: dict[str, FieldResult]) -> None:
    """When basis is No rent review, align absent fields to not_present + high."""
    basis = fields.get("rent_review_basis_of_valuation")
    if not basis or basis.value != "No rent review":
        return
    if basis.status not in {"found", "not_present"}:
        return

    basis.status = "not_present"
    if basis.confidence in {"low", "medium"}:
        basis.confidence = "high"

    for field_id in _RENT_REVIEW_ABSENT_FIELD_IDS:
        field = fields.get(field_id)
        if not field:
            continue
        if field.value and field.status == "found":
            continue
        if field.status == "unclear" or (field.status == "not_present" and not field.value):
            field.status = "not_present"
            field.confidence = "high"
            field.needs_review = False
            field.review_reason = None

    time_of_essence = fields.get("rent_review_time_of_essence")
    if (
        time_of_essence
        and time_of_essence.value == "No"
        and time_of_essence.confidence in {"low", "medium"}
    ):
        time_of_essence.confidence = "high"


_THIRD_PARTY_NO_RENT_REVIEW_REASONING = (
    "No rent review operative evidence in the pack; third-party determination is not applicable."
)


def _operative_row_count(block_counts: dict[str, int]) -> int:
    return block_counts.get("operative_current", 0) + block_counts.get("operative_other", 0)


def normalize_third_party_fields(
    fields: dict[str, FieldResult],
    *,
    block_counts: dict[str, int],
) -> None:
    """Align third_party fields when the pack lacks rent review operative text."""
    if _operative_row_count(block_counts) > 0:
        det = fields.get("third_party_determination")
        if (
            det
            and det.value == "No mention"
            and det.status == "unclear"
        ):
            det.status = "found"
            det.confidence = "high"
            det.needs_review = False
            det.review_reason = None
        return

    for field in fields.values():
        field.value = ""
        field.status = "not_present"
        field.confidence = "high"
        field.reasoning = _THIRD_PARTY_NO_RENT_REVIEW_REASONING
        field.needs_review = False
        field.review_reason = None


_ALTERATIONS_SILENT_SUBCATEGORY_FIELD_IDS = (
    "alterations_external",
    "alterations_demountable_partitions",
    "alterations_electrics",
)

_ALTERATIONS_SILENT_SUBCATEGORY_REASONING = (
    "The lease contains alterations provisions but no specific provision for this sub-category."
)


def normalize_alterations_fields(
    fields: dict[str, FieldResult],
    *,
    block_counts: dict[str, int],
) -> None:
    """When alterations pack has operative text but a sub-category is omitted, use not_present."""
    if _operative_row_count(block_counts) == 0:
        return

    for field_id in _ALTERATIONS_SILENT_SUBCATEGORY_FIELD_IDS:
        field = fields.get(field_id)
        if not field:
            continue
        if field.value:
            continue
        if field.status != "unclear":
            continue
        field.status = "not_present"
        field.confidence = "high"
        field.reasoning = _ALTERATIONS_SILENT_SUBCATEGORY_REASONING
        field.needs_review = False
        field.review_reason = None

"""JSON schema generation and output validation for extraction groups.

Generates the JSON schema to send to the model so it returns structured output,
and validates/serialises FieldResult dicts to the final group envelope format.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .groups import FieldSchema, GroupConfig
from .assembler import segment_kind_label
from .resolver import FieldResult, SupportingEvidence


# ---------------------------------------------------------------------------
# Per-field JSON schema
# ---------------------------------------------------------------------------


_FIELD_RESULT_BASE = {
    "type": "object",
    "required": ["value", "status", "quotes", "reasoning", "confidence"],
    "additionalProperties": False,
    "properties": {
        "value": {"type": "string"},
        "status": {"type": "string", "enum": ["found", "not_present", "unclear"]},
        "quotes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short verbatim phrases (8-25 words) copied exactly from the evidence pack.",
        },
        "reasoning": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}


def _value_schema_for_field(fs: FieldSchema) -> dict:
    """Return the schema constraint for the 'value' key of a field."""
    match fs.format:
        case "yes_no":
            return {"type": "string", "enum": ["Yes", "No", ""]}
        case "yes_no_na":
            return {"type": "string", "enum": ["Yes", "No", "N/A", ""]}
        case "yes_no_with_detail":
            return {"type": "string"}
        case "date":
            return {
                "type": "string",
                "description": "DD/MM/YYYY or empty string if not determinable.",
            }
        case "frequency":
            return {
                "type": "string",
                "description": "'annually', 'X years' (e.g. '5 years'), or empty string.",
            }
        case "enum":
            return {"type": "string", "enum": fs.allowed_values + [""]}
        case _:
            return {"type": "string"}


def build_response_schema(group: GroupConfig) -> dict:
    """Build the JSON Schema to send to the model for this group."""
    properties: dict[str, Any] = {}
    for fs in group.fields:
        field_schema = dict(_FIELD_RESULT_BASE)
        # Override the value property with format-specific constraints
        field_schema = json.loads(json.dumps(_FIELD_RESULT_BASE))  # deep copy
        field_schema["properties"]["value"] = _value_schema_for_field(fs)
        field_schema["description"] = f"{fs.label} — {fs.decision_rule[:120]}"
        properties[fs.field_id] = field_schema

    required_ids = [fs.field_id for fs in group.fields if fs.required]

    return {
        "type": "object",
        "required": required_ids,
        "additionalProperties": False,
        "properties": properties,
    }


# ---------------------------------------------------------------------------
# Field descriptions for prompt injection
# ---------------------------------------------------------------------------


def build_field_descriptions(group: GroupConfig) -> str:
    """Build a human-readable field list for the user prompt template."""
    lines: list[str] = []
    for fs in group.fields:
        lines.append(f"### {fs.label} (`{fs.field_id}`)")
        lines.append(f"Format: `{fs.format}`")
        if fs.allowed_values:
            lines.append(f"Allowed values: {', '.join(fs.allowed_values)}")
        if fs.decision_rule:
            lines.append(f"Rule: {fs.decision_rule}")
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Output serialisation
# ---------------------------------------------------------------------------


def supporting_evidence_to_dict(se: SupportingEvidence) -> dict:
    return {
        "evidence_id": se.evidence_id,
        "text_verbatim": se.text_verbatim,
        "page_number": se.page_number,
        "section_title": se.section_title,
    }


def field_result_to_dict(fr: FieldResult) -> dict:
    return {
        "value": fr.value,
        "status": fr.status,
        "quotes": fr.quotes,
        "reasoning": fr.reasoning,
        "confidence": fr.confidence,
        "supporting_evidence": [supporting_evidence_to_dict(se) for se in fr.supporting_evidence],
        "needs_review": fr.needs_review,
        "review_reason": fr.review_reason,
        "applied_segment_kind": fr.applied_segment_kind,
        "applied_segment_label": segment_kind_label(fr.applied_segment_kind)
            if fr.applied_segment_kind and fr.applied_segment_kind != "mixed"
            else ("Mixed segments" if fr.applied_segment_kind == "mixed" else None),
    }


def build_group_envelope(
    *,
    doc_id: str,
    group_id: str,
    group_schema_version: str,
    prompt_version: str,
    extracted_at: str,
    provider: str,
    model: str,
    model_profile: str,
    usage: dict,
    input_evidence_ids: list[str],
    block_counts: dict,
    warnings: list[str],
    fields: dict[str, FieldResult],
    empty_pack_short_circuit: bool = False,
) -> dict:
    """Build the final output envelope for a group run."""
    fields_dict = {fid: field_result_to_dict(fr) for fid, fr in fields.items()}

    needs_review_count = sum(1 for fr in fields.values() if fr.needs_review)
    unclear_count = sum(1 for fr in fields.values() if fr.status == "unclear")
    found_count = sum(1 for fr in fields.values() if fr.status == "found")
    not_present_count = sum(1 for fr in fields.values() if fr.status == "not_present")

    envelope: dict = {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "group_id": group_id,
        "group_schema_version": group_schema_version,
        "prompt_version": prompt_version,
        "extracted_at": extracted_at,
        "provider": provider,
        "model": model,
        "model_profile": model_profile,
        "usage": usage,
        "input_evidence_ids": input_evidence_ids,
        "evidence_block_counts": block_counts,
        "assembly_warnings": warnings,
        "validation_summary": {
            "total_fields": len(fields),
            "found": found_count,
            "not_present": not_present_count,
            "unclear": unclear_count,
            "needs_review": needs_review_count,
        },
        "fields": fields_dict,
    }
    if empty_pack_short_circuit:
        envelope["empty_pack_short_circuit"] = True
    return envelope

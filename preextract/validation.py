"""Validation helpers for all pre-extraction artefacts.

Each validate_* function accepts one artefact dict and returns a stage-level
result dict:
  {
    "stage":    str,
    "passed":   bool,
    "errors":   list[str],   # hard failures (must be fixed)
    "warnings": list[str],   # soft issues (emitted but suspect)
    "counts":   dict,        # counts for the report summary
  }

validate_all() runs every stage and returns the full validation_report dict.
"""

from __future__ import annotations

import re
from collections import Counter

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PARSE_PATH_RE = re.compile(
    r"^(items\.pages\[\d+\]\.items(\[\d+\]\.items)*\[\d+\]\.bbox\[\d+\]"
    r"|images_content_metadata\.images\[\d+\]\.bbox)$"
)

_EID_RE = re.compile(r"^p\d+_(item_\d+_\d+|layout_image_\d+)$")


def _stage(stage_name: str, errors: list[str], warnings: list[str], counts: dict) -> dict:
    return {
        "stage": stage_name,
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


def validate_manifest(manifest: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    pages: list[dict] = manifest.get("pages") or []
    evidence: list[dict] = manifest.get("evidence") or []

    # Schema-level presence
    for field in ("schema_version", "doc_id", "pages", "evidence"):
        if field not in manifest:
            errors.append(f"Missing required field: {field}")

    page_numbers: set[int] = {p["page_number"] for p in pages}
    max_page = max(page_numbers, default=0)

    # Evidence-id uniqueness
    all_eids: list[str] = [r.get("evidence_id", "") for r in evidence]
    eid_counts = Counter(all_eids)
    dupes = [eid for eid, cnt in eid_counts.items() if cnt > 1]
    if dupes:
        errors.append(f"Duplicate evidence_ids ({len(dupes)}): {dupes[:5]}")

    # evidence_id format
    bad_format = [eid for eid in all_eids if eid and not _EID_RE.match(eid)]
    if bad_format:
        warnings.append(f"Non-standard evidence_id format ({len(bad_format)}): {bad_format[:3]}")

    # Per-row checks
    empty_text_count = 0
    negative_bbox_count = 0
    out_of_range_page = 0
    bad_normalized = 0
    bad_parse_path = 0

    for row in evidence:
        pn = row.get("page_number", 0)
        if pn < 1 or pn > max_page:
            out_of_range_page += 1

        rb = row.get("raw_bbox") or {}
        w = float(rb.get("w") or 0)
        h = float(rb.get("h") or 0)
        if row.get("source") == "items" and (w <= 0 or h <= 0):
            negative_bbox_count += 1

        norm = row.get("normalized")
        if norm:
            for k in ("x", "y", "w", "h"):
                v = norm.get(k, 0)
                if not (-0.05 <= v <= 1.1):
                    bad_normalized += 1
                    break

        if row.get("source") == "items" and not row.get("text"):
            empty_text_count += 1

        pp = row.get("parse_path") or ""
        if pp and not _PARSE_PATH_RE.match(pp):
            bad_parse_path += 1

    if negative_bbox_count > 0:
        errors.append(f"Items with zero/negative bbox dimensions: {negative_bbox_count}")
    if out_of_range_page > 0:
        warnings.append(f"Evidence rows with page_number outside page list: {out_of_range_page}")
    if bad_normalized > 5:
        warnings.append(f"Evidence rows with normalized coords out of [0,1.1]: {bad_normalized}")
    if empty_text_count > len(evidence) * 0.5:
        warnings.append(
            f"More than half of items-source rows have empty text ({empty_text_count}/{len(evidence)})"
        )
    if bad_parse_path > 0:
        warnings.append(f"Rows with unexpected parse_path format: {bad_parse_path}")

    items_count = sum(1 for r in evidence if r.get("source") == "items")
    image_count = sum(1 for r in evidence if r.get("source") == "images_content_metadata")

    return _stage(
        "evidence_manifest",
        errors,
        warnings,
        {
            "pages": len(pages),
            "evidence_total": len(evidence),
            "evidence_items": items_count,
            "evidence_images": image_count,
        },
    )


# ---------------------------------------------------------------------------
# Segment map validation
# ---------------------------------------------------------------------------


def validate_segment_map(segment_map: dict, manifest: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    segments: list[dict] = segment_map.get("segments") or []
    precedence: list[str] = segment_map.get("precedence") or []
    manifest_eids: set[str] = {r["evidence_id"] for r in (manifest.get("evidence") or [])}
    manifest_pages: list[int] = sorted(p["page_number"] for p in (manifest.get("pages") or []))
    max_page = manifest_pages[-1] if manifest_pages else 0
    min_page = manifest_pages[0] if manifest_pages else 1

    seg_ids: list[str] = [s.get("segment_id", "") for s in segments]

    # Unique segment IDs
    if len(seg_ids) != len(set(seg_ids)):
        errors.append("Duplicate segment_ids detected")

    # Precedence list coverage
    missing_from_prec = set(seg_ids) - set(precedence)
    if missing_from_prec:
        errors.append(f"Segments missing from precedence list: {missing_from_prec}")
    extra_in_prec = set(precedence) - set(seg_ids)
    if extra_in_prec:
        errors.append(f"Precedence references unknown segment_ids: {extra_in_prec}")

    # Page coverage: union of segment ranges must cover [min_page, max_page]
    covered: set[int] = set()
    for seg in segments:
        pr = seg.get("page_range") or []
        if len(pr) == 2:
            covered.update(range(int(pr[0]), int(pr[1]) + 1))
    uncovered = set(manifest_pages) - covered
    if uncovered:
        warnings.append(
            f"Pages not covered by any segment: {sorted(uncovered)[:10]}"
        )

    # Evidence id references
    for seg in segments:
        for field in ("start_evidence_id", "end_evidence_id"):
            eid = seg.get(field)
            if eid and eid not in manifest_eids:
                errors.append(f"Segment {seg['segment_id']} {field} not in manifest: {eid}")

    # Supersession references valid
    for seg in segments:
        for ref in seg.get("supersedes_segment_ids") or []:
            if ref not in seg_ids:
                warnings.append(
                    f"Segment {seg['segment_id']} supersedes unknown segment: {ref}"
                )

    # Warn if no segments at all
    if not segments:
        warnings.append("segment_map has no segments")

    return _stage(
        "segment_map",
        errors,
        warnings,
        {"segments": len(segments), "pages_covered": len(covered)},
    )


# ---------------------------------------------------------------------------
# Structure map validation
# ---------------------------------------------------------------------------


def validate_structure_map(structure_map: dict, manifest: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_eids: set[str] = {r["evidence_id"] for r in (manifest.get("evidence") or [])}

    segments: list[dict] = structure_map.get("segments") or []
    if not segments:
        warnings.append("structure_map has no segments")

    total_anchors = 0
    total_assignments = 0
    dangling_assignment_eids = 0
    toc_missing_segments: list[str] = []

    for seg in segments:
        anchors = seg.get("anchors_flat") or []
        assignments = seg.get("box_assignments") or []
        toc = seg.get("toc_lines") or []
        total_anchors += len(anchors)
        total_assignments += len(assignments)

        if not toc:
            toc_missing_segments.append(seg.get("segment_id", "?"))

        for a in assignments:
            eid = a.get("evidence_id")
            if eid and eid not in manifest_eids:
                dangling_assignment_eids += 1

    if dangling_assignment_eids > 0:
        errors.append(
            f"box_assignments reference evidence_ids not in manifest: {dangling_assignment_eids}"
        )
    if toc_missing_segments:
        warnings.append(
            f"Segments with empty toc_lines: {toc_missing_segments}"
        )
    if total_anchors == 0:
        warnings.append("No anchors detected across all segments")

    return _stage(
        "structure_map",
        errors,
        warnings,
        {"segments": len(segments), "anchors": total_anchors, "assignments": total_assignments},
    )


# ---------------------------------------------------------------------------
# Definitions index validation
# ---------------------------------------------------------------------------


def validate_definitions_index(definitions_index: dict, manifest: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_eids: set[str] = {r["evidence_id"] for r in (manifest.get("evidence") or [])}

    definitions: list[dict] = definitions_index.get("definitions") or []
    dangling = 0
    empty_terms = 0

    for defn in definitions:
        if not defn.get("term"):
            empty_terms += 1
        term_eid = defn.get("term_evidence_id")
        if term_eid and term_eid not in manifest_eids:
            dangling += 1
        for eid in defn.get("definition_evidence_ids") or []:
            if eid not in manifest_eids:
                dangling += 1

    if dangling > 0:
        errors.append(f"Definitions reference evidence_ids not in manifest: {dangling}")
    if empty_terms > 0:
        warnings.append(f"Definitions with empty term strings: {empty_terms}")
    if not definitions:
        warnings.append("definitions_index has no entries")

    return _stage(
        "definitions_index",
        errors,
        warnings,
        {"definitions": len(definitions)},
    )


# ---------------------------------------------------------------------------
# Section map validation
# ---------------------------------------------------------------------------


def validate_section_map(section_map: dict, manifest: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_eids: set[str] = {r["evidence_id"] for r in (manifest.get("evidence") or [])}

    segments: list[dict] = section_map.get("segments") or []
    dangling = 0
    empty_topics: list[str] = []
    total_topics = 0

    for seg_entry in segments:
        seg_id = seg_entry.get("segment_id", "?")
        topics: dict = seg_entry.get("topics") or {}
        total_topics += len(topics)

        for topic_key, topic in topics.items():
            eids = topic.get("evidence_ids") or []
            if not eids:
                empty_topics.append(f"{seg_id}:{topic_key}")
            for eid in eids:
                if eid not in manifest_eids:
                    dangling += 1

    if dangling > 0:
        errors.append(f"Section map references evidence_ids not in manifest: {dangling}")
    if empty_topics:
        warnings.append(
            f"Topics with no evidence ({len(empty_topics)}): {empty_topics[:8]}"
        )

    return _stage(
        "semantic_section_map",
        errors,
        warnings,
        {"segments": len(segments), "topics": total_topics, "empty_topics": len(empty_topics)},
    )


# ---------------------------------------------------------------------------
# Aggregate validate_all
# ---------------------------------------------------------------------------


def validate_all(
    manifest: dict,
    segment_map: dict | None = None,
    structure_map: dict | None = None,
    definitions_index: dict | None = None,
    section_map: dict | None = None,
) -> dict:
    """
    Run all available validators and return the full validation_report dict.

    Hard errors in any stage set the top-level "passed" to False.
    """
    results: list[dict] = []

    results.append(validate_manifest(manifest))

    if segment_map is not None:
        results.append(validate_segment_map(segment_map, manifest))

    if structure_map is not None:
        results.append(validate_structure_map(structure_map, manifest))

    if definitions_index is not None:
        results.append(validate_definitions_index(definitions_index, manifest))

    if section_map is not None:
        results.append(validate_section_map(section_map, manifest))

    all_passed = all(r["passed"] for r in results)

    counts: dict = {}
    for r in results:
        counts.update(r.get("counts") or {})

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": manifest.get("doc_id", "unknown"),
        "passed": all_passed,
        "stage_results": results,
        "counts": counts,
        "spot_checks": [],
    }

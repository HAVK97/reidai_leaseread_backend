"""Tests for schedule start detection in structure_detector."""

from __future__ import annotations

import json
from pathlib import Path

from preextract.structure_detector import (
    _build_evidence_index,
    _detect_schedule_sections,
    _page_lines,
    _schedule_start_match,
)

Q_GLAZING_RAW = (
    Path(__file__).parent.parent
    / "parse_outputs"
    / "Henry_Lease Q Glazing to Automerc"
    / "llamaparse_raw_result.json"
)


def test_schedule_start_match_strips_markdown_heading_prefix():
    assert _schedule_start_match("# SCHEDULE 2: THE RENT AND RENT REVIEW")
    assert _schedule_start_match("SCHEDULE 2: THE RENT AND RENT REVIEW")


def test_page_lines_reads_nested_header_value_on_schedule_2_page():
    raw = json.loads(Q_GLAZING_RAW.read_text(encoding="utf-8"))
    pages = (raw.get("items") or {}).get("pages") or []
    page_42 = next(p for p in pages if int(p.get("page_number") or 0) == 42)
    lines = _page_lines(page_42)
    assert any(
        "schedule 2" in line.lower() and "rent review" in line.lower()
        for line in lines
    )
    assert any(_schedule_start_match(line) for line in lines)


def test_q_glazing_detects_schedule_2_operative_page():
    raw = json.loads(Q_GLAZING_RAW.read_text(encoding="utf-8"))
    manifest_path = (
        Path(__file__).parent.parent
        / "preextract_outputs"
        / "Henry_Lease_Q_Glazing_to_Automerc"
        / "evidence_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_index = _build_evidence_index(manifest.get("evidence") or [])
    pages = (raw.get("items") or {}).get("pages") or []
    pages_by_number = {
        int(p.get("page_number", i + 1)): p for i, p in enumerate(pages)
    }
    schedules = _detect_schedule_sections(
        pages_by_number,
        start_page=41,
        end_page=43,
        evidence_index=evidence_index,
        exclude_ranges=None,
    )
    tokens = {sch["token"] for sch in schedules}
    assert "Schedule 2" in tokens
    schedule_2 = next(s for s in schedules if s["token"] == "Schedule 2")
    assert int(schedule_2["page_range"][0]) == 42

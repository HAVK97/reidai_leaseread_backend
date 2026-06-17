"""Tests for structure_v2 box assignment helpers."""

from __future__ import annotations

from preextract.structure_v2 import _resolve_eid_position, _span_positions


def test_span_positions_resolves_multi_bbox_end_evidence_id():
    id_pos = {
        "p13_item_2_0": 10,
        "p13_item_4_0": 12,
        "p13_item_20_0": 20,
    }
    start, end = _span_positions(id_pos, "p13_item_2_0", "p13_item_20_1")
    assert start == 10
    assert end == 20
    assert _resolve_eid_position(id_pos, "p13_item_20_1") == 20

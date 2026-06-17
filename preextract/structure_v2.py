"""Lease-specific v2 structure builder.

This module adapts the deterministic detector in
preextract.structure_detector into the preextract artefact contract.
It intentionally preserves the existing downstream shape: segment_map.json and
structure_map.json with anchors_flat, box_assignments, and toc_lines.
"""

from __future__ import annotations

import re
from typing import Any

from preextract.structure_detector import analyze_lease_pages

SCHEMA_VERSION = "2.0"


def _raw_pages(raw: dict) -> list[dict]:
    return (raw.get("items") or {}).get("pages") or []


def _evidence_lookup(manifest: dict) -> dict[str, dict]:
    return {r["evidence_id"]: r for r in manifest.get("evidence") or []}


def _ordered_text_rows(manifest: dict) -> list[dict]:
    rows = [
        r
        for r in manifest.get("evidence") or []
        if r.get("source") == "items" and r.get("bbox_index", 0) == 0
    ]
    return sorted(
        rows,
        key=lambda r: (
            int(r.get("page_number") or 0),
            float((r.get("raw_bbox") or {}).get("y") or 0),
            float((r.get("raw_bbox") or {}).get("x") or 0),
        ),
    )


def _ordered_id_index(manifest: dict) -> dict[str, int]:
    return {r["evidence_id"]: idx for idx, r in enumerate(_ordered_text_rows(manifest))}


def _resolve_eid_position(id_pos: dict[str, int], evidence_id: str | None) -> int | None:
    """Map evidence_id to document-order index.

    ``_ordered_id_index`` only indexes bbox_index=0 rows. Clause spans from the
    detector may reference sibling bbox rows (e.g. page-footers); fall back to
    the primary bbox on the same parsed item.
    """
    if not evidence_id:
        return None
    if evidence_id in id_pos:
        return id_pos[evidence_id]
    m = re.match(r"^(p\d+_item_\d+)_\d+$", evidence_id)
    if m:
        primary = f"{m.group(1)}_0"
        if primary in id_pos:
            return id_pos[primary]
    return None


def _span_positions(
    id_pos: dict[str, int],
    start_eid: str | None,
    end_eid: str | None,
) -> tuple[int | None, int | None]:
    start = _resolve_eid_position(id_pos, start_eid)
    end = _resolve_eid_position(id_pos, end_eid)
    if start is None or end is None or end < start:
        return None, None
    return start, end


def _bbox_for_eid(eid_lookup: dict[str, dict], evidence_id: str | None) -> list[float]:
    row = eid_lookup.get(evidence_id or "") or {}
    rb = row.get("raw_bbox") or {}
    x = float(rb.get("x") or 0)
    y = float(rb.get("y") or 0)
    w = float(rb.get("w") or rb.get("width") or 0)
    h = float(rb.get("h") or rb.get("height") or 0)
    return [x, y, x + w, y + h]


def _text_for_eid(eid_lookup: dict[str, dict], evidence_id: str | None, fallback: str) -> str:
    row = eid_lookup.get(evidence_id or "") or {}
    return (row.get("text") or fallback or "").strip()


def _page_for_eid(eid_lookup: dict[str, dict], evidence_id: str | None, fallback: int) -> int:
    row = eid_lookup.get(evidence_id or "") or {}
    try:
        return int(row.get("page_number") or fallback)
    except Exception:
        return fallback


def _id_span_for_pages(
    rows: list[dict],
    page_range: tuple[int, int] | list[int],
) -> tuple[str | None, str | None]:
    s, e = int(page_range[0]), int(page_range[1])
    in_range = [r for r in rows if s <= int(r.get("page_number") or 0) <= e]
    if not in_range:
        return None, None
    return in_range[0]["evidence_id"], in_range[-1]["evidence_id"]


def _anchor(
    *,
    anchor_id: int,
    family: str,
    token: str,
    title: str,
    level: int,
    start_evidence_id: str | None,
    end_evidence_id: str | None,
    eid_lookup: dict[str, dict],
    confidence: float,
    parent_id: int | None = None,
    fallback_page: int = 1,
) -> dict[str, Any]:
    page_number = _page_for_eid(eid_lookup, start_evidence_id, fallback_page)
    raw_text = _text_for_eid(eid_lookup, start_evidence_id, f"{token} {title}".strip())
    return {
        "anchor_id": anchor_id,
        "page_idx": page_number - 1,
        "page_number": page_number,
        "box": _bbox_for_eid(eid_lookup, start_evidence_id),
        "raw_text": raw_text,
        "norm_text": re.sub(r"\s+", " ", raw_text.lower()).strip(),
        "label": "Section-header",
        "family": family,
        "level": level,
        "token": token,
        "title": title,
        "confidence": round(confidence, 3),
        "parent_id": parent_id,
        "evidence_id": start_evidence_id,
        "start_evidence_id": start_evidence_id,
        "end_evidence_id": end_evidence_id,
    }


def _add_range_anchor(
    anchors: list[dict],
    *,
    family: str,
    token: str,
    title: str,
    page_range: tuple[int, int] | list[int],
    rows: list[dict],
    eid_lookup: dict[str, dict],
    confidence: float,
    level: int = 1,
    parent_id: int | None = None,
) -> dict | None:
    start_eid, end_eid = _id_span_for_pages(rows, page_range)
    if not start_eid:
        return None
    anchor = _anchor(
        anchor_id=len(anchors),
        family=family,
        token=token,
        title=title,
        level=level,
        start_evidence_id=start_eid,
        end_evidence_id=end_eid,
        eid_lookup=eid_lookup,
        confidence=confidence,
        parent_id=parent_id,
        fallback_page=int(page_range[0]),
    )
    anchors.append(anchor)
    return anchor


def _add_explicit_anchor(
    anchors: list[dict],
    *,
    family: str,
    token: str,
    title: str,
    level: int,
    start_evidence_id: str | None,
    end_evidence_id: str | None,
    eid_lookup: dict[str, dict],
    confidence: float,
    parent_id: int | None = None,
    fallback_page: int = 1,
) -> dict | None:
    if not start_evidence_id:
        return None
    anchor = _anchor(
        anchor_id=len(anchors),
        family=family,
        token=token,
        title=title,
        level=level,
        start_evidence_id=start_evidence_id,
        end_evidence_id=end_evidence_id,
        eid_lookup=eid_lookup,
        confidence=confidence,
        parent_id=parent_id,
        fallback_page=fallback_page,
    )
    anchors.append(anchor)
    return anchor


def _detect_lr_anchors(
    instrument: dict,
    rows: list[dict],
    id_pos: dict[str, int],
    eid_lookup: dict[str, dict],
) -> list[dict]:
    anchors: list[dict] = []
    lr_pages = {
        p
        for pr in instrument.get("lr_ranges") or []
        for p in range(int(pr[0]), int(pr[1]) + 1)
    }
    if not lr_pages:
        return anchors

    starts: list[tuple[str, str, str]] = []
    for row in rows:
        if int(row.get("page_number") or 0) not in lr_pages:
            continue
        text = row.get("text") or ""
        m = re.search(r"\b(LR\d+(?:\.\d+)?)\b[\s:.\-]*(.{0,120})", text, re.I)
        if not m:
            continue
        token = m.group(1).upper()
        title = re.sub(r"\s+", " ", m.group(2)).strip(" .:-") or token
        if any(existing[0] == token for existing in starts):
            continue
        starts.append((token, title, row["evidence_id"]))

    for idx, (token, title, start_eid) in enumerate(starts):
        start_pos = id_pos.get(start_eid)
        if idx + 1 < len(starts):
            next_pos = id_pos.get(starts[idx + 1][2])
            end_pos = None if next_pos is None else max(start_pos or 0, next_pos - 1)
            end_eid = None
            if end_pos is not None:
                ordered = _ordered_text_rows({"evidence": rows})
                if 0 <= end_pos < len(ordered):
                    end_eid = ordered[end_pos]["evidence_id"]
        else:
            _, end_eid = _id_span_for_pages(rows, instrument["page_range"])
        anchor = _anchor(
            anchor_id=len(anchors),
            family="lr_clauses",
            token=token,
            title=title,
            level=1,
            start_evidence_id=start_eid,
            end_evidence_id=end_eid,
            eid_lookup=eid_lookup,
            confidence=0.93,
        )
        anchors.append(anchor)
    return anchors


def _numbered_parent_token(token: str) -> str | None:
    parts = [p for p in token.split(".") if p]
    if len(parts) <= 1 or not all(p.isdigit() for p in parts):
        return None
    return ".".join(parts[:-1])


def _anchor_path(anchor: dict, by_id: dict[int, dict]) -> list[str]:
    path: list[str] = []
    cursor: dict | None = anchor
    seen: set[int] = set()
    while cursor is not None:
        aid = int(cursor["anchor_id"])
        if aid in seen:
            break
        seen.add(aid)
        path.append(str(cursor.get("token") or cursor.get("title") or aid))
        parent_id = cursor.get("parent_id")
        cursor = by_id.get(parent_id) if parent_id is not None else None
    return list(reversed(path))


def _apply_hierarchy(
    anchors: list[dict],
    *,
    segment_id: str,
    segment_kind: str,
) -> list[dict]:
    """
    Materialize parent/child links on flat anchors.

    The detector already computes span-aware anchors. This pass makes the legal
    hierarchy explicit so topic tags can later inherit down clause/schedule
    subtrees without guessing from raw text again.
    """
    by_token_family: dict[tuple[str, str], dict] = {}
    by_id: dict[int, dict] = {int(a["anchor_id"]): a for a in anchors}

    for anchor in anchors:
        anchor["segment_id"] = segment_id
        anchor["segment_kind"] = segment_kind
        anchor["node_id"] = f"{segment_id}:a{anchor['anchor_id']}"
        anchor["children_ids"] = []
        by_token_family[(anchor["family"], str(anchor.get("token") or ""))] = anchor

    prescribed = by_token_family.get(("front_matter", "PRESCRIBED_CLAUSES"))

    for anchor in anchors:
        family = anchor.get("family")
        token = str(anchor.get("token") or "")
        parent_id = anchor.get("parent_id")

        if family == "lr_clauses" and prescribed is not None:
            parent_id = prescribed["anchor_id"]
        elif family == "numbered_clauses":
            parent_token = _numbered_parent_token(token)
            if parent_token:
                parent = by_token_family.get(("numbered_clauses", parent_token))
                if parent is not None:
                    parent_id = parent["anchor_id"]
        elif family == "schedules" and "::" in token and parent_id is None:
            schedule_token = token.split("::", 1)[0]
            parent = by_token_family.get(("schedules", schedule_token))
            if parent is not None:
                parent_id = parent["anchor_id"]

        anchor["parent_id"] = parent_id

    for anchor in anchors:
        parent_id = anchor.get("parent_id")
        if parent_id is None:
            continue
        parent = by_id.get(int(parent_id))
        if parent is not None and anchor["anchor_id"] not in parent["children_ids"]:
            parent["children_ids"].append(anchor["anchor_id"])

    for anchor in anchors:
        anchor["path"] = _anchor_path(anchor, by_id)

    return anchors


def _tree_node(anchor: dict, children_by_parent: dict[int | None, list[dict]]) -> dict:
    return {
        "node_id": anchor["node_id"],
        "anchor_id": anchor["anchor_id"],
        "segment_id": anchor.get("segment_id"),
        "segment_kind": anchor.get("segment_kind"),
        "family": anchor.get("family"),
        "level": anchor.get("level"),
        "token": anchor.get("token"),
        "title": anchor.get("title"),
        "path": anchor.get("path") or [],
        "page_number": anchor.get("page_number"),
        "start_evidence_id": anchor.get("start_evidence_id"),
        "end_evidence_id": anchor.get("end_evidence_id"),
        "children_ids": anchor.get("children_ids") or [],
        "children": [
            _tree_node(child, children_by_parent)
            for child in children_by_parent.get(anchor["anchor_id"], [])
        ],
    }


def _structure_tree(
    anchors: list[dict],
    *,
    segment_id: str,
    segment_kind: str,
    page_range: list[int] | tuple[int, int],
) -> list[dict]:
    children_by_parent: dict[int | None, list[dict]] = {}
    for anchor in anchors:
        children_by_parent.setdefault(anchor.get("parent_id"), []).append(anchor)

    for children in children_by_parent.values():
        children.sort(key=lambda a: (a.get("page_idx", 0), a.get("box", [0, 0])[1], a["anchor_id"]))

    roots = children_by_parent.get(None, [])
    return [
        {
            "node_id": f"{segment_id}:root",
            "segment_id": segment_id,
            "segment_kind": segment_kind,
            "family": "segment",
            "level": 0,
            "token": segment_id,
            "title": segment_kind.replace("_", " ").title(),
            "path": [segment_id],
            "page_range": [int(page_range[0]), int(page_range[1])],
            "children_ids": [a["anchor_id"] for a in roots],
            "children": [_tree_node(anchor, children_by_parent) for anchor in roots],
        }
    ]


def _segment_id_for_label(label: str, idx: int) -> str:
    if label == "lease":
        return "seg_1"
    if label == "current_instrument":
        return "seg_current"
    if label == "annexed_old_lease":
        return "seg_annexed_old"
    return f"seg_{idx + 1}"


def build_segment_map_v2(raw: dict, manifest: dict) -> dict:
    """Build segment_map.json from the v2 lease detector."""
    analysis = analyze_lease_pages(_raw_pages(raw), manifest.get("evidence") or [])
    segments: list[dict] = []
    for idx, instrument in enumerate(analysis.get("instruments") or []):
        label = instrument["label"]
        segment_id = _segment_id_for_label(label, idx)
        page_range = [int(instrument["page_range"][0]), int(instrument["page_range"][1])]
        rank = 0 if label == "current_instrument" else 3 if label in {"lease", "annexed_old_lease"} else 4
        kind = "base_lease" if label == "lease" else label
        segments.append(
            {
                "segment_id": segment_id,
                "kind": kind,
                "title": label.replace("_", " ").title(),
                "page_range": page_range,
                "start_evidence_id": instrument.get("start_evidence_id"),
                "end_evidence_id": instrument.get("end_evidence_id"),
                "evidence_ids": [
                    r["evidence_id"]
                    for r in manifest.get("evidence") or []
                    if page_range[0] <= int(r.get("page_number") or 0) <= page_range[1]
                ],
                "priority": "highest" if rank == 0 else "base",
                "precedence_rank": rank,
                "supersedes_segment_ids": [],
                "detection": {
                    "method": "structure_v2_old_new_detector",
                    "signals": [label],
                    "confidence": 0.9,
                },
            }
        )

    high = [s for s in segments if s["precedence_rank"] <= 1]
    base = [s for s in segments if s["precedence_rank"] >= 3]
    for seg in high:
        seg["supersedes_segment_ids"] = [b["segment_id"] for b in base]

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": manifest.get("doc_id", "unknown"),
        "segments": segments,
        "precedence": [
            s["segment_id"]
            for s in sorted(segments, key=lambda s: (s["precedence_rank"], s["page_range"][0]))
        ],
        "warnings": [],
    }


def _anchors_for_instrument(
    instrument: dict,
    manifest: dict,
) -> list[dict]:
    rows = _ordered_text_rows(manifest)
    id_pos = _ordered_id_index(manifest)
    eid_lookup = _evidence_lookup(manifest)
    anchors: list[dict] = []

    cover_page = instrument.get("cover_page")
    if cover_page:
        _add_range_anchor(
            anchors,
            family="front_matter",
            token="COVER",
            title="Cover",
            page_range=(int(cover_page), int(cover_page)),
            rows=rows,
            eid_lookup=eid_lookup,
            confidence=0.9,
        )
    for pr in instrument.get("toc_ranges") or []:
        _add_range_anchor(
            anchors,
            family="front_matter",
            token="CONTENTS",
            title="Table of Contents",
            page_range=pr,
            rows=rows,
            eid_lookup=eid_lookup,
            confidence=0.92,
        )
    for pr in instrument.get("summary_ranges") or []:
        _add_range_anchor(
            anchors,
            family="front_matter",
            token="LEASE_PARTICULARS",
            title="Lease Summary / Particulars",
            page_range=pr,
            rows=rows,
            eid_lookup=eid_lookup,
            confidence=0.88,
        )
    for pr in instrument.get("lr_ranges") or []:
        _add_range_anchor(
            anchors,
            family="front_matter",
            token="PRESCRIBED_CLAUSES",
            title="Land Registry Prescribed Clauses",
            page_range=pr,
            rows=rows,
            eid_lookup=eid_lookup,
            confidence=0.88,
        )
    for lr_anchor in _detect_lr_anchors(instrument, rows, id_pos, eid_lookup):
        lr_anchor["anchor_id"] = len(anchors)
        anchors.append(lr_anchor)
    for pr in instrument.get("definitions_ranges") or []:
        _add_range_anchor(
            anchors,
            family="numbered_clauses",
            token="DEFINITIONS",
            title="Definitions and Interpretation",
            page_range=pr,
            rows=rows,
            eid_lookup=eid_lookup,
            confidence=0.86,
        )
    for clause in instrument.get("clause_ranges") or []:
        _add_explicit_anchor(
            anchors,
            family="numbered_clauses",
            token=str(clause.get("token") or ""),
            title=str(clause.get("title") or ""),
            level=int(clause.get("level") or 1),
            start_evidence_id=clause.get("start_evidence_id"),
            end_evidence_id=clause.get("end_evidence_id"),
            eid_lookup=eid_lookup,
            confidence=0.84,
            fallback_page=int(clause.get("start_page") or instrument["page_range"][0]),
        )
    for schedule in instrument.get("schedules") or []:
        sch = _add_explicit_anchor(
            anchors,
            family="schedules",
            token=str(schedule.get("token") or "SCHEDULE"),
            title=str(schedule.get("title") or "Schedule"),
            level=1,
            start_evidence_id=schedule.get("start_evidence_id"),
            end_evidence_id=schedule.get("end_evidence_id"),
            eid_lookup=eid_lookup,
            confidence=0.88,
            fallback_page=int((schedule.get("page_range") or instrument["page_range"])[0]),
        )
        for parent in schedule.get("parent_clauses") or []:
            _add_explicit_anchor(
                anchors,
                family="schedules",
                token=f"{schedule.get('token')}::{parent.get('token')}",
                title=str(parent.get("title") or ""),
                level=2,
                start_evidence_id=parent.get("start_evidence_id"),
                end_evidence_id=parent.get("end_evidence_id"),
                eid_lookup=eid_lookup,
                confidence=0.8,
                parent_id=None if sch is None else sch["anchor_id"],
                fallback_page=int(parent.get("start_page") or instrument["page_range"][0]),
            )
    for pr in instrument.get("plan_ranges") or []:
        _add_range_anchor(
            anchors,
            family="front_matter",
            token="FLOORPLAN",
            title="Plans",
            page_range=pr,
            rows=rows,
            eid_lookup=eid_lookup,
            confidence=0.82,
        )

    return sorted(anchors, key=lambda a: (a["page_idx"], a["box"][1], a["box"][0], a["anchor_id"]))


def _section_assignment_for_row(
    row: dict,
    anchors: list[dict],
    id_pos: dict[str, int],
) -> dict | None:
    pos = id_pos.get(row["evidence_id"])
    if pos is None:
        return None

    candidates: list[tuple[int, int, dict]] = []
    for anchor in anchors:
        start, end = _span_positions(
            id_pos,
            anchor.get("start_evidence_id"),
            anchor.get("end_evidence_id"),
        )
        if start is None or end is None or not (start <= pos <= end):
            continue
        width = end - start
        candidates.append((int(anchor.get("level") or 1), -width, anchor))

    if not candidates:
        prior = [
            a
            for a in anchors
            if id_pos.get(a.get("start_evidence_id") or "", 10**12) <= pos
        ]
        if not prior:
            return None
        return prior[-1]

    schedule_candidates = [
        item for item in candidates if (item[2].get("family") or "") == "schedules"
    ]
    if schedule_candidates:
        # Schedule pages can overlap stale numbered-clause spans. Prefer the
        # narrowest schedule anchor so schedule body text is not owned by the
        # last operative clause before the schedules section.
        schedule_candidates.sort(key=lambda item: (item[1], item[0]), reverse=True)
        return schedule_candidates[0][2]

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _box_assignments_for_instrument(
    instrument: dict,
    manifest: dict,
    anchors: list[dict],
) -> list[dict]:
    id_pos = _ordered_id_index(manifest)
    rows = _ordered_text_rows(manifest)
    s, e = int(instrument["page_range"][0]), int(instrument["page_range"][1])
    assignments: list[dict] = []

    for el_idx, row in enumerate(rows):
        page = int(row.get("page_number") or 0)
        if page < s or page > e:
            continue
        anchor = _section_assignment_for_row(row, anchors, id_pos)
        rb = row.get("raw_bbox") or {}
        x = float(rb.get("x") or 0)
        y = float(rb.get("y") or 0)
        w = float(rb.get("w") or rb.get("width") or 0)
        h = float(rb.get("h") or rb.get("height") or 0)
        token = None if anchor is None else anchor["token"]
        parent_token = None
        sub_parent_token = None
        parts = [p for p in (token or "").split(".") if p]
        if len(parts) >= 1 and parts[0].isdigit():
            parent_token = parts[0]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            sub_parent_token = f"{parts[0]}.{parts[1]}"

        assignments.append(
            {
                "page_idx": page - 1,
                "page_number": page,
                "element_idx": el_idx,
                "evidence_id": row["evidence_id"],
                "label": row.get("label"),
                "box": [x, y, x + w, y + h],
                "anchor_id": None if anchor is None else anchor["anchor_id"],
                "node_id": None if anchor is None else anchor.get("node_id"),
                "path": [] if anchor is None else anchor.get("path", []),
                "segment_id": None if anchor is None else anchor.get("segment_id"),
                "segment_kind": None if anchor is None else anchor.get("segment_kind"),
                "section_family": None if anchor is None else anchor["family"],
                "section_token": token,
                "section_title": None if anchor is None else anchor["title"],
                "section_level": None if anchor is None else anchor["level"],
                "parent_token": parent_token,
                "sub_parent_token": sub_parent_token,
            }
        )
    return assignments


def _toc_lines(anchors: list[dict], id_pos: dict[str, int]) -> list[str]:
    lines: list[str] = []
    for anchor in anchors:
        if anchor["family"] == "front_matter" and anchor["token"] not in {
            "COVER",
            "CONTENTS",
            "LEASE_PARTICULARS",
            "PRESCRIBED_CLAUSES",
            "FLOORPLAN",
        }:
            continue
        start = anchor.get("page_number") or 1
        end = start
        _, end_pos = _span_positions(
            id_pos,
            anchor.get("start_evidence_id"),
            anchor.get("end_evidence_id"),
        )
        if end_pos is not None:
            # Page end is only cosmetic for the human TOC view.
            end = start
        lines.append(f"{anchor['token']} {anchor['title']} {start}-{end}")
    return lines


def build_structure_map_v2(raw: dict, manifest: dict, segment_map: dict | None = None) -> dict:
    """Build structure_map.json using the lease-specific v2 detector."""
    analysis = analyze_lease_pages(_raw_pages(raw), manifest.get("evidence") or [])
    id_pos = _ordered_id_index(manifest)
    segments_out: list[dict] = []

    segment_id_by_label = {}
    if segment_map:
        for seg in segment_map.get("segments") or []:
            label = seg.get("kind")
            if label:
                segment_id_by_label[label] = seg["segment_id"]

    for idx, instrument in enumerate(analysis.get("instruments") or []):
        label = instrument["label"]
        segment_id = segment_id_by_label.get(label) or _segment_id_for_label(label, idx)
        segment_kind = "base_lease" if label == "lease" else label
        anchors = _apply_hierarchy(
            _anchors_for_instrument(instrument, manifest),
            segment_id=segment_id,
            segment_kind=segment_kind,
        )
        assignments = _box_assignments_for_instrument(instrument, manifest, anchors)
        segments_out.append(
            {
                "segment_id": segment_id,
                "segment_kind": segment_kind,
                "page_range": [
                    int(instrument["page_range"][0]),
                    int(instrument["page_range"][1]),
                ],
                "anchors_flat": anchors,
                "structure_tree": _structure_tree(
                    anchors,
                    segment_id=segment_id,
                    segment_kind=segment_kind,
                    page_range=instrument["page_range"],
                ),
                "toc_lines": _toc_lines(anchors, id_pos),
                "box_assignments": assignments,
                "pages_count": int(instrument["page_range"][1]) - int(instrument["page_range"][0]) + 1,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": manifest.get("doc_id", "unknown"),
        "engine": "structure_v2",
        "segments": segments_out,
    }

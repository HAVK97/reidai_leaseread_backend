#!/usr/bin/env python3
"""
Detect whether each lease pack contains:
  - only one lease instrument, or
  - current instrument + annexed old/existing lease.

Output is terminal-only evidence-id spans (derived from page detections).
No files are written.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _load_pages(parse_json_path: Path) -> list[dict]:
    payload = json.loads(parse_json_path.read_text(encoding="utf-8"))
    try:
        return payload["items"]["pages"] or []
    except Exception as exc:  # pragma: no cover
        raise ValueError(f"Unsupported parse JSON format: {parse_json_path}") from exc


def _load_evidence_rows(evidence_manifest_path: Path) -> list[dict]:
    payload = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
    rows = payload.get("evidence") or []
    if not isinstance(rows, list):
        raise ValueError(f"Unsupported evidence manifest format: {evidence_manifest_path}")
    return rows


def _build_evidence_index(rows: list[dict]) -> dict[str, Any]:
    """
    Build ordered evidence-id lookups per page.

    - text_ids_by_page: text-like boxes (preferred for legal span anchors)
    - any_ids_by_page: all boxes, including image/layout
    """
    text_ids_by_page: dict[int, list[str]] = {}
    any_ids_by_page: dict[int, list[str]] = {}
    text_rows_by_page: dict[int, list[dict[str, Any]]] = {}
    ordered_ids: list[str] = []
    id_to_pos: dict[str, int] = {}

    for row in rows:
        eid = str(row.get("evidence_id") or "").strip()
        if not eid:
            continue
        try:
            page = int(row.get("page_number"))
        except Exception:
            continue

        source = str(row.get("source") or "").lower()
        item_type = str(row.get("item_type") or "").lower()
        label = str(row.get("label") or "").lower()
        text = str(row.get("text") or "").strip()
        is_image_like = (
            source == "images_content_metadata"
            or item_type in {"image", "picture", "figure"}
            or "image" in label
        )

        pos = len(ordered_ids)
        ordered_ids.append(eid)
        id_to_pos[eid] = pos
        any_ids_by_page.setdefault(page, []).append(eid)
        if (not is_image_like) and text:
            text_ids_by_page.setdefault(page, []).append(eid)
            text_rows_by_page.setdefault(page, []).append(
                {
                    "evidence_id": eid,
                    "pos": pos,
                    "text": text,
                    "norm": _norm(text),
                }
            )

    return {
        "text_ids_by_page": text_ids_by_page,
        "any_ids_by_page": any_ids_by_page,
        "text_rows_by_page": text_rows_by_page,
        "ordered_ids": ordered_ids,
        "id_to_pos": id_to_pos,
    }


def _id_span_for_page_range(
    page_range: tuple[int, int],
    evidence_index: dict[str, Any],
) -> tuple[str | None, str | None]:
    s, e = page_range
    text_ids_by_page = evidence_index["text_ids_by_page"]
    any_ids_by_page = evidence_index["any_ids_by_page"]

    start_id: str | None = None
    end_id: str | None = None

    for p in range(s, e + 1):
        ids = text_ids_by_page.get(p) or any_ids_by_page.get(p) or []
        if ids:
            start_id = ids[0]
            break

    for p in range(e, s - 1, -1):
        ids = text_ids_by_page.get(p) or any_ids_by_page.get(p) or []
        if ids:
            end_id = ids[-1]
            break

    return start_id, end_id


def _format_id_span(page_range: tuple[int, int], evidence_index: dict[str, Any]) -> str:
    start_id, end_id = _id_span_for_page_range(page_range, evidence_index)
    if not start_id and not end_id:
        return "none"
    if not start_id:
        return f"none -> {end_id}"
    if not end_id:
        return f"{start_id} -> none"
    return f"{start_id} -> {end_id}"


def _format_id_spans(
    ranges: list[tuple[int, int]],
    evidence_index: dict[str, Any],
) -> str:
    if not ranges:
        return "none"
    return ", ".join(_format_id_span(r, evidence_index) for r in ranges)


def _format_evidence_span(start_id: str | None, end_id: str | None) -> str:
    if not start_id and not end_id:
        return "none"
    if not start_id:
        return f"none -> {end_id}"
    if not end_id:
        return f"{start_id} -> none"
    return f"{start_id} -> {end_id}"


def _evidence_pos(evidence_index: dict[str, Any], evidence_id: str | None) -> int | None:
    if not evidence_id:
        return None
    pos = evidence_index["id_to_pos"].get(evidence_id)
    return int(pos) if pos is not None else None


def _evidence_id_at_pos(evidence_index: dict[str, Any], pos: int) -> str | None:
    ordered_ids = evidence_index["ordered_ids"]
    if pos < 0 or pos >= len(ordered_ids):
        return None
    return str(ordered_ids[pos])


def _span_from_positions(
    evidence_index: dict[str, Any],
    start_pos: int | None,
    end_pos: int | None,
) -> tuple[str | None, str | None]:
    if start_pos is None or end_pos is None or end_pos < start_pos:
        return None, None
    return _evidence_id_at_pos(evidence_index, start_pos), _evidence_id_at_pos(evidence_index, end_pos)


def _resolve_anchor_evidence_id(
    evidence_index: dict[str, Any],
    page_number: int,
    token: str,
    title: str,
    *,
    min_pos: int | None = None,
) -> str | None:
    rows = evidence_index["text_rows_by_page"].get(page_number) or []
    if not rows:
        ids = evidence_index["text_ids_by_page"].get(page_number) or evidence_index["any_ids_by_page"].get(page_number) or []
        return ids[0] if ids else None

    title_norm = _norm(title)
    token_norm = _norm(token)
    combined_norm = _norm(f"{token} {title}")

    def _after(row: dict[str, Any]) -> bool:
        return min_pos is None or int(row["pos"]) > min_pos

    for needle in [combined_norm, title_norm]:
        if not needle:
            continue
        probe = needle[:100]
        for row in rows:
            if not _after(row):
                continue
            row_norm = str(row["norm"])
            if probe and (probe in row_norm or row_norm[:100] in needle):
                return str(row["evidence_id"])

    if token_norm:
        for row in rows:
            if not _after(row):
                continue
            if re.search(rf"(^|\s){re.escape(token_norm)}[.)]?\s", str(row["norm"])):
                return str(row["evidence_id"])

    for row in rows:
        if _after(row):
            return str(row["evidence_id"])
    return str(rows[0]["evidence_id"])


def _page_text(p: dict) -> str:
    rows = p.get("items") or []
    return _norm(" ".join((it.get("value") or it.get("md") or "") for it in rows))


def _item_text_lines(item: dict) -> list[str]:
    """Collect matchable text lines from a page item, including nested children."""
    lines: list[str] = []
    value = _norm(item.get("value") or "")
    md = _norm(item.get("md") or "")
    if value:
        lines.append(value)
    elif md:
        lines.append(md)
    for child in item.get("items") or []:
        if not isinstance(child, dict):
            continue
        child_value = _norm(child.get("value") or "")
        child_md = _norm(child.get("md") or "")
        if child_value:
            lines.append(child_value)
        elif child_md:
            lines.append(child_md)
    return lines


def _page_lines(p: dict) -> list[str]:
    rows = p.get("items") or []
    lines: list[str] = []
    for it in rows:
        lines.extend(_item_text_lines(it))
    header = _norm(p.get("header") or "")
    if header:
        lines.insert(0, header)
    return [line for line in lines if line]


def _image_item_count(p: dict) -> int:
    return sum(
        1
        for it in (p.get("items") or [])
        if (it.get("type") or "").lower() in {"image", "picture", "figure"}
        or re.search(r"!\[.*?\]\(", it.get("md") or "")
    )


def _looks_true_front_page(p: dict) -> bool:
    """
    True only for title/cover pages like the screenshots:
      - DATED / date near title page text
      - LEASE / COUNTERPART LEASE as the instrument title
      - party/property/title-page style text

    False for:
      - LR pages
      - printed contents
      - body pages saying "THIS LEASE is made..."
    """
    rows = p.get("items") or []
    text = _page_text(p)
    lines = _page_lines(p)
    item_count = len(rows)

    if not text:
        return False

    # Navigation / registration / body pages are not title pages.
    if re.search(r"\b(contents|prescribed clauses|lr1|lr2|land registry|required wording)\b", text):
        return False
    if re.search(r"\b(this lease is made|this sublease is made|definitions|tenant'?s covenants)\b", text):
        return False

    # Title pages in this sample set are sparse. Keep this generous because
    # OCR can split logos/signatures into separate items, but reject dense body.
    if item_count > 18:
        return False

    # Crucial: use item/line-level signals, not whole-page blobs.
    # Body pages contain "date", "lease", "of", "and" all the time.
    has_date_line = any(
        re.match(r"^(<u>)?\s*dated\b", line) or re.match(r"^date\b", line)
        for line in lines[:10]
    )
    has_instrument_title_line = any(
        (
            re.match(r"^(counterpart lease|underlease|sublease)\b", line)
            or re.match(r"^lease(\s+of\b|\s+relating\b|$)", line)
        )
        and not re.match(r"^lease (summary|particulars)\b", line)
        for line in lines[:12]
    )

    has_parties_or_property_line = any(
        re.match(r"^(between|to|and|relating to|-of-|of)\b", line)
        or re.search(r"\b(limited|llp|plc|ltd)\b", line)
        for line in lines[:12]
    )

    return has_date_line and has_instrument_title_line and has_parties_or_property_line


def _looks_toc_start_page(p: dict) -> bool:
    """True for the first page of a printed table of contents."""
    lines = _page_lines(p)
    text = _page_text(p)
    if not text:
        return False

    # Start page normally has a clear CONTENTS heading near the top.
    has_contents_heading = any(
        re.match(r"^(<u>)?\s*(table of )?contents\b", line)
        or re.match(r"^(<u>)?\s*lease contents\b", line)
        for line in lines[:8]
    )
    if not has_contents_heading:
        return False

    # A line-level CONTENTS heading is strong enough for the start page. Some
    # scans collapse the actual entries into one markdown/table item that is
    # hard to count reliably.
    return True


def _toc_entry_count(p: dict) -> int:
    """
    Count clause/schedule-looking TOC entries.

    Works whether LlamaParse split entries into separate items or collapsed
    the printed contents page into one long string/table.
    """
    lines = _page_lines(p)
    text = _page_text(p)

    line_hits = sum(
        1
        for line in lines
        if re.match(
            r"^\s*(\|)?\s*(\d+(?:\.\d+)*\.?|schedule\s+\d+|part\s+\d+)\b",
            line,
            re.I,
        )
    )
    blob_hits = len(
        re.findall(
            r"(?:^|\s|\|)\s*(\d+(?:\.\d+)*\.?|schedule\s+\d+|part\s+\d+)\s*(?:\||\s+)[a-z]",
            text,
            re.I,
        )
    )
    return max(line_hits, blob_hits)


def _looks_toc_continuation_page(p: dict) -> bool:
    """True for pages after a CONTENTS start page that continue the TOC list."""
    text = _page_text(p)
    if not text:
        return False

    # Hard reject obvious legal body starts.
    if re.search(r"\b(this lease is made|this sublease is made|provided always that)\b", text):
        return False
    if re.search(r"\b(land registry|required wording|lr\d+)\b", text):
        return False
    if re.search(r"\b(lease summary|lease particulars|particulars)\b", text):
        return False

    # Continuation pages may not say "contents"; they just contain dense clause
    # or schedule entries.
    return _toc_entry_count(p) >= 4


def _detect_toc_ranges(
    pages_by_number: dict[int, dict],
    start_page: int,
    end_page: int,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    p = start_page
    while p <= end_page:
        page = pages_by_number.get(p)
        if page is None or not _looks_toc_start_page(page):
            p += 1
            continue

        toc_start = p
        toc_end = p
        n = p + 1
        while n <= end_page:
            next_page = pages_by_number.get(n)
            if next_page is None or not _looks_toc_continuation_page(next_page):
                break
            toc_end = n
            n += 1

        ranges.append((toc_start, toc_end))
        p = toc_end + 1
    return ranges


def _looks_lease_summary_start_page(p: dict) -> bool:
    """True for lease summary / lease particulars / particulars pages."""
    lines = _page_lines(p)
    text = _page_text(p)
    if not text:
        return False

    # Exclude LR/prescribed pages: they often mention lease/parties too.
    if _looks_lr_start_page(p):
        return False

    return any(
        re.match(r"^(<u>)?\s*(lease summary|lease particulars|particulars)\b", line)
        for line in lines[:8]
    )


def _looks_lease_summary_continuation_page(p: dict) -> bool:
    text = _page_text(p)
    if not text:
        return False
    if _looks_lr_start_page(p) or _looks_toc_start_page(p):
        return False
    if re.search(r"\b(this lease is made|this sublease is made|definitions and interpretation)\b", text):
        return False

    # Summary/particulars pages are field-list heavy.
    summary_fields = [
        "landlord",
        "tenant",
        "guarantor",
        "premises",
        "property",
        "term",
        "contractual term",
        "annual rent",
        "basic rent",
        "permitted use",
        "break date",
        "rent commencement",
        "review date",
        "service charge",
    ]
    return sum(1 for f in summary_fields if re.search(rf"\b{re.escape(f)}\b", text)) >= 4


def _looks_lr_start_page(p: dict) -> bool:
    """True for LR / prescribed clauses start page."""
    lines = _page_lines(p)
    text = _page_text(p)
    if not text:
        return False

    has_lr_heading = any(
        re.match(r"^(hm land registry required wording|prescribed clauses)\b", line)
        for line in lines[:8]
    )
    has_lr1 = bool(re.search(r"\blr1\b.*\bdate of lease\b", text))
    has_lr2 = bool(re.search(r"\blr2\b.*\btitle number", text))
    return has_lr_heading or (has_lr1 and has_lr2)


def _looks_lr_continuation_page(p: dict) -> bool:
    text = _page_text(p)
    if not text:
        return False
    if _looks_toc_start_page(p) or _looks_lease_summary_start_page(p):
        return False
    if re.search(r"\bdefinitions and interpretation\b", text):
        return False

    # LR continuation has multiple LR tokens or high-number LR terms.
    lr_tokens = re.findall(r"\blr\d+(?:\.\d+)?\b", text)
    if len(set(lr_tokens)) >= 2:
        return True
    return bool(re.search(r"\blr(7|8|9|10|11|12|13|14)\b", text))


def _detect_section_ranges(
    pages_by_number: dict[int, dict],
    start_page: int,
    end_page: int,
    is_start,
    is_continuation,
    exclude_ranges: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    excluded_pages: set[int] = set()
    for ex_start, ex_end in exclude_ranges or []:
        excluded_pages.update(range(ex_start, ex_end + 1))

    ranges: list[tuple[int, int]] = []
    p = start_page
    while p <= end_page:
        if p in excluded_pages:
            p += 1
            continue
        page = pages_by_number.get(p)
        if page is None or not is_start(page):
            p += 1
            continue

        section_start = p
        section_end = p
        n = p + 1
        while n <= end_page:
            if n in excluded_pages:
                break
            next_page = pages_by_number.get(n)
            if next_page is None or not is_continuation(next_page):
                break
            section_end = n
            n += 1

        ranges.append((section_start, section_end))
        p = section_end + 1
    return ranges


def _looks_plan_page(p: dict) -> bool:
    """True for floorplans, title plans, location plans, demise/property plans."""
    text = _page_text(p)
    if not text:
        return False

    # Exclude front matter/non-plan pages that can contain logos/stamps/images.
    if (
        _looks_true_front_page(p)
        or _looks_toc_start_page(p)
        or _looks_lease_summary_start_page(p)
        or _looks_lr_start_page(p)
    ):
        return False

    plan_words = bool(
        re.search(
            r"\b("
            r"floor\s*plan|floorplan|title plan|location plan|site plan|demise plan|"
            r"lease plan|car park allocation plan|property plan|land registry official copy of title plan|"
            r"ordnance survey|scale\s*:\s*1:|north approx|north arrow"
            r")\b",
            text,
        )
    )
    generic_plan_heading = any(
        re.match(r"^(#\s*)?(plan|plans)\b", line)
        for line in _page_lines(p)[:6]
    )
    has_image = _image_item_count(p) > 0

    image_alt_has_plan = any(
        re.search(
            r"!\[[^\]]*(floor\s*plan|title plan|location plan|site plan|lease plan|"
            r"car park allocation plan|plan map|architectural floor plan)[^\]]*\]\(",
            it.get("md") or "",
            re.I,
        )
        for it in (p.get("items") or [])
    )

    return (has_image and plan_words) or generic_plan_heading or image_alt_has_plan


def _detect_plan_ranges(
    pages_by_number: dict[int, dict],
    start_page: int,
    end_page: int,
    exclude_ranges: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    excluded_pages: set[int] = set()
    for ex_start, ex_end in exclude_ranges or []:
        excluded_pages.update(range(ex_start, ex_end + 1))

    plan_pages = [
        p
        for p in range(start_page, end_page + 1)
        if p not in excluded_pages
        and pages_by_number.get(p) is not None
        and _looks_plan_page(pages_by_number[p])
    ]
    if not plan_pages:
        return []

    ranges: list[tuple[int, int]] = []
    s = prev = plan_pages[0]
    for p in plan_pages[1:]:
        # Allow a one-page gap inside plan packets. Some OCR outputs alternate
        # between "lease plan" and "location plan" cues or lose text on image pages.
        if p <= prev + 2:
            prev = p
            continue
        ranges.append((s, prev))
        s = prev = p
    ranges.append((s, prev))
    return ranges


def _looks_schedule_start_page(p: dict) -> bool:
    text = _page_text(p)
    if not text:
        return False
    if _looks_toc_start_page(p):
        return False
    return any(
        re.match(
            r"^(the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)?\s*schedule\b"
            r"|^schedule\s+\d+\b",
            re.sub(r"</?u>", "", line).strip(),
            re.I,
        )
        for line in _page_lines(p)[:8]
    )


def _first_schedule_page(
    pages_by_number: dict[int, dict],
    start_page: int,
    end_page: int,
    exclude_ranges: list[tuple[int, int]] | None = None,
) -> int | None:
    excluded_pages: set[int] = set()
    for ex_start, ex_end in exclude_ranges or []:
        excluded_pages.update(range(ex_start, ex_end + 1))
    for p in range(start_page, end_page + 1):
        if p in excluded_pages:
            continue
        page = pages_by_number.get(p)
        if page is not None and _looks_schedule_start_page(page):
            return p
    return None


def _schedule_start_match(line: str) -> re.Match | None:
    clean = re.sub(r"</?u>", "", line).strip()
    clean = re.sub(r"^#+\s*", "", clean).strip()
    return re.match(
        r"^(the\s+)?"
        r"((first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+schedule"
        r"|schedule\s+\d+)"
        r"\b[\s\-:–—]*(.*)$",
        clean,
        re.I,
    )


def _detect_schedule_sections(
    pages_by_number: dict[int, dict],
    start_page: int,
    end_page: int,
    evidence_index: dict[str, Any],
    exclude_ranges: list[tuple[int, int]] | None = None,
) -> list[dict[str, object]]:
    excluded_pages: set[int] = set()
    for ex_start, ex_end in exclude_ranges or []:
        excluded_pages.update(range(ex_start, ex_end + 1))

    starts: list[dict[str, object]] = []
    for p in range(start_page, end_page + 1):
        if p in excluded_pages:
            continue
        page = pages_by_number.get(p)
        if page is None:
            continue
        seen_page_tokens: set[tuple[int, str]] = set()
        page_starts: list[dict[str, object]] = []
        for line in _page_lines(page):
            m = _schedule_start_match(line)
            if not m:
                continue
            token = re.sub(r"\s+", " ", m.group(2)).strip().title()
            dedupe_key = (p, token)
            if dedupe_key in seen_page_tokens:
                continue
            seen_page_tokens.add(dedupe_key)
            title = re.sub(r"\s+", " ", (m.group(4) or "")).strip(" .:-")
            page_starts.append(
                {
                    "token": token,
                    "title": title,
                    "start_page": p,
                    "start_evidence_id": _resolve_anchor_evidence_id(
                        evidence_index,
                        p,
                        token,
                        title,
                    ),
                }
            )
        starts.extend(page_starts)

    if not starts:
        return []

    starts.sort(key=lambda s: int(s["start_page"]))
    schedules: list[dict[str, object]] = []
    for idx, s in enumerate(starts):
        sp = int(s["start_page"])
        ep = int(starts[idx + 1]["start_page"]) - 1 if idx + 1 < len(starts) else end_page
        schedules.append(
            {
                "token": s["token"],
                "title": s["title"],
                "page_range": (sp, max(sp, ep)),
                "start_evidence_id": s.get("start_evidence_id"),
            }
        )
    for idx, sch in enumerate(schedules):
        start_pos = _evidence_pos(evidence_index, sch.get("start_evidence_id"))  # type: ignore[arg-type]
        if idx + 1 < len(schedules):
            next_pos = _evidence_pos(
                evidence_index,
                schedules[idx + 1].get("start_evidence_id"),  # type: ignore[arg-type]
            )
            end_pos = None if next_pos is None else max(start_pos or 0, next_pos - 1)
        else:
            _, fallback_end_id = _id_span_for_page_range(sch["page_range"], evidence_index)  # type: ignore[arg-type]
            end_pos = _evidence_pos(evidence_index, fallback_end_id)
        sch["end_evidence_id"] = _span_from_positions(evidence_index, start_pos, end_pos)[1]
    return schedules


def _schedule_parent_anchors_from_lines(lines: list[str], idx: int) -> list[tuple[str, str]]:
    line = re.sub(r"</?u>", "", lines[idx]).strip()
    if not line:
        return []

    out: list[tuple[str, str]] = []

    # PART headings count as parent markers inside schedules.
    m_part = re.match(r"^(part\s+[a-z0-9ivx]+)\b[\s\-:–—]*(.*)$", line, re.I)
    if m_part:
        token = re.sub(r"\s+", " ", m_part.group(1)).strip().title()
        title = re.sub(r"\s+", " ", (m_part.group(2) or "")).strip(" .:-")
        out.append((token, title))
        return out

    # Standalone numeric token then next line is title.
    m_tok = re.match(r"^(\d+)[.)]?$", line)
    if m_tok and idx + 1 < len(lines):
        nxt = re.sub(r"</?u>", "", lines[idx + 1]).strip()
        if nxt and len(nxt) <= 180:
            out.append((m_tok.group(1), nxt))
            return out

    # Short topical subheadings inside schedules are often not numbered
    # (e.g. Schedule 1 / "The Property" / 1.1 ...). Treat only high-signal
    # property headings as schedule children so topic routing can claim their
    # body text without scanning every schedule line for keywords.
    if re.match(
        r"^(the\s+)?(property|premises|demised premises|demise)$",
        line,
        re.I,
    ):
        title = re.sub(r"\s+", " ", line).strip(" .:-")
        out.append((title, title))
        return out

    # Parse one or many parent numeric clauses on same OCR line:
    # e.g. "1. xxx 2. yyy 3. zzz"
    #
    # Exclude child patterns like 1.1 / 2.1.
    matches = list(re.finditer(r"(?:^|\s)(\d+)[.)]\s+", line))
    if not matches:
        return out

    for i, m in enumerate(matches):
        token = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        title = line[start:end].strip(" .:-,;")
        if not title:
            continue
        if re.match(r"^\d+\.\d+\b", title):
            continue
        if len(title) > 180:
            title = title[:180].rstrip(" ,;:-")
        out.append((token, title))

    return out


def _detect_schedule_parent_clauses(
    pages_by_number: dict[int, dict],
    schedule_range: tuple[int, int],
    evidence_index: dict[str, Any],
    schedule_end_evidence_id: str | None = None,
) -> list[dict[str, object]]:
    s, e = schedule_range
    anchors: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    order = 0

    for p in range(s, e + 1):
        page = pages_by_number.get(p)
        if page is None:
            continue
        lines = _page_lines(page)
        for idx, _line in enumerate(lines):
            parsed_many = _schedule_parent_anchors_from_lines(lines, idx)
            for token, title in parsed_many:
                # Exclude child/sub numbering if OCR inserted in token/title start.
                if re.match(r"^\d+\.\d+", token):
                    continue
                if re.match(r"^\d+\.\d+", title):
                    continue
                key = (token, p)
                if key in seen:
                    continue
                seen.add(key)
                min_pos = _evidence_pos(evidence_index, anchors[-1].get("start_evidence_id")) if anchors else None  # type: ignore[arg-type]
                start_evidence_id = _resolve_anchor_evidence_id(
                    evidence_index,
                    p,
                    token,
                    title,
                    min_pos=min_pos,
                )
                anchors.append(
                    {
                        "token": token,
                        "title": title,
                        "start_page": p,
                        "start_evidence_id": start_evidence_id,
                        "order": order,
                    }
                )
                order += 1

    anchors.sort(
        key=lambda a: (
            _evidence_pos(evidence_index, a.get("start_evidence_id")) or 10**12,  # type: ignore[arg-type]
            int(a["start_page"]),
            int(a["order"]),
        )
    )
    schedule_end_pos = _evidence_pos(evidence_index, schedule_end_evidence_id)
    for idx, a in enumerate(anchors):
        start = int(a["start_page"])
        start_pos = _evidence_pos(evidence_index, a.get("start_evidence_id"))  # type: ignore[arg-type]
        if idx + 1 < len(anchors):
            nxt = int(anchors[idx + 1]["start_page"])
            next_pos = _evidence_pos(evidence_index, anchors[idx + 1].get("start_evidence_id"))  # type: ignore[arg-type]
            end_pos = None if next_pos is None else max(start_pos or 0, next_pos - 1)
            end = max(start, nxt)
        else:
            end_pos = schedule_end_pos
            end = e
        a["page_range"] = (start, end)
        a["end_evidence_id"] = _span_from_positions(evidence_index, start_pos, end_pos)[1]
    return anchors


def _clause_anchor_from_lines(lines: list[str], idx: int) -> tuple[str, str, int] | None:
    line = re.sub(r"^[#*\-\s]+", "", lines[idx]).strip()
    if not line:
        return None

    # Main or first-child clause only:
    #   8 Title
    #   8.1 Title
    # Excludes:
    #   8.1.1 deeper grandchildren
    m = re.match(r"^(\d+(?:\.\d+)?)[.)]?\s+(.+)$", line)
    if not m:
        # Sometimes OCR splits "2.1" and the title into separate items.
        m_token = re.match(r"^(\d+(?:\.\d+)?)[.)]?$", line)
        if not m_token or idx + 1 >= len(lines):
            return None
        token = m_token.group(1)
        title = lines[idx + 1].strip()
    else:
        token = m.group(1)
        title = m.group(2).strip()

    parts = token.split(".")
    level = len(parts)
    if level > 2:
        return None
    if not parts[0].isdigit() or int(parts[0]) < 1 or int(parts[0]) > 40:
        return None

    title = re.sub(r"\s+", " ", title).strip(" .:-")
    if not title or len(title) < 2:
        return None
    if len(title) > 180:
        title = title[:180].rstrip(" ,;:-")
    if re.search(r"\b(docusign|envelope id|official copy is incomplete)\b", title, re.I):
        return None
    if level == 1 and re.match(r"^\d+\.\d+\b", title):
        # Page-number/OCR artifact, e.g. "2 1.9. '2003 order' means..."
        return None
    if re.match(r"^(page|clause|schedule)$", title, re.I):
        return None

    return token, title, level


def _detect_clause_ranges(
    pages_by_number: dict[int, dict],
    start_page: int,
    end_page: int,
    evidence_index: dict[str, Any],
    exclude_ranges: list[tuple[int, int]] | None = None,
) -> list[dict[str, object]]:
    excluded_pages: set[int] = set()
    for ex_start, ex_end in exclude_ranges or []:
        excluded_pages.update(range(ex_start, ex_end + 1))

    schedule_start = _first_schedule_page(
        pages_by_number,
        start_page,
        end_page,
        exclude_ranges=exclude_ranges,
    )
    clause_end_page = (schedule_start - 1) if schedule_start else end_page

    anchors: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    order = 0
    for p in range(start_page, clause_end_page + 1):
        if p in excluded_pages:
            continue
        page = pages_by_number.get(p)
        if page is None:
            continue
        lines = _page_lines(page)
        for idx, _line in enumerate(lines):
            parsed = _clause_anchor_from_lines(lines, idx)
            if parsed is None:
                continue
            token, title, level = parsed
            key = (token, p)
            if key in seen:
                continue
            seen.add(key)
            min_pos = _evidence_pos(evidence_index, anchors[-1].get("start_evidence_id")) if anchors else None  # type: ignore[arg-type]
            start_evidence_id = _resolve_anchor_evidence_id(
                evidence_index,
                p,
                token,
                title,
                min_pos=min_pos,
            )
            anchors.append(
                {
                    "token": token,
                    "title": title,
                    "level": level,
                    "start_page": p,
                    "start_evidence_id": start_evidence_id,
                    "order": order,
                }
            )
            order += 1

    # Keep reading order, then reject obvious backwards top-level restarts.
    filtered: list[dict[str, object]] = []
    current_top: int | None = None
    max_top_seen = 0
    for anchor in anchors:
        token = str(anchor["token"])
        parts = token.split(".")
        top = int(parts[0])
        level = int(anchor["level"])

        if level == 1:
            if top < max_top_seen:
                # Usually schedule/bullet numbering restarting after main body.
                continue
            current_top = top
            max_top_seen = max(max_top_seen, top)
        elif current_top is None or top != current_top:
            # Child clause must belong to the current parent clause.
            continue
        filtered.append(anchor)

    # Assign page ranges by hierarchy:
    # - parent clause spans until the next parent clause
    # - child clause spans until the next sibling child OR next parent
    # This means clause 8 includes 8.1/8.2/etc, and 8.1 includes lower
    # unprinted grandchildren such as 8.1.1 until 8.2 starts.
    for idx, anchor in enumerate(filtered):
        start = int(anchor["start_page"])
        level = int(anchor["level"])
        start_pos = _evidence_pos(evidence_index, anchor.get("start_evidence_id"))  # type: ignore[arg-type]
        next_boundary_page: int | None = None
        next_boundary_pos: int | None = None

        for later in filtered[idx + 1:]:
            later_level = int(later["level"])
            if later_level <= level:
                next_boundary_page = int(later["start_page"])
                next_boundary_pos = _evidence_pos(evidence_index, later.get("start_evidence_id"))  # type: ignore[arg-type]
                break

        end = clause_end_page if next_boundary_page is None else max(start, next_boundary_page)
        anchor["page_range"] = (start, end)
        if next_boundary_pos is None:
            _, fallback_end_id = _id_span_for_page_range((start, end), evidence_index)
            end_pos = _evidence_pos(evidence_index, fallback_end_id)
        else:
            end_pos = max(start_pos or 0, next_boundary_pos - 1)
        anchor["end_evidence_id"] = _span_from_positions(evidence_index, start_pos, end_pos)[1]
    return filtered


def _print_clause_ranges(
    clause_ranges: list[dict[str, object]],
    evidence_index: dict[str, Any],
) -> None:
    print("  clauses:")
    if not clause_ranges:
        print("    none")
        return
    for c in clause_ranges:
        id_span = _format_evidence_span(
            c.get("start_evidence_id"),  # type: ignore[arg-type]
            c.get("end_evidence_id"),  # type: ignore[arg-type]
        )
        indent = "    " if c["level"] == 1 else "      "
        print(f"{indent}{c['token']} {c['title']}: {id_span}")


def _print_schedule_ranges(
    schedules: list[dict[str, object]],
    evidence_index: dict[str, Any],
) -> None:
    print("  schedules:")
    if not schedules:
        print("    none")
        return
    for sch in schedules:
        id_span = _format_evidence_span(
            sch.get("start_evidence_id"),  # type: ignore[arg-type]
            sch.get("end_evidence_id"),  # type: ignore[arg-type]
        )
        heading = f"{sch['token']} {sch.get('title') or ''}".strip()
        print(f"    {heading}: {id_span}")
        parents = sch.get("parent_clauses") or []
        if not parents:
            print("      parent_clauses: none")
            continue
        print("      parent_clauses:")
        for p in parents:
            id_span = _format_evidence_span(
                p.get("start_evidence_id"),  # type: ignore[arg-type]
                p.get("end_evidence_id"),  # type: ignore[arg-type]
            )
            title = f"{p['token']} {p.get('title') or ''}".strip()
            print(f"        {title}: {id_span}")


def _extract_definitions_section(
    clause_ranges: list[dict[str, object]],
    exclude_ranges: list[tuple[int, int]] | None = None,
) -> tuple[list[tuple[int, int]], list[dict[str, object]]]:
    """
    Pull definitions+interpretation into their own section range.

    Rule:
    - Start from the beginning of top-level clauses.
    - Include consecutive top-level clauses whose title mentions definitions
      or interpretation.
    - Remove included parent clauses and their children from the general
      clause list.
    """
    top_levels = [c for c in clause_ranges if int(c["level"]) == 1]
    if not top_levels:
        return [], clause_ranges

    def _is_def_like(title: str) -> bool:
        t = (title or "").lower()
        return ("definition" in t) or ("interpretation" in t)

    selected_parents: list[dict[str, object]] = []
    for c in top_levels:
        if not selected_parents:
            if _is_def_like(str(c["title"])):
                selected_parents.append(c)
            else:
                break
        else:
            if _is_def_like(str(c["title"])):
                selected_parents.append(c)
            else:
                break

    if not selected_parents:
        return [], clause_ranges

    parent_tokens = {str(c["token"]) for c in selected_parents}

    def _belongs_to_selected(anchor: dict[str, object]) -> bool:
        tok = str(anchor["token"])
        if tok in parent_tokens:
            return True
        for pt in parent_tokens:
            if tok.startswith(f"{pt}."):
                return True
        return False

    selected = [c for c in clause_ranges if _belongs_to_selected(c)]
    kept = [c for c in clause_ranges if not _belongs_to_selected(c)]

    start = min(int(c["page_range"][0]) for c in selected)  # type: ignore[index]
    end = max(int(c["page_range"][1]) for c in selected)    # type: ignore[index]

    excluded_pages: set[int] = set()
    for ex_start, ex_end in exclude_ranges or []:
        excluded_pages.update(range(ex_start, ex_end + 1))

    # Remove excluded pages (e.g. plan/floorplan insertions) and keep the
    # contiguous chunk starting at `start` for a clean definitions section.
    pages = [p for p in range(start, end + 1) if p not in excluded_pages]
    if not pages:
        return [], kept

    run_start = pages[0]
    run_end = run_start
    for p in pages[1:]:
        if p == run_end + 1:
            run_end = p
        else:
            break

    return [(run_start, run_end)], kept


def _detect_ranges_and_covers(
    pages: list[dict],
) -> tuple[list[tuple[int, int]], list[int]]:
    if not pages:
        return [], []

    page_numbers = [int(p.get("page_number", i + 1)) for i, p in enumerate(pages)]
    front_pages = [
        int(p.get("page_number", i + 1))
        for i, p in enumerate(pages)
        if _looks_true_front_page(p)
    ]

    # One true title page means one lease.  Two true title pages means current
    # instrument followed by annexed old/existing lease.  Ignore any later
    # accidental candidates; this script's job is only old/new page ranges.
    if len(front_pages) >= 2:
        starts = [page_numbers[0], front_pages[1]]
    else:
        starts = [page_numbers[0]]
    ranges: list[tuple[int, int]] = []
    for i, s in enumerate(starts):
        e = starts[i + 1] - 1 if i + 1 < len(starts) else page_numbers[-1]
        ranges.append((s, e))
    return ranges, front_pages


def _classify_pack(ranges: list[tuple[int, int]]) -> str:
    return "current+old" if len(ranges) >= 2 else "single"


def _format_ranges(ranges: list[tuple[int, int]]) -> str:
    if not ranges:
        return "none"
    return ", ".join(f"{s}-{e}" for s, e in ranges)


def _find_parse_jsons(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for lease_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        nested = lease_dir / lease_dir.name / "parse.json"
        flat = lease_dir / "parse.json"
        chosen = nested if nested.exists() else flat if flat.exists() else None
        if chosen:
            out.append((lease_dir.name, chosen))
    return out


def _build_instrument_analysis(
    *,
    label: str,
    page_range: tuple[int, int],
    cover_page: int | None,
    pages_by_number: dict[int, dict],
    evidence_index: dict[str, Any],
) -> dict[str, Any]:
    """Return the detector output for one logical instrument."""
    s, e = page_range
    toc_ranges = _detect_toc_ranges(pages_by_number, s, e)
    summary_ranges = _detect_section_ranges(
        pages_by_number,
        s,
        e,
        _looks_lease_summary_start_page,
        _looks_lease_summary_continuation_page,
        exclude_ranges=toc_ranges,
    )
    lr_ranges = _detect_section_ranges(
        pages_by_number,
        s,
        e,
        _looks_lr_start_page,
        _looks_lr_continuation_page,
        exclude_ranges=toc_ranges,
    )
    excluded_for_plans = toc_ranges + summary_ranges + lr_ranges
    plan_ranges = _detect_plan_ranges(
        pages_by_number,
        s,
        e,
        exclude_ranges=excluded_for_plans,
    )
    schedule_sections = _detect_schedule_sections(
        pages_by_number,
        s,
        e,
        evidence_index,
        exclude_ranges=toc_ranges + summary_ranges + lr_ranges + plan_ranges,
    )
    for sch in schedule_sections:
        sch["parent_clauses"] = _detect_schedule_parent_clauses(
            pages_by_number,
            sch["page_range"],  # type: ignore[arg-type]
            evidence_index,
            sch.get("end_evidence_id"),  # type: ignore[arg-type]
        )
    schedule_ranges = [sch["page_range"] for sch in schedule_sections]  # type: ignore[index]

    clause_ranges = _detect_clause_ranges(
        pages_by_number,
        s,
        e,
        evidence_index,
        exclude_ranges=excluded_for_plans + plan_ranges + schedule_ranges,
    )
    definitions_ranges, clause_ranges = _extract_definitions_section(
        clause_ranges,
        exclude_ranges=plan_ranges,
    )

    start_evidence_id, end_evidence_id = _id_span_for_page_range(page_range, evidence_index)
    return {
        "label": label,
        "page_range": page_range,
        "start_evidence_id": start_evidence_id,
        "end_evidence_id": end_evidence_id,
        "cover_page": cover_page,
        "toc_ranges": toc_ranges,
        "definitions_ranges": definitions_ranges,
        "summary_ranges": summary_ranges,
        "lr_ranges": lr_ranges,
        "plan_ranges": plan_ranges,
        "schedules_section_ranges": schedule_ranges,
        "schedules": schedule_sections,
        "clause_ranges": clause_ranges,
    }


def analyze_lease_pages(
    pages: list[dict],
    evidence_rows: list[dict],
) -> dict[str, Any]:
    """
    Return structured old/new lease and section ranges.

    This is the reusable, non-printing entry point for the detector. The CLI
    below remains a reporting wrapper, while preextract can consume this
    directly to build canonical JSON artefacts.
    """
    evidence_index = _build_evidence_index(evidence_rows)
    pages_by_number = {
        int(p.get("page_number", i + 1)): p
        for i, p in enumerate(pages)
    }
    ranges, cover_pages = _detect_ranges_and_covers(pages)
    pack_type = _classify_pack(ranges)

    instruments: list[dict[str, Any]] = []
    for idx, page_range in enumerate(ranges):
        if pack_type == "single":
            label = "lease"
        elif idx == 0:
            label = "current_instrument"
        elif idx == 1:
            label = "annexed_old_lease"
        else:
            label = f"additional_instrument_{idx + 1}"

        instruments.append(
            _build_instrument_analysis(
                label=label,
                page_range=page_range,
                cover_page=cover_pages[idx] if idx < len(cover_pages) else None,
                pages_by_number=pages_by_number,
                evidence_index=evidence_index,
            )
        )

    return {
        "type": pack_type,
        "page_ranges": ranges,
        "cover_pages": cover_pages,
        "instruments": instruments,
    }


def _print_instrument_block(
    *,
    label: str,
    page_range: tuple[int, int],
    cover_page: int | None,
    toc_ranges: list[tuple[int, int]],
    definitions_ranges: list[tuple[int, int]],
    summary_ranges: list[tuple[int, int]],
    lr_ranges: list[tuple[int, int]],
    plan_ranges: list[tuple[int, int]],
    schedules_section_ranges: list[tuple[int, int]],
    schedules: list[dict[str, object]],
    clause_ranges: list[dict[str, object]],
    evidence_index: dict[str, dict[int, list[str]]],
) -> None:
    print(f"{label}: {_format_id_span(page_range, evidence_index)}")
    if cover_page is not None:
        print(f"  cover_page: {_format_id_span((cover_page, cover_page), evidence_index)}")
    print(f"  table_of_contents: {_format_id_spans(toc_ranges, evidence_index)}")
    print(f"  definitions_section: {_format_id_spans(definitions_ranges, evidence_index)}")
    print(f"  lease_summary: {_format_id_spans(summary_ranges, evidence_index)}")
    print(f"  land_registry_prescribed_clauses: {_format_id_spans(lr_ranges, evidence_index)}")
    print(f"  plans: {_format_id_spans(plan_ranges, evidence_index)}")
    print(f"  schedules_section: {_format_id_spans(schedules_section_ranges, evidence_index)}")
    _print_schedule_ranges(schedules, evidence_index)
    _print_clause_ranges(clause_ranges, evidence_index)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print page ranges for current/old lease detection across all leases."
    )
    parser.add_argument(
        "--root",
        default="preextract_outputs",
        help="Root folder containing lease output subdirectories.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"Not found: {root}")

    targets = _find_parse_jsons(root)
    if not targets:
        raise SystemExit("No parse.json files found.")

    for lease_name, parse_path in targets:
        pages = _load_pages(parse_path)
        evidence_path = parse_path.with_name("evidence_manifest.json")
        if not evidence_path.exists():
            raise SystemExit(f"Missing evidence manifest for {lease_name}: {evidence_path}")
        evidence_rows = _load_evidence_rows(evidence_path)
        evidence_index = _build_evidence_index(evidence_rows)
        pages_by_number = {
            int(p.get("page_number", i + 1)): p
            for i, p in enumerate(pages)
        }
        ranges, cover_pages = _detect_ranges_and_covers(pages)
        pack_type = _classify_pack(ranges)

        print(f"\n=== {lease_name} ===")
        print(f"type: {pack_type}")
        if not ranges:
            print("no pages found")
            continue

        if pack_type == "single":
            s, e = ranges[0]
            toc_ranges = _detect_toc_ranges(pages_by_number, s, e)
            summary_ranges = _detect_section_ranges(
                pages_by_number,
                s,
                e,
                _looks_lease_summary_start_page,
                _looks_lease_summary_continuation_page,
                exclude_ranges=toc_ranges,
            )
            lr_ranges = _detect_section_ranges(
                pages_by_number,
                s,
                e,
                _looks_lr_start_page,
                _looks_lr_continuation_page,
                exclude_ranges=toc_ranges,
            )
            excluded_for_plans = toc_ranges + summary_ranges + lr_ranges
            plan_ranges = _detect_plan_ranges(
                pages_by_number,
                s,
                e,
                exclude_ranges=excluded_for_plans,
            )
            schedule_sections = _detect_schedule_sections(
                pages_by_number,
                s,
                e,
                evidence_index,
                exclude_ranges=toc_ranges + summary_ranges + lr_ranges + plan_ranges,
            )
            for sch in schedule_sections:
                sch["parent_clauses"] = _detect_schedule_parent_clauses(
                    pages_by_number,
                    sch["page_range"],  # type: ignore[arg-type]
                    evidence_index,
                    sch.get("end_evidence_id"),  # type: ignore[arg-type]
                )
            schedule_ranges = [sch["page_range"] for sch in schedule_sections]  # type: ignore[index]

            excluded_for_clauses = excluded_for_plans + plan_ranges + schedule_ranges
            clause_ranges = _detect_clause_ranges(
                pages_by_number,
                s,
                e,
                evidence_index,
                exclude_ranges=excluded_for_clauses,
            )
            definitions_ranges, clause_ranges = _extract_definitions_section(
                clause_ranges,
                exclude_ranges=plan_ranges,
            )
            _print_instrument_block(
                label="lease",
                page_range=(s, e),
                cover_page=cover_pages[0] if cover_pages else None,
                toc_ranges=toc_ranges,
                definitions_ranges=definitions_ranges,
                summary_ranges=summary_ranges,
                lr_ranges=lr_ranges,
                plan_ranges=plan_ranges,
                schedules_section_ranges=schedule_ranges,
                schedules=schedule_sections,
                clause_ranges=clause_ranges,
                evidence_index=evidence_index,
            )
        else:
            s1, e1 = ranges[0]
            s2, e2 = ranges[1]
            current_toc_ranges = _detect_toc_ranges(pages_by_number, s1, e1)
            annexed_toc_ranges = _detect_toc_ranges(pages_by_number, s2, e2)
            current_summary_ranges = _detect_section_ranges(
                pages_by_number,
                s1,
                e1,
                _looks_lease_summary_start_page,
                _looks_lease_summary_continuation_page,
                exclude_ranges=current_toc_ranges,
            )
            annexed_summary_ranges = _detect_section_ranges(
                pages_by_number,
                s2,
                e2,
                _looks_lease_summary_start_page,
                _looks_lease_summary_continuation_page,
                exclude_ranges=annexed_toc_ranges,
            )
            current_lr_ranges = _detect_section_ranges(
                pages_by_number,
                s1,
                e1,
                _looks_lr_start_page,
                _looks_lr_continuation_page,
                exclude_ranges=current_toc_ranges,
            )
            annexed_lr_ranges = _detect_section_ranges(
                pages_by_number,
                s2,
                e2,
                _looks_lr_start_page,
                _looks_lr_continuation_page,
                exclude_ranges=annexed_toc_ranges,
            )
            current_plan_ranges = _detect_plan_ranges(
                pages_by_number,
                s1,
                e1,
                exclude_ranges=current_toc_ranges
                + current_summary_ranges
                + current_lr_ranges,
            )
            annexed_plan_ranges = _detect_plan_ranges(
                pages_by_number,
                s2,
                e2,
                exclude_ranges=annexed_toc_ranges
                + annexed_summary_ranges
                + annexed_lr_ranges,
            )
            current_schedule_sections = _detect_schedule_sections(
                pages_by_number,
                s1,
                e1,
                evidence_index,
                exclude_ranges=current_toc_ranges
                + current_summary_ranges
                + current_lr_ranges
                + current_plan_ranges,
            )
            for sch in current_schedule_sections:
                sch["parent_clauses"] = _detect_schedule_parent_clauses(
                    pages_by_number,
                    sch["page_range"],  # type: ignore[arg-type]
                    evidence_index,
                    sch.get("end_evidence_id"),  # type: ignore[arg-type]
                )
            current_schedule_ranges = [sch["page_range"] for sch in current_schedule_sections]  # type: ignore[index]

            annexed_schedule_sections = _detect_schedule_sections(
                pages_by_number,
                s2,
                e2,
                evidence_index,
                exclude_ranges=annexed_toc_ranges
                + annexed_summary_ranges
                + annexed_lr_ranges
                + annexed_plan_ranges,
            )
            for sch in annexed_schedule_sections:
                sch["parent_clauses"] = _detect_schedule_parent_clauses(
                    pages_by_number,
                    sch["page_range"],  # type: ignore[arg-type]
                    evidence_index,
                    sch.get("end_evidence_id"),  # type: ignore[arg-type]
                )
            annexed_schedule_ranges = [sch["page_range"] for sch in annexed_schedule_sections]  # type: ignore[index]
            current_clause_ranges = _detect_clause_ranges(
                pages_by_number,
                s1,
                e1,
                evidence_index,
                exclude_ranges=current_toc_ranges
                + current_summary_ranges
                + current_lr_ranges
                + current_plan_ranges
                + current_schedule_ranges,
            )
            current_definitions_ranges, current_clause_ranges = _extract_definitions_section(
                current_clause_ranges,
                exclude_ranges=current_plan_ranges,
            )
            annexed_clause_ranges = _detect_clause_ranges(
                pages_by_number,
                s2,
                e2,
                evidence_index,
                exclude_ranges=annexed_toc_ranges
                + annexed_summary_ranges
                + annexed_lr_ranges
                + annexed_plan_ranges
                + annexed_schedule_ranges,
            )
            annexed_definitions_ranges, annexed_clause_ranges = _extract_definitions_section(
                annexed_clause_ranges,
                exclude_ranges=annexed_plan_ranges,
            )
            _print_instrument_block(
                label="current_instrument",
                page_range=(s1, e1),
                cover_page=cover_pages[0] if cover_pages else None,
                toc_ranges=current_toc_ranges,
                definitions_ranges=current_definitions_ranges,
                summary_ranges=current_summary_ranges,
                lr_ranges=current_lr_ranges,
                plan_ranges=current_plan_ranges,
                schedules_section_ranges=current_schedule_ranges,
                schedules=current_schedule_sections,
                clause_ranges=current_clause_ranges,
                evidence_index=evidence_index,
            )
            _print_instrument_block(
                label="annexed_old_lease",
                page_range=(s2, e2),
                cover_page=cover_pages[1] if len(cover_pages) >= 2 else None,
                toc_ranges=annexed_toc_ranges,
                definitions_ranges=annexed_definitions_ranges,
                summary_ranges=annexed_summary_ranges,
                lr_ranges=annexed_lr_ranges,
                plan_ranges=annexed_plan_ranges,
                schedules_section_ranges=annexed_schedule_ranges,
                schedules=annexed_schedule_sections,
                clause_ranges=annexed_clause_ranges,
                evidence_index=evidence_index,
            )
            if len(ranges) > 2:
                for idx, (s, e) in enumerate(ranges[2:], start=3):
                    print(
                        f"additional_instrument_{idx}: "
                        f"{_format_id_span((s, e), evidence_index)}"
                    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


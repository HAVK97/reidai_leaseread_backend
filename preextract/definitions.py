"""Build the definitions/interpretation index from structure and evidence.

structure_v2 intentionally gives this stage a broad "Definitions and
Interpretation" envelope.  This module then performs the row-level split:

* term definitions: exact defined-term spans, safe for downstream term lookup
* interpretation rules: legal interpretation rows kept separate from terms

That split keeps interpretation text available without letting it pollute the
final term span for the preceding definition.
"""

from __future__ import annotations

import re
import unicodedata

SCHEMA_VERSION = "1.2"

# ---------------------------------------------------------------------------
# Term extraction patterns
# ---------------------------------------------------------------------------

# Priority-ordered patterns; first match wins for a given text row.  The prefix
# allows old leases that number each definition, e.g. `1.1. "Accountant" means`.
_ROW_PREFIX = r"^(?:\d+(?:\.\d+)*[.)]?\s+)?"
_TERM_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "Term" means ... (straight and smart quotes)
    (
        re.compile(
            _ROW_PREFIX
            + r'["\'\u201c\u2018]([^"\'\u201d\u2019]{1,80})["\'\u201d\u2019]\s+'
            r'(?:means?|shall mean|has the meaning|is defined)',
            re.I,
        ),
        "quoted",
    ),
    # Term means ... (plain text after markdown decoration is stripped)
    (
        re.compile(
            _ROW_PREFIX
            + r'([^"\':]{1,80})\s+'
            r'(?:means?|shall mean|has the meaning|is defined)\b'
        ),
        "title_case",
    ),
    # "Term": ... (straight and smart quotes)
    (
        re.compile(
            _ROW_PREFIX
            + r'["\'\u201c\u2018]([^"\'\u201d\u2019]{1,80})["\'\u201d\u2019]\s*:\s*',
            re.I,
        ),
        "quoted_colon",
    ),
    # Term: ... (leading title-case word(s), no quotes)
    (
        re.compile(
            _ROW_PREFIX + r"([A-Z][A-Za-z0-9\s&'\-\(\)]{1,80})\s*:\s*"
        ),
        "title_case_colon",
    ),
]

# Pattern for "in this lease / deed, the following expressions have the
# following meanings" style preambles — skip these rows.
_PREAMBLE_RE = re.compile(
    r"\b(the following definitions|following definitions|rules of interpretation apply)\b",
    re.I,
)

_FALSE_TERMS = {
    "agreed terms",
    "background",
    "definitions",
    "interpretation",
    "parties",
}

_ROMAN_MARKER_RE = re.compile(
    r"^(?=[ivxlcdm]+$)m{0,4}(cm|cd|d?c{0,3})"
    r"(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$",
    re.I,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def _match_text(text: str) -> str:
    """Normalize OCR/markdown decoration before applying legal term patterns."""
    text = _norm(text)
    text = text.replace("**", "").replace("__", "")
    return text.strip()


def _clean_term(term: str) -> str:
    term = _norm(term)
    term = term.replace("**", "").replace("__", "")
    return term.strip(" \"'\u201c\u201d\u2018\u2019:;.")


def _extract_term(text: str) -> tuple[str | None, str]:
    """
    Try to extract a defined term from a text row.

    Returns (term | None, source_pattern).
    """
    normalized = _match_text(text)
    if not normalized:
        return None, "no_match"
    if _PREAMBLE_RE.search(normalized):
        return None, "no_match"
    for pat, source in _TERM_PATTERNS:
        m = pat.match(normalized)
        if m:
            term = _clean_term(m.group(1))
            norm_term = _normalize_term(term)
            if (
                2 <= len(term) <= 80
                and len(term.split()) <= 6
                and norm_term not in _FALSE_TERMS
            ):
                return term, source
    return None, "no_match"


def _normalize_term(term: str) -> str:
    """Return a lowercase, whitespace-collapsed version for dedup keying."""
    return re.sub(r"[^a-z0-9 ]", "", term.lower()).strip()


def _looks_top_level_clause_heading(text: str) -> bool:
    """
    True when a row looks like a new top-level clause heading, e.g.
      "3. TENANT'S COVENANTS"
      "4 RENT REVIEW"
    Used as a hard stop to prevent definitions spans bleeding into later clauses
    if section assignment drifts.
    """
    t = _norm(text)
    if not t:
        return False
    # Strong signal: numeric heading with uppercase/legal-title body.
    if re.match(r"^\d{1,2}[.)]\s+[A-Z][A-Z'\-\s&]{4,}$", text.strip()):
        return True
    if re.match(r"^\d{1,2}\s+[A-Z][A-Z'\-\s&]{4,}$", text.strip()):
        return True
    return False


def _looks_footer_or_noise(text: str) -> bool:
    """Filter page footers and OCR fragments that should not join spans."""
    t = _norm(text)
    if not t:
        return True
    if re.match(r"^[A-Z]{2,}/[A-Z]{2,}/[\dA-Z./-]+\s+\d{1,4}$", t):
        return True
    if re.match(r"^\d{1,4}$", t):
        return True
    return False


def _looks_interpretation_heading(text: str) -> bool:
    """
    Detect the start of interpretation rules, not every "In this Lease" row.

    `1.2 In this Lease:` can introduce definitions in some leases, so this is
    intentionally narrower than "contains in this lease".
    """
    t = _match_text(text).lower()
    t = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", t)
    t = t.strip(" .:")
    if t.startswith("in this lease, unless the context otherwise requires"):
        return True
    if t.startswith("unless the context otherwise requires"):
        return True
    if t.startswith("references to the landlord and tenant"):
        return True
    return False


def _interpretation_marker_info(text: str) -> dict | None:
    t = _match_text(text)
    m = re.match(r"^\(([a-z]+)\)\s+", t, re.I)
    if m:
        marker = m.group(1).lower()
        marker_type = "roman" if _ROMAN_MARKER_RE.match(marker) else "alpha"
        return {"marker": marker, "marker_type": marker_type}
    m = re.match(r"^(\d+(?:\.\d+)*)[.)]?\s+", t)
    if m:
        return {"marker": m.group(1), "marker_type": "numeric"}
    return None


def _interpretation_marker(text: str) -> str | None:
    info = _interpretation_marker_info(text)
    return None if info is None else str(info["marker"])


def _starts_nested_list(text: str) -> bool:
    t = _match_text(text).rstrip()
    return t.endswith(":")


def _roman_to_int(marker: str) -> int | None:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    prev = 0
    for ch in reversed(marker.lower()):
        value = values.get(ch)
        if value is None:
            return None
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total


def _interpretation_row_indices(rows: list[dict]) -> set[int]:
    indices: set[int] = set()
    active = False
    for idx, row in enumerate(rows):
        text = row.get("text") or ""
        if _looks_interpretation_heading(text):
            active = True
            indices.add(idx)
            continue
        if active and _looks_top_level_clause_heading(text):
            active = False
        if active:
            indices.add(idx)
    return indices


def _build_interpretation_rules(rows: list[dict], segment_id: str) -> list[dict]:
    rules: list[dict] = []
    idx = 0
    rule_seq = 1

    def _row_ids(group: list[dict]) -> list[str]:
        return [r["evidence_id"] for r in group if r.get("evidence_id")]

    def _row_pages(group: list[dict]) -> list[int]:
        return sorted({int(r["page_number"]) for r in group if r.get("page_number")})

    while idx < len(rows):
        row = rows[idx]
        text = row.get("text") or ""
        if not _looks_interpretation_heading(text):
            idx += 1
            continue

        heading_eid = row.get("evidence_id")
        rules.append({
            "rule_id": f"{segment_id}:interpretation:{rule_seq}",
            "segment_id": segment_id,
            "kind": "interpretation_heading",
            "marker": None,
            "evidence_ids": [heading_eid] if heading_eid else [],
            "page_numbers": [int(row.get("page_number") or 0)],
        })
        rule_seq += 1
        idx += 1

        while idx < len(rows):
            row = rows[idx]
            text = row.get("text") or ""
            if _looks_interpretation_heading(text):
                break
            if _looks_top_level_clause_heading(text):
                break
            if _looks_footer_or_noise(text):
                idx += 1
                continue

            marker_info = _interpretation_marker_info(text)
            marker = None if marker_info is None else marker_info["marker"]
            marker_type = None if marker_info is None else marker_info["marker_type"]
            if marker_type == "roman" and isinstance(marker, str) and len(marker) == 1:
                marker_type = "alpha"
            parent_group = [row]
            children: list[dict] = []
            idx += 1

            nested_mode = marker_type == "alpha" and _starts_nested_list(text)
            last_nested_roman = 0
            while idx < len(rows):
                next_row = rows[idx]
                next_text = next_row.get("text") or ""
                if _looks_interpretation_heading(next_text) or _looks_top_level_clause_heading(next_text):
                    break

                next_marker_info = _interpretation_marker_info(next_text)
                if next_marker_info:
                    next_marker_type = next_marker_info["marker_type"]
                    if nested_mode and next_marker_type == "roman":
                        roman_value = _roman_to_int(str(next_marker_info["marker"]))
                        if roman_value is None or roman_value <= last_nested_roman:
                            break
                        last_nested_roman = roman_value
                        child_group = [next_row]
                        idx += 1
                        while idx < len(rows):
                            child_next = rows[idx]
                            child_next_text = child_next.get("text") or ""
                            child_next_marker_info = _interpretation_marker_info(child_next_text)
                            if (
                                _looks_interpretation_heading(child_next_text)
                                or _looks_top_level_clause_heading(child_next_text)
                                or child_next_marker_info
                            ):
                                break
                            if not _looks_footer_or_noise(child_next_text):
                                child_group.append(child_next)
                            idx += 1

                        parent_group.extend(child_group)
                        children.append({
                            "marker": next_marker_info["marker"],
                            "marker_type": next_marker_type,
                            "evidence_ids": _row_ids(child_group),
                            "page_numbers": _row_pages(child_group),
                        })
                        continue
                    break

                if not _looks_footer_or_noise(next_text):
                    parent_group.append(next_row)
                idx += 1

            eids = _row_ids(parent_group)
            pages = _row_pages(parent_group)
            if not eids:
                continue
            rules.append({
                "rule_id": f"{segment_id}:interpretation:{rule_seq}",
                "segment_id": segment_id,
                "kind": "interpretation_rule",
                "marker": marker,
                "marker_type": marker_type,
                "evidence_ids": eids,
                "page_numbers": pages,
                "children": children,
            })
            rule_seq += 1

    return rules


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def build_definitions_index(
    manifest: dict,
    structure_map: dict,
) -> dict:
    """
    Build definitions_index.json from the evidence manifest and structure map.

    Args:
        manifest:      evidence_manifest dict.
        structure_map: structure_map dict (from preextract.structure_v2.build_structure_map_v2).

    Returns:
        definitions_index dict ready for JSON serialisation.
    """
    doc_id = manifest.get("doc_id", "unknown")
    evidence: list[dict] = manifest.get("evidence") or []

    # Build evidence lookup by id
    eid_lookup: dict[str, dict] = {r["evidence_id"]: r for r in evidence}

    all_definitions: list[dict] = []
    all_interpretation_rules: list[dict] = []

    for seg_struct in structure_map.get("segments") or []:
        segment_id = seg_struct.get("segment_id", "whole_doc")
        anchors_flat: list[dict] = seg_struct.get("anchors_flat") or []
        box_assignments: list[dict] = seg_struct.get("box_assignments") or []

        # ------------------------------------------------------------------
        # Step 1: Find definitions-clause anchor tokens
        # ------------------------------------------------------------------
        def_tokens: set[str] = set()
        for anchor in anchors_flat:
            token = anchor.get("token") or ""
            title = anchor.get("title") or ""
            family = anchor.get("family") or ""
            if family != "numbered_clauses":
                continue
            combined = f"{token} {title}".lower()
            if token == "DEFINITIONS" or any(
                kw in combined for kw in ("definition", "interpretation")
            ):
                # Grab the top-level parent token (e.g. "1" from "1.1")
                parent = token.split(".")[0] if "." in token else token
                def_tokens.add(parent)
                def_tokens.add(token)

        # ------------------------------------------------------------------
        # Step 2: Collect evidence_ids within the definitions range
        # ------------------------------------------------------------------
        def_evidence_ids: list[str] = []
        def_eid_set: set[str] = set()
        for assignment in box_assignments:
            eid = assignment.get("evidence_id")
            if not eid:
                continue
            section_token = assignment.get("section_token") or ""
            parent_token = assignment.get("parent_token") or ""
            # Belongs to definitions if section token or its parent is a def token
            if section_token in def_tokens or parent_token in def_tokens:
                def_evidence_ids.append(eid)
                def_eid_set.add(eid)

        # Rescue quoted-term definition rows mis-assigned to LR / other sections
        # (common when definitions continue across pages overlapping LR boxes).
        for assignment in box_assignments:
            eid = assignment.get("evidence_id")
            if not eid or eid in def_eid_set:
                continue
            row = eid_lookup.get(eid)
            if not row:
                continue
            text = row.get("text") or ""
            term, source_pattern = _extract_term(text)
            if not term or source_pattern not in {"quoted", "quoted_colon"}:
                continue
            if (assignment.get("section_family") or "") == "schedules":
                continue
            def_evidence_ids.append(eid)
            def_eid_set.add(eid)

        # ------------------------------------------------------------------
        # Step 3: Extract term–definition pairs from definitions evidence.
        #
        # Deterministic span capture:
        # - a term starts at the row matching _extract_term(...)
        # - its definition body extends until the next detected term start
        #   (exclusive), staying within the definitions-assigned evidence rows.
        # This captures continuation lines such as "(a) ... (b) ..." under
        # "Basic Rent means:" without requiring an LLM.
        # ------------------------------------------------------------------
        assignment_by_eid = {
            a.get("evidence_id"): a
            for a in box_assignments
            if a.get("evidence_id")
        }

        def_rows: list[dict] = []
        for eid in def_evidence_ids:
            row = eid_lookup.get(eid)
            if not row:
                continue
            assignment = assignment_by_eid.get(eid) or {}
            rb = row.get("raw_bbox") or {}
            def_rows.append({
                "evidence_id": eid,
                "text": row.get("text") or "",
                "page_number": int(row.get("page_number") or 0),
                "y": float(rb.get("y") or 0),
                "x": float(rb.get("x") or 0),
                "section_token": assignment.get("section_token") or "",
            })
        # Ensure stable reading order across rows to keep span eids ordered.
        def_rows.sort(key=lambda r: (r["page_number"], r["y"], r["x"]))
        def_rows = [r for r in def_rows if not _looks_footer_or_noise(r.get("text") or "")]

        interpretation_indices = _interpretation_row_indices(def_rows)
        all_interpretation_rules.extend(
            _build_interpretation_rules(def_rows, segment_id)
        )

        term_starts: list[dict] = []
        for idx, row in enumerate(def_rows):
            if idx in interpretation_indices:
                continue
            text = row["text"]
            if not text:
                continue
            term, source_pattern = _extract_term(text)
            if not term:
                continue
            norm_term = _normalize_term(term)
            if not norm_term:
                continue
            term_starts.append({
                "idx": idx,
                "term": term,
                "norm_term": norm_term,
                "source_pattern": source_pattern,
                "term_evidence_id": row["evidence_id"],
            })

        seen_norm_to_entry_idx: dict[str, int] = {}
        for i, start in enumerate(term_starts):
            start_idx = int(start["idx"])
            next_start_idx = (
                int(term_starts[i + 1]["idx"])
                if i + 1 < len(term_starts)
                else len(def_rows)
            )
            span_rows: list[dict] = []
            for row in def_rows[start_idx:next_start_idx]:
                if row is not def_rows[start_idx] and _looks_interpretation_heading(row.get("text") or ""):
                    break
                # Hard-stop on apparent new top-level clause headings.
                if row is not def_rows[start_idx] and _looks_top_level_clause_heading(row.get("text") or ""):
                    break
                # Defensive guard: if assignment leaves definitions tokens, stop.
                tok = row.get("section_token") or ""
                parent_tok = tok.split(".", 1)[0] if "." in tok else tok
                if row is not def_rows[start_idx] and tok not in def_tokens and parent_tok not in def_tokens:
                    break
                span_rows.append(row)

            span_eids = [r["evidence_id"] for r in span_rows if r.get("evidence_id")]
            span_pages = sorted({int(r["page_number"]) for r in span_rows if r.get("page_number")})
            if not span_eids:
                continue

            existing_key = f"{segment_id}::{start['norm_term']}"
            if existing_key in seen_norm_to_entry_idx:
                entry = all_definitions[seen_norm_to_entry_idx[existing_key]]
                for eid in span_eids:
                    if eid not in entry["definition_evidence_ids"]:
                        entry["definition_evidence_ids"].append(eid)
                merged_pages = sorted(set(entry.get("page_numbers") or []).union(span_pages))
                entry["page_numbers"] = merged_pages
                continue

            seen_norm_to_entry_idx[existing_key] = len(all_definitions)
            all_definitions.append({
                "term": start["term"],
                "normalized_term": start["norm_term"],
                "kind": "term_definition",
                "segment_id": segment_id,
                "anchor_id": None,
                "term_evidence_id": start["term_evidence_id"],
                "definition_evidence_ids": span_eids,
                "page_numbers": span_pages,
                "source_pattern": start["source_pattern"],
                "confidence": 0.90 if start["source_pattern"] in ("quoted", "markdown_bold") else 0.70,
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": doc_id,
        "definitions": all_definitions,
        "interpretation_rules": all_interpretation_rules,
    }

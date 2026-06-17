"""Detect document segments within a PDF and assign precedence.

A "segment" is one logical document inside the PDF (e.g. a deed of variation,
the original lease body, the schedules, update pages).  The segment_map
records page ranges, detection signals, and a precedence order so that
extraction can resolve conflicts between an overriding variation and the
base lease text.

Detection strategy (deterministic-first, no LLM needed for typical packs):
  1. Scan the first few evidence items on each page for document-type signals.
  2. Identify pages that open a new sub-document ("THIS DEED / THIS LEASE…").
  3. Group consecutive pages between those openings into candidate segments.
  4. Classify each candidate by its strongest signal.
  5. Emit one segment per candidate plus the precedence list.

For a single clean lease, the result is exactly one "base_lease" segment
covering the whole document.  For an amended pack with a deed of variation
preceding the original lease, two (or more) segments are emitted with the
variation ranked above the base.

Precedence ranking (lower = higher priority):
  deed_of_variation / supplemental_deed   → 0  (highest)
  update_pages / memorandum / licence /
    counterpart                            → 1
  lease_particulars / prescribed_clauses  → 2
  base_lease / underlease                 → 3
  plans                                   → 4
  schedules_only                          → 4
  contents                                → 5  (nav only, lowest)
"""

from __future__ import annotations

import re
from collections import defaultdict

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Each tuple: (compiled regex, kind, priority_label, base_confidence)
_DOC_SIGNALS: list[tuple[re.Pattern, str, str, float]] = [
    (re.compile(r"\bdeed\s+of\s+variation\b", re.I), "deed_of_variation", "highest", 0.95),
    (re.compile(r"\bsupplemental\s+(?:deed|lease|agreement)\b", re.I), "supplemental_deed", "highest", 0.90),
    (re.compile(r"\blicen[cs]e\s+to\s+(?:assign|alter|sublet|occupy)\b", re.I), "licence", "high", 0.88),
    (re.compile(r"\bmemorandum\s+of\s+(?:variation|agreement|assignment)\b", re.I), "memorandum", "high", 0.85),
    (re.compile(r"\bcounterpart\b", re.I), "counterpart", "high", 0.82),
    (re.compile(r"\blease\s+particulars\b|\bproperty\s+particulars\b", re.I), "lease_particulars", "standard", 0.85),
    (re.compile(r"\bprescribed\s+clauses\b", re.I), "prescribed_clauses", "standard", 0.90),
    (re.compile(r"\blr1\b", re.I), "prescribed_clauses", "standard", 0.80),
    (re.compile(r"\bunderlease\b|\bsub.?lease\b", re.I), "underlease", "base", 0.85),
]

# Patterns that signal the opening of a new sub-document on a page.
# Matching text must appear in the first ~3 evidence items of the page.
_NEW_DOC_RE = re.compile(
    r"\bthis\s+(deed\s+of\s+variation"
    r"|supplemental\s+(?:deed|lease|agreement)"
    r"|deed"
    r"|lease"
    r"|underlease"
    r"|agreement"
    r"|licence"
    r"|memorandum)\b",
    re.I,
)

# These keywords suggest "update pages" at the front of a pack
_UPDATE_PAGE_RE = re.compile(
    r"\b(?:update\s+page|front\s+sheet|cover\s+page)\b", re.I
)

# Relative priority ranks (lower = higher precedence)
_PRIORITY_RANK: dict[str, int] = {
    "deed_of_variation": 0,
    "supplemental_deed": 0,
    "update_pages": 1,
    "memorandum": 1,
    "licence": 1,
    "counterpart": 1,
    "lease_particulars": 2,
    "prescribed_clauses": 2,
    "base_lease": 3,
    "underlease": 3,
    "plans": 4,
    "schedules_only": 4,
    "contents": 5,
}

_PRIORITY_LABEL: dict[str, str] = {
    "deed_of_variation": "highest",
    "supplemental_deed": "highest",
    "update_pages": "high",
    "memorandum": "high",
    "licence": "high",
    "counterpart": "high",
    "lease_particulars": "standard",
    "prescribed_clauses": "standard",
    "base_lease": "base",
    "underlease": "base",
    "plans": "supplemental",
    "schedules_only": "supplemental",
    "contents": "nav",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TITLE_LABELS = {"title", "document_title", "heading", "paragraph_title", "section-header"}


def _page_head_texts(
    evidence_by_page: dict[int, list[dict]],
    page_number: int,
    n: int = 5,
    title_only: bool = False,
) -> list[str]:
    """
    Return the text of the first n evidence items on a page, sorted by y.

    When title_only=True, prefer items with title/heading labels (which are
    more reliable for boundary detection) and fall back to positional items
    only if no titled items exist on this page.
    """
    rows = sorted(
        (evidence_by_page.get(page_number) or []),
        key=lambda r: ((r.get("raw_bbox") or {}).get("y", 0), (r.get("raw_bbox") or {}).get("x", 0)),
    )
    if title_only:
        title_rows = [
            r for r in rows
            if (r.get("label") or r.get("item_type") or "").lower() in _TITLE_LABELS
        ]
        selected = (title_rows or rows)[:n]
    else:
        selected = rows[:n]
    return [r.get("text") or "" for r in selected]


def _classify_page(
    head_texts: list[str],
) -> tuple[str | None, list[str], float]:
    """
    Classify a page's opening text into a document kind.

    Returns (kind | None, signals, confidence).
    """
    combined = " ".join(head_texts).strip()
    if not combined:
        return None, [], 0.0

    signals: list[str] = []
    best_kind: str | None = None
    best_conf: float = 0.0

    for pat, kind, _prio, conf in _DOC_SIGNALS:
        if pat.search(combined):
            signals.append(pat.pattern)
            if conf > best_conf:
                best_conf = conf
                best_kind = kind

    return best_kind, signals, best_conf


def _is_new_doc_start(head_texts: list[str]) -> tuple[bool, str | None]:
    """
    Return (True, doc_type_hint) if a page's opening lines open a new
    sub-document, else (False, None).

    Only fires when the pattern appears at the VERY START of a short line —
    this prevents body text like "under this Lease" or "Lease means this
    lease…" from triggering false document boundaries.
    """
    for text in head_texts:
        clean = text.strip()
        # Ignore long body paragraphs; new-document opening lines are short.
        if len(clean) > 120:
            continue
        # re.match anchors to start of string — "This Deed of Variation…" fires;
        # "(d) …under this Lease" does NOT.
        m = _NEW_DOC_RE.match(clean)
        if m:
            return True, m.group(1).lower().strip()
        if _UPDATE_PAGE_RE.match(clean):
            return True, "update_pages"
    return False, None


# ---------------------------------------------------------------------------
# Evidence index helper
# ---------------------------------------------------------------------------


def _evidence_ids_for_page_range(
    evidence: list[dict],
    start_page: int,
    end_page: int,
) -> list[str]:
    """Return all evidence_ids whose page_number falls in [start_page, end_page]."""
    return [
        r["evidence_id"]
        for r in evidence
        if start_page <= r.get("page_number", 0) <= end_page
    ]


def _first_eid_on_page(evidence_by_page: dict[int, list[dict]], page_number: int) -> str | None:
    rows = sorted(
        evidence_by_page.get(page_number) or [],
        key=lambda r: ((r.get("raw_bbox") or {}).get("y", 0),),
    )
    return rows[0]["evidence_id"] if rows else None


def _last_eid_on_page(evidence_by_page: dict[int, list[dict]], page_number: int) -> str | None:
    rows = sorted(
        evidence_by_page.get(page_number) or [],
        key=lambda r: ((r.get("raw_bbox") or {}).get("y", 0),),
    )
    return rows[-1]["evidence_id"] if rows else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_segment_map(
    manifest: dict,
    structure_map: dict | None = None,
) -> dict:
    """
    Build segment_map.json from the evidence manifest.

    Args:
        manifest:      evidence_manifest dict.
        structure_map: optional structure_map dict.  When provided, the
                       whole-doc toc_lines are used to confirm segment
                       classification (not yet implemented beyond structure
                       hints).

    Returns:
        segment_map dict ready for JSON serialisation.
    """
    doc_id = manifest.get("doc_id", "unknown")
    evidence: list[dict] = manifest.get("evidence") or []
    pages_meta: list[dict] = manifest.get("pages") or []

    if not pages_meta:
        return {
            "schema_version": SCHEMA_VERSION,
            "doc_id": doc_id,
            "segments": [],
            "precedence": [],
            "warnings": ["No pages in manifest"],
        }

    all_page_numbers = sorted(p["page_number"] for p in pages_meta)
    max_page = all_page_numbers[-1]

    # Index evidence by page for fast lookup (items source only for text)
    evidence_by_page: dict[int, list[dict]] = defaultdict(list)
    for row in evidence:
        if row.get("source") == "items" and row.get("bbox_index", 0) == 0:
            evidence_by_page[row["page_number"]].append(row)

    # ------------------------------------------------------------------
    # Phase 1: Identify page-level document kind signals
    # ------------------------------------------------------------------
    page_signals: dict[int, tuple[str | None, list[str], float]] = {}
    page_new_doc: dict[int, tuple[bool, str | None]] = {}

    for pn in all_page_numbers:
        heads = _page_head_texts(evidence_by_page, pn)
        page_signals[pn] = _classify_page(heads)
        # For new-doc boundary detection, use title/heading items only to avoid
        # false positives from body paragraphs.
        title_heads = _page_head_texts(evidence_by_page, pn, n=3, title_only=True)
        page_new_doc[pn] = _is_new_doc_start(title_heads)

    # ------------------------------------------------------------------
    # Phase 2: Identify segment boundaries
    # Boundaries occur at pages that start a new sub-document.
    # ------------------------------------------------------------------
    boundary_pages: list[int] = []
    for pn in all_page_numbers:
        is_start, _ = page_new_doc[pn]
        if is_start or pn == all_page_numbers[0]:
            if pn not in boundary_pages:
                boundary_pages.append(pn)

    # Ensure at least page 1 is a boundary
    if not boundary_pages or boundary_pages[0] != all_page_numbers[0]:
        boundary_pages.insert(0, all_page_numbers[0])
    boundary_pages = sorted(set(boundary_pages))

    # ------------------------------------------------------------------
    # Phase 3: Build candidate segments from boundaries
    # ------------------------------------------------------------------
    candidate_segments: list[tuple[int, int]] = []
    for i, start in enumerate(boundary_pages):
        end = boundary_pages[i + 1] - 1 if i + 1 < len(boundary_pages) else max_page
        candidate_segments.append((start, end))

    # ------------------------------------------------------------------
    # Phase 4: Classify each candidate segment
    # ------------------------------------------------------------------
    segments_out: list[dict] = []
    warnings: list[str] = []

    for seg_idx, (start_page, end_page) in enumerate(candidate_segments):
        seg_id = f"seg_{seg_idx + 1}"

        # Collect signals across the segment's pages
        all_signals: list[str] = []
        all_kinds: dict[str, float] = {}

        for pn in range(start_page, min(start_page + 5, end_page + 1)):
            kind, sigs, conf = page_signals.get(pn, (None, [], 0.0))
            all_signals.extend(sigs)
            if kind and conf > all_kinds.get(kind, 0.0):
                all_kinds[kind] = conf

        # Also check new-doc hint from the boundary page
        _, doc_hint = page_new_doc.get(start_page, (False, None))

        # When the boundary opener announces "this lease"/"this deed", the
        # segment is the main lease body.  Override weaker classification
        # signals (e.g. LR / lease_particulars language carried over from
        # the previous segment's tail pages).
        _strong_override = {"deed_of_variation", "supplemental_deed", "licence", "memorandum", "counterpart"}
        _is_strong = bool(all_kinds and max(all_kinds, key=lambda k: all_kinds[k]) in _strong_override)

        if not _is_strong and doc_hint in ("lease", "deed", "agreement") and seg_idx > 0:
            kind = "base_lease"
            conf = 0.82
            method = "boundary_opener"
        elif not all_kinds:
            # No explicit signal — classify by position and page-head text
            if seg_idx == 0 and doc_hint in ("lease", "underlease", None):
                kind = "base_lease"
                conf = 0.75
                method = "position_heuristic"
            elif doc_hint == "update_pages":
                kind = "update_pages"
                conf = 0.75
                method = "title_keyword"
            elif seg_idx == 0:
                kind = "base_lease"
                conf = 0.60
                method = "fallback"
            else:
                kind = "base_lease"
                conf = 0.50
                method = "fallback"
                warnings.append(
                    f"seg_{seg_idx + 1} (pages {start_page}-{end_page}): "
                    f"no strong classification signal; defaulted to base_lease"
                )
        else:
            kind = max(all_kinds, key=lambda k: all_kinds[k])
            conf = all_kinds[kind]
            method = "regex_signal"

        priority = _PRIORITY_LABEL.get(kind, "base")
        rank = _PRIORITY_RANK.get(kind, 3)

        # Collect all evidence_ids for this segment
        seg_eids = _evidence_ids_for_page_range(evidence, start_page, end_page)
        first_eid = _first_eid_on_page(evidence_by_page, start_page)
        last_eid = _last_eid_on_page(evidence_by_page, end_page)

        # Build a human-readable title
        heads = _page_head_texts(evidence_by_page, start_page, n=2)
        title_hint = next((t for t in heads if len(t) > 3), kind.replace("_", " ").title())
        title = title_hint[:120]

        segments_out.append({
            "segment_id": seg_id,
            "kind": kind,
            "title": title,
            "page_range": [start_page, end_page],
            "start_evidence_id": first_eid,
            "end_evidence_id": last_eid,
            "evidence_ids": seg_eids,
            "priority": priority,
            "precedence_rank": rank,
            "supersedes_segment_ids": [],
            "detection": {
                "method": method,
                "signals": list(set(all_signals)),
                "confidence": round(conf, 3),
            },
        })

    # ------------------------------------------------------------------
    # Phase 5: Build supersession links and precedence order
    # ------------------------------------------------------------------
    # Variation/higher-ranked segments supersede lower-ranked ones
    # from the same doc stack (simple heuristic: a "highest" segment
    # supersedes all "base" segments in the same file).
    high_segs = [s for s in segments_out if s["precedence_rank"] <= 1]
    base_segs = [s for s in segments_out if s["precedence_rank"] >= 3]
    for hs in high_segs:
        hs["supersedes_segment_ids"] = [bs["segment_id"] for bs in base_segs]

    # Precedence list: sorted by (rank, page_range[0])
    precedence = [
        s["segment_id"]
        for s in sorted(
            segments_out,
            key=lambda s: (s["precedence_rank"], s["page_range"][0]),
        )
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": doc_id,
        "segments": segments_out,
        "precedence": precedence,
        "warnings": warnings,
    }

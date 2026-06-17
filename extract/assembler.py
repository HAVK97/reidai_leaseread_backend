"""Evidence pack assembler.

Turns a GroupConfig + ArtefactLoader into two things:
1. A source-labelled evidence pack string to send to the model.
2. A PackMeta dataclass with input_evidence_ids and source mapping for
   debugging and reproducibility.

Pack layout (matches EXTRACTION_APPROACH.md spec):
  # DEFINITIONS
  [evidence rows from definitions allowlist]

  # LEASE SUMMARY / PARTICULARS
  [evidence rows from whole_sections, e.g. LEASE_PARTICULARS]

  # OPERATIVE TEXT — current lease
  [evidence rows from semantic topics, current segment]

  # OPERATIVE TEXT — old/annexed lease (if present)
  [evidence rows from semantic topics, other segments]

Each row:
  [evidence_id] p{n} | {section_title} | {text}
"""

from __future__ import annotations

import re
from html import unescape
from dataclasses import dataclass, field
from typing import NamedTuple

from .artefacts import ArtefactLoader
from .groups import GroupConfig, SectionRef


# ---------------------------------------------------------------------------
# Row and pack shapes
# ---------------------------------------------------------------------------


class PackRow(NamedTuple):
    evidence_id: str
    page_number: int
    section_token: str | None
    section_title: str | None
    segment_id: str | None
    segment_kind: str | None
    source_block: str  # definitions | lease_summary | operative_current | operative_other
    text: str


@dataclass
class PackMeta:
    """Machine-readable metadata for a built evidence pack."""

    doc_id: str
    group_id: str
    input_evidence_ids: list[str] = field(default_factory=list)
    # Maps evidence_id -> source_block label
    source_map: dict[str, str] = field(default_factory=dict)
    # Per-block counts for debugging
    block_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"\|\s*:?-{2,}:?\s*(?=\|)")
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PARTY_LABELS = {
    "landlord",
    "lessor",
    "tenant",
    "lessee",
    "guarantor",
    "surety",
}
_PARTY_CONTEXT_LABELS = {"other parties", "parties"}


def render_pack_row_text(row: PackRow) -> str:
    """Return model-facing text for a row.

    The manifest text stays authoritative for evidence output, but model input
    benefits from flattening table markup into quoteable, readable snippets.
    """

    text = _normalize_ws(row.text)
    if not text:
        return text

    party_lines = _flatten_party_table_text(text)
    if party_lines:
        return " ".join(party_lines)

    flattened = _flatten_table_text(text)
    return flattened or text


def render_pack_row_section_title(row: PackRow) -> str:
    """Return model-facing section title for a row."""

    sec = row.section_title or row.section_token or ""
    if _flatten_party_table_text(row.text):
        return "Parties to this Lease"
    return sec


def _flatten_table_text(text: str) -> str:
    """Make markdown/html table text easier to read without extracting facts."""

    if "|" not in text and "<table" not in text.lower() and "<br" not in text.lower():
        return text

    cleaned = _clean_table_markup(text)
    cleaned = _MARKDOWN_TABLE_SEPARATOR_RE.sub("|", cleaned)
    cleaned = re.sub(r"\|\s*-{2,}(?:\s*-+)*\s*\|", " | ", cleaned)
    cleaned = re.sub(r"\|\s*\|", ". ", cleaned)
    cleaned = re.sub(r"\s*\|\s*", " | ", cleaned)
    cleaned = re.sub(r"(?:\|\s*){2,}", "| ", cleaned)
    return _normalize_ws(cleaned).strip(" |")


def _flatten_party_table_text(text: str) -> list[str]:
    """Flatten party rows inside prescribed-clause/party tables.

    This is rendering-only: it does not decide the extracted party values.
    """

    if "parties to this lease" not in text.lower():
        return []

    cleaned = _clean_table_markup(text)
    body_match = re.search(
        r"parties\s+to\s+this\s+lease\s*\|?\s*(.*?)(?:\|\s*\|\s*LR\d+[\.\s]|\|\s*LR\d+[\.\s]|LR\d+[\.\s]|$)",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not body_match:
        return []

    body = body_match.group(1)
    body = body.replace("|", "\n")
    parts = [_normalize_ws(part).strip(" .") for part in body.splitlines()]
    parts = [part for part in parts if part and not _is_table_separator(part)]

    lines: list[str] = []
    context: str | None = None
    current_label: str | None = None
    current_value: list[str] = []

    def flush() -> None:
        nonlocal current_label, current_value
        if not current_label:
            return
        value = _normalize_ws(" ".join(current_value)).strip(" .")
        if not value:
            return
        label = current_label.title()
        if context:
            label = f"{context.title()} - {label}"
        lines.append(f"Parties to this Lease - {label}: {value}")
        current_label = None
        current_value = []

    for part in parts:
        lowered = part.lower().strip(":")
        if lowered in _PARTY_CONTEXT_LABELS:
            flush()
            context = lowered
            continue
        if lowered in _PARTY_LABELS:
            flush()
            current_label = lowered
            current_value = []
            continue
        if current_label:
            current_value.append(part)

    flush()
    if context == "other parties" and not any("Other Parties" in line for line in lines):
        lines.append("Parties to this Lease - Other Parties")
    return lines


def _clean_table_markup(text: str) -> str:
    text = unescape(text)
    text = _HTML_BR_RE.sub("\n", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = _HTML_TAG_RE.sub(" ", text)
    return text


def _is_table_separator(text: str) -> bool:
    chars = set(text.replace(" ", ""))
    return bool(chars) and chars <= {"-", ":", "|"}


class EvidencePackAssembler:
    """Assembles a source-labelled evidence pack from group config + artefacts."""

    # Evidence IDs that represent images (no text), skip them in packs
    _IMAGE_ID_RE = re.compile(r"layout_image")

    def __init__(self, loader: ArtefactLoader) -> None:
        self.loader = loader

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, group: GroupConfig) -> tuple[str, PackMeta]:
        """Build the evidence pack string + metadata for a group.

        Returns:
            (pack_text, meta)
        """
        meta = PackMeta(
            doc_id=self.loader.doc_id,
            group_id=group.group_id,
        )

        rows: list[PackRow] = []
        seen_ids: set[str] = set()

        # Resolve current segment upfront — needed by all collection methods
        # so that definitions and whole sections can be labelled current vs old.
        current_seg_id = self.loader.get_current_segment_id()

        # 1. Definitions block
        def_rows = self._collect_definitions(group, seen_ids, current_seg_id)
        rows.extend(def_rows)

        # 2. Whole sections (e.g. LEASE_PARTICULARS)
        section_rows = self._collect_whole_sections(group, seen_ids, current_seg_id)
        rows.extend(section_rows)

        # 3. Semantic topic rows — current segment first, others after
        topic_rows_current, topic_rows_other = self._collect_topic_rows(
            group, seen_ids, current_seg_id
        )
        rows.extend(topic_rows_current)
        rows.extend(topic_rows_other)

        # Build meta
        for row in rows:
            if row.evidence_id not in meta.source_map:
                meta.input_evidence_ids.append(row.evidence_id)
                meta.source_map[row.evidence_id] = row.source_block

        meta.block_counts = _count_blocks(rows)

        if not rows:
            meta.warnings.append(
                f"No evidence assembled for group '{group.group_id}'. "
                "Check semantic topic names and whole_sections in the group config."
            )

        pack_text = _render_pack(rows)
        return pack_text, meta

    # ------------------------------------------------------------------
    # Collection helpers
    # ------------------------------------------------------------------

    def _collect_definitions(
        self,
        group: GroupConfig,
        seen_ids: set[str],
        current_seg_id: str | None = None,
    ) -> list[PackRow]:
        if not group.definition_allowlist:
            return []

        di = self.loader.definitions_index
        if not di:
            return []

        manifest_index = self.loader.build_evidence_index()
        rows: list[PackRow] = []

        defns = self.loader.get_definitions_for_terms(group.definition_allowlist)
        for defn in defns:
            seg_id = defn.get("segment_id")
            seg_kind = self.loader.get_segment_kind(seg_id) if seg_id else None

            if current_seg_id and seg_id:
                source_block = (
                    "definitions_current"
                    if seg_id == current_seg_id
                    else "definitions_old"
                )
            else:
                source_block = "definitions"

            for eid in defn.get("definition_evidence_ids", []):
                if eid in seen_ids or self._is_image(eid):
                    continue
                ev = manifest_index.get(eid)
                if not ev:
                    continue
                text = _normalize_ws(ev.get("text", ""))
                if not text:
                    continue
                seen_ids.add(eid)
                rows.append(
                    PackRow(
                        evidence_id=eid,
                        page_number=ev.get("page_number", 0),
                        section_token="DEFINITIONS",
                        section_title=f"Definition: {defn.get('term', '')}",
                        segment_id=seg_id,
                        segment_kind=seg_kind,
                        source_block=source_block,
                        text=text,
                    )
                )
        return rows

    def _collect_whole_sections(
        self,
        group: GroupConfig,
        seen_ids: set[str],
        current_seg_id: str | None = None,
    ) -> list[PackRow]:
        if not group.whole_sections:
            return []

        manifest_index = self.loader.build_evidence_index()
        box_index = self.loader.build_structure_box_index()
        rows: list[PackRow] = []

        for sec_ref in group.whole_sections:
            eids = self.loader.get_section_evidence_ids(sec_ref.family, sec_ref.token)
            for eid in eids:
                if eid in seen_ids or self._is_image(eid):
                    continue
                ev = manifest_index.get(eid)
                if not ev:
                    continue
                text = _normalize_ws(ev.get("text", ""))
                if not text:
                    continue
                ba = box_index.get(eid, {})
                seg_id = ba.get("segment_id")
                seg_kind = ba.get("segment_kind")

                if current_seg_id and seg_id:
                    source_block = (
                        "lease_summary_current"
                        if seg_id == current_seg_id
                        else "lease_summary_old"
                    )
                else:
                    source_block = "lease_summary"

                seen_ids.add(eid)
                rows.append(
                    PackRow(
                        evidence_id=eid,
                        page_number=ev.get("page_number", 0),
                        section_token=sec_ref.token,
                        section_title=ba.get("section_title") or sec_ref.token,
                        segment_id=seg_id,
                        segment_kind=seg_kind,
                        source_block=source_block,
                        text=text,
                    )
                )
        return rows

    def _collect_topic_rows(
        self,
        group: GroupConfig,
        seen_ids: set[str],
        current_seg_id: str | None,
    ) -> tuple[list[PackRow], list[PackRow]]:
        """Returns (current_segment_rows, other_segment_rows)."""
        if not group.semantic_topics:
            return [], []

        manifest_index = self.loader.build_evidence_index()
        box_index = self.loader.build_structure_box_index()

        # Collect all topic evidence across segments
        all_topic_eids: dict[str, str] = {}  # eid -> segment_id
        for topic in group.semantic_topics:
            for seg_id, eids in self.loader.get_all_topic_evidence_ids(topic).items():
                for eid in eids:
                    if eid not in all_topic_eids:
                        all_topic_eids[eid] = seg_id

        current_rows: list[PackRow] = []
        other_rows: list[PackRow] = []

        for eid, seg_id in all_topic_eids.items():
            if eid in seen_ids or self._is_image(eid):
                continue
            ev = manifest_index.get(eid)
            if not ev:
                continue
            text = _normalize_ws(ev.get("text", ""))
            if not text:
                continue

            ba = box_index.get(eid, {})
            seg_kind = ba.get("segment_kind") or self.loader.get_segment_kind(seg_id) or ""
            is_current = seg_id == current_seg_id
            source_block = "operative_current" if is_current else "operative_other"

            seen_ids.add(eid)
            row = PackRow(
                evidence_id=eid,
                page_number=ev.get("page_number", 0),
                section_token=ba.get("section_token"),
                section_title=ba.get("section_title"),
                segment_id=seg_id,
                segment_kind=seg_kind,
                source_block=source_block,
                text=text,
            )
            if is_current:
                current_rows.append(row)
            else:
                other_rows.append(row)

        # Sort each list by page number (document reading order)
        current_rows.sort(key=lambda r: r.page_number)
        other_rows.sort(key=lambda r: r.page_number)

        return current_rows, other_rows

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_image(self, evidence_id: str) -> bool:
        return bool(self._IMAGE_ID_RE.search(evidence_id))


# ---------------------------------------------------------------------------
# Segment display labels (UK conveyancing wording for packs and prompts)
# ---------------------------------------------------------------------------

_SEGMENT_KIND_LABELS: dict[str, str] = {
    "current_instrument": "Lease",
    "annexed_old_lease": "Annexed lease",
    "base_lease": "Lease",
    "deed_of_variation": "Deed of variation",
    "supplemental_deed": "Supplemental deed",
    "memorandum": "Memorandum",
    "licence": "Licence",
    "underlease": "Underlease",
    "lease_particulars": "Lease particulars",
    "prescribed_clauses": "Prescribed clauses",
}


def segment_kind_label(segment_kind: str | None) -> str | None:
    """Human-readable segment label for evidence pack rows and review messages."""
    if not segment_kind:
        return None
    return _SEGMENT_KIND_LABELS.get(
        segment_kind, segment_kind.replace("_", " ").title()
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _count_blocks(rows: list[PackRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.source_block] = counts.get(row.source_block, 0) + 1
    return counts


_BLOCK_HEADERS = {
    # Single-segment (no old/new split): generic headers
    "definitions": "DEFINITIONS",
    "lease_summary": "LEASE SUMMARY / PARTICULARS",
    # Multi-segment: binding lease (front document)
    "definitions_current": "LEASE — DEFINITIONS",
    "lease_summary_current": "LEASE — SUMMARY / PARTICULARS",
    # Multi-segment: annexed lease
    "definitions_old": "ANNEXED LEASE — DEFINITIONS",
    "lease_summary_old": "ANNEXED LEASE — SUMMARY / PARTICULARS",
    # Operative text (segment-split by topic collector)
    "operative_current": "OPERATIVE TEXT — lease",
    "operative_other": "OPERATIVE TEXT — annexed lease",
}

_BLOCK_ORDER = [
    # Single-segment fallbacks
    "definitions",
    "lease_summary",
    # Multi-segment: lease first (highest priority)
    "definitions_current",
    "lease_summary_current",
    "operative_current",
    # Multi-segment: annexed lease
    "definitions_old",
    "lease_summary_old",
    "operative_other",
]


def _render_pack(rows: list[PackRow]) -> str:
    """Render rows into the source-labelled evidence pack string."""
    by_block: dict[str, list[PackRow]] = {b: [] for b in _BLOCK_ORDER}
    for row in rows:
        target = row.source_block if row.source_block in by_block else "operative_other"
        by_block[target].append(row)

    parts: list[str] = []
    for block_key in _BLOCK_ORDER:
        block_rows = by_block[block_key]
        if not block_rows:
            continue
        header = _BLOCK_HEADERS[block_key]
        parts.append(f"# {header}")
        for row in block_rows:
            sec = render_pack_row_section_title(row)
            line = f"[{row.evidence_id}] p{row.page_number}"
            seg_label = segment_kind_label(row.segment_kind)
            if seg_label:
                line += f" | {seg_label}"
            if sec:
                line += f" | {sec}"
            line += f" | {render_pack_row_text(row)}"
            parts.append(line)
        parts.append("")  # blank line between blocks

    return "\n".join(parts).strip()

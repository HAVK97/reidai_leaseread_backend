"""Quote resolver — maps model-returned quote strings to manifest evidence rows.

The model never returns evidence_id values (they get miscoped). Instead it
returns short verbatim quotes (8-25 words). This module matches those quotes
against the assembled evidence pack rows using normalised whitespace comparison
with an optional fuzzy threshold.

For each field:
  1. Normalise each quote.
  2. Search every row in the pack for a row whose normalised text CONTAINS
     the normalised quote.
  3. Collect all matching evidence_ids and fetch text_verbatim from the manifest.
  4. Flag:
     - no_match: no row matched → needs_review = True
     - ambiguous: quote matched more than one distinct section (low risk if
       same section, higher risk if different sections/pages)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .artefacts import ArtefactLoader
from .assembler import PackRow

MAX_SUPPORTING_EVIDENCE = 5
_MIN_SPECIFIC_QUOTE_CHARS = 12
_NON_SPECIFIC_QUOTES = {
    "landlord",
    "tenant",
    "lessor",
    "lessee",
    "guarantor",
    "surety",
    "other parties",
}


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass
class SupportingEvidence:
    evidence_id: str
    text_verbatim: str  # always from manifest, never from model
    page_number: int
    section_title: str | None


@dataclass
class FieldResult:
    """Post-processed output for a single extracted field."""

    field_id: str
    value: str
    status: str  # found | not_present | unclear
    quotes: list[str]
    reasoning: str
    confidence: str  # high | medium | low
    supporting_evidence: list[SupportingEvidence] = field(default_factory=list)
    needs_review: bool = False
    review_reason: str | None = None
    # Which segment kind the extracted value was drawn from (derived deterministically
    # from the resolved supporting_evidence rows, not from the model).
    applied_segment_kind: str | None = None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Collapse whitespace, lowercase, strip."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _is_non_specific_quote(norm_quote: str) -> bool:
    return (
        not norm_quote
        or len(norm_quote) < _MIN_SPECIFIC_QUOTE_CHARS
        or norm_quote in _NON_SPECIFIC_QUOTES
    )


class QuoteResolver:
    """Match model quotes against pack rows and enrich with manifest text."""

    def __init__(
        self,
        loader: ArtefactLoader,
        pack_rows: list[PackRow],
        fuzzy_threshold: float = 0.90,
        current_segment_id: str | None = None,
    ) -> None:
        self.loader = loader
        self.pack_rows = pack_rows
        self.fuzzy_threshold = fuzzy_threshold
        self.current_segment_id = current_segment_id
        self._manifest_index = loader.build_evidence_index()
        # Pre-normalise all row texts for fast search
        self._norm_texts: list[str] = [_normalise(r.text) for r in pack_rows]
        # Fast lookup: evidence_id → PackRow (for segment checks after resolution)
        self._pack_row_index: dict[str, PackRow] = {r.evidence_id: r for r in pack_rows}

    def resolve_field(self, field_id: str, raw: dict) -> FieldResult:
        """Build a FieldResult from the model's raw field dict."""
        value = raw.get("value", "")
        status = raw.get("status", "unclear")
        quotes: list[str] = raw.get("quotes", []) or []
        reasoning = raw.get("reasoning", "")
        confidence = raw.get("confidence", "low")

        supporting: list[SupportingEvidence] = []
        unresolved_quotes: list[str] = []
        ambiguous_quotes: list[str] = []
        ignored_quotes: list[str] = []

        for quote in quotes:
            norm_quote = _normalise(quote)
            if _is_non_specific_quote(norm_quote):
                ignored_quotes.append(quote)
                continue

            matches = self._find_matches(quote)
            if not matches:
                unresolved_quotes.append(quote)
            else:
                if len({r.evidence_id for r in matches}) > 1:
                    # Multiple rows matched — could be the same section across pages
                    # Check if they span more than one section_token
                    tokens = {r.section_token for r in matches}
                    if len(tokens) > 1:
                        ambiguous_quotes.append(quote)
                for row in matches:
                    ev = self._manifest_index.get(row.evidence_id)
                    if not ev:
                        continue
                    already = {s.evidence_id for s in supporting}
                    if row.evidence_id not in already:
                        supporting.append(
                            SupportingEvidence(
                                evidence_id=row.evidence_id,
                                text_verbatim=ev.get("text", ""),
                                page_number=ev.get("page_number", 0),
                                section_title=row.section_title,
                            )
                        )

        if len(supporting) > MAX_SUPPORTING_EVIDENCE:
            supporting = supporting[:MAX_SUPPORTING_EVIDENCE]

        # Determine needs_review
        needs_review = False
        review_reason = None

        if status == "found" and not supporting:
            if ignored_quotes and not unresolved_quotes:
                review_reason = f"Only non-specific quotes ignored: {ignored_quotes[:3]}"
            else:
                needs_review = True
                review_reason = "status=found but no quotes resolved to evidence rows"
                if confidence == "high":
                    confidence = "medium"
        elif unresolved_quotes and not supporting:
            needs_review = True
            review_reason = f"Unresolved quotes: {unresolved_quotes[:3]}"
            if confidence == "high":
                confidence = "medium"
        elif ambiguous_quotes:
            review_reason = f"Ambiguous quotes matched multiple sections: {ambiguous_quotes[:2]}"

        # Derive applied_segment_kind deterministically from resolved evidence rows.
        # This is always code-derived — never trusted from the model.
        applied_segment_kind: str | None = None
        if supporting:
            seg_kinds = [
                self._pack_row_index[se.evidence_id].segment_kind
                for se in supporting
                if se.evidence_id in self._pack_row_index
                and self._pack_row_index[se.evidence_id].segment_kind
            ]
            if seg_kinds:
                unique = set(seg_kinds)
                applied_segment_kind = seg_kinds[0] if len(unique) == 1 else "mixed"

        return FieldResult(
            field_id=field_id,
            value=value,
            status=status,
            quotes=quotes,
            reasoning=reasoning,
            confidence=confidence,
            supporting_evidence=supporting,
            needs_review=needs_review,
            review_reason=review_reason,
            applied_segment_kind=applied_segment_kind,
        )

    def _find_matches(self, quote: str) -> list[PackRow]:
        """Find pack rows whose text contains the normalised quote."""
        norm_q = _normalise(quote)
        if not norm_q or len(norm_q) < 5:
            return []

        exact: list[PackRow] = []
        for row, norm_text in zip(self.pack_rows, self._norm_texts):
            if norm_q in norm_text:
                exact.append(row)

        if exact:
            return exact

        adjacent = self._adjacent_span_matches(norm_q)
        if adjacent:
            return adjacent

        # Fuzzy fallback using token overlap ratio
        if self.fuzzy_threshold < 1.0:
            return self._fuzzy_matches(norm_q)

        return []

    def _adjacent_span_matches(self, norm_q: str) -> list[PackRow]:
        """Match quotes that span adjacent OCR/layout evidence rows."""
        for span_size in (2, 3):
            for start in range(0, len(self._norm_texts) - span_size + 1):
                span_text = " ".join(self._norm_texts[start : start + span_size])
                if norm_q in span_text:
                    return self.pack_rows[start : start + span_size]
        q_tokens = set(norm_q.split())
        if not q_tokens:
            return []
        for span_size in range(2, 7):
            for start in range(0, len(self._norm_texts) - span_size + 1):
                span_texts = self._norm_texts[start : start + span_size]
                span_tokens = set(" ".join(span_texts).split())
                overlap = len(q_tokens & span_tokens) / len(q_tokens)
                if overlap < self.fuzzy_threshold:
                    continue
                contributing_rows = [
                    self.pack_rows[start + offset]
                    for offset, text in enumerate(span_texts)
                    if q_tokens & set(text.split())
                ]
                if len(contributing_rows) > 1:
                    return contributing_rows
        return []

    def _fuzzy_matches(self, norm_q: str) -> list[PackRow]:
        """Simple token-overlap fuzzy match as fallback."""
        q_tokens = set(norm_q.split())
        if not q_tokens:
            return []
        matches: list[PackRow] = []
        for row, norm_text in zip(self.pack_rows, self._norm_texts):
            row_tokens = set(norm_text.split())
            if not row_tokens:
                continue
            overlap = len(q_tokens & row_tokens) / len(q_tokens)
            if overlap >= self.fuzzy_threshold:
                matches.append(row)
        return matches


def resolve_all_fields(
    model_response: dict,
    resolver: QuoteResolver,
    group_field_ids: list[str],
) -> dict[str, FieldResult]:
    """Resolve every field from a model response dict.

    Args:
        model_response: raw dict from provider (keyed by field_id)
        resolver:       QuoteResolver built from the assembled pack
        group_field_ids: ordered list of expected field ids

    Returns:
        dict[field_id -> FieldResult]
    """
    results: dict[str, FieldResult] = {}
    for field_id in group_field_ids:
        raw = model_response.get(field_id, {})
        if not isinstance(raw, dict):
            raw = {"value": str(raw), "status": "unclear", "quotes": [], "reasoning": "", "confidence": "low"}
        results[field_id] = resolver.resolve_field(field_id, raw)
    return results

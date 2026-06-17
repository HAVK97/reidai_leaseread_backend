"""Load and index all preextract artefacts for a single document.

Exposes a single ArtefactLoader that accepts a preextract output directory
and provides fast lookups required by the assembler and resolver.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator


class ArtefactLoader:
    """Load and cache artefacts for one doc_id output directory."""

    REQUIRED = ("evidence_manifest.json",)
    OPTIONAL = (
        "segment_map.json",
        "structure_map.json",
        "definitions_index.json",
        "semantic_section_map.json",
    )

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._cache: dict[str, dict] = {}
        self._evidence_index: dict[str, dict] | None = None
        self._structure_box_index: dict[str, dict] | None = None  # evidence_id -> box_assignment

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, name: str, required: bool = False) -> dict | None:
        if name not in self._cache:
            path = self.out_dir / name
            if not path.exists():
                if required:
                    raise FileNotFoundError(
                        f"Required artefact not found: {path}"
                    )
                return None
            with path.open("r", encoding="utf-8") as f:
                self._cache[name] = json.load(f)
        return self._cache[name]

    @property
    def manifest(self) -> dict:
        return self._load("evidence_manifest.json", required=True)  # type: ignore[return-value]

    @property
    def segment_map(self) -> dict | None:
        return self._load("segment_map.json")

    @property
    def structure_map(self) -> dict | None:
        return self._load("structure_map.json")

    @property
    def definitions_index(self) -> dict | None:
        return self._load("definitions_index.json")

    @property
    def semantic_section_map(self) -> dict | None:
        return self._load("semantic_section_map.json")

    @property
    def doc_id(self) -> str:
        return self.manifest.get("doc_id", self.out_dir.name)

    # ------------------------------------------------------------------
    # Evidence index
    # ------------------------------------------------------------------

    def build_evidence_index(self) -> dict[str, dict]:
        """evidence_id -> full evidence row dict."""
        if self._evidence_index is None:
            self._evidence_index = {
                row["evidence_id"]: row
                for row in self.manifest.get("evidence", [])
                if row.get("evidence_id")
            }
        return self._evidence_index

    def get_evidence_row(self, evidence_id: str) -> dict | None:
        return self.build_evidence_index().get(evidence_id)

    def iter_evidence(self) -> Iterator[dict]:
        yield from self.manifest.get("evidence", [])

    # ------------------------------------------------------------------
    # Structure box assignment index
    # ------------------------------------------------------------------

    def build_structure_box_index(self) -> dict[str, dict]:
        """evidence_id -> box_assignment row (for section token/title context)."""
        if self._structure_box_index is None:
            idx: dict[str, dict] = {}
            sm = self.structure_map
            if sm:
                for seg in sm.get("segments", []):
                    for ba in seg.get("box_assignments", []):
                        eid = ba.get("evidence_id")
                        if eid:
                            idx[eid] = ba
            self._structure_box_index = idx
        return self._structure_box_index

    def get_box_assignment(self, evidence_id: str) -> dict | None:
        return self.build_structure_box_index().get(evidence_id)

    # ------------------------------------------------------------------
    # Semantic section map helpers
    # ------------------------------------------------------------------

    def get_topic_evidence_ids(
        self, topic: str, segment_id: str | None = None
    ) -> list[str]:
        """Return evidence_ids for a semantic topic, optionally filtered by segment."""
        ssm = self.semantic_section_map
        if not ssm:
            return []
        results: list[str] = []
        for seg in ssm.get("segments", []):
            if segment_id and seg.get("segment_id") != segment_id:
                continue
            topic_data = seg.get("topics", {}).get(topic)
            if topic_data:
                results.extend(topic_data.get("evidence_ids", []))
        return results

    def get_all_topic_evidence_ids(
        self, topic: str
    ) -> dict[str, list[str]]:
        """Returns {segment_id: [evidence_ids]} for a topic across all segments."""
        ssm = self.semantic_section_map
        if not ssm:
            return {}
        result: dict[str, list[str]] = {}
        for seg in ssm.get("segments", []):
            sid = seg.get("segment_id", "")
            topic_data = seg.get("topics", {}).get(topic)
            if topic_data and topic_data.get("evidence_ids"):
                result[sid] = topic_data["evidence_ids"]
        return result

    # ------------------------------------------------------------------
    # Structure map helpers
    # ------------------------------------------------------------------

    def get_section_evidence_ids(
        self, family: str, token: str, segment_id: str | None = None
    ) -> list[str]:
        """Return evidence_ids for a whole section and its descendants.

        Whole-section refs such as ``front_matter:PRESCRIBED_CLAUSES`` are
        intended to include child anchors like LR1/LR2/LR3, not just rows
        assigned directly to the parent section.
        """
        sm = self.structure_map
        if not sm:
            return []
        results: list[str] = []
        seen: set[str] = set()
        for seg in sm.get("segments", []):
            if segment_id and seg.get("segment_id") != segment_id:
                continue

            anchors = {
                int(a["anchor_id"]): a
                for a in seg.get("anchors_flat", [])
                if a.get("anchor_id") is not None
            }
            matching_anchor_ids = {
                aid
                for aid, anchor in anchors.items()
                if anchor.get("family") == family and anchor.get("token") == token
            }
            section_anchor_ids: set[int] = set()

            def add_descendants(anchor_id: int) -> None:
                if anchor_id in section_anchor_ids:
                    return
                section_anchor_ids.add(anchor_id)
                for child_id in (anchors.get(anchor_id) or {}).get("children_ids") or []:
                    try:
                        add_descendants(int(child_id))
                    except (TypeError, ValueError):
                        continue

            for aid in matching_anchor_ids:
                add_descendants(aid)

            for ba in seg.get("box_assignments", []):
                anchor_id = ba.get("anchor_id")
                is_descendant = False
                try:
                    is_descendant = int(anchor_id) in section_anchor_ids
                except (TypeError, ValueError):
                    pass

                is_exact = (
                    ba.get("section_family") == family
                    and ba.get("section_token") == token
                )
                if not (is_descendant or is_exact):
                    continue

                eid = ba.get("evidence_id")
                if eid and eid not in seen:
                    results.append(eid)
                    seen.add(eid)
        return results

    # ------------------------------------------------------------------
    # Definitions helpers
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_definition_term(term: str) -> str:
        """Normalize a defined-term label for allowlist matching."""
        normalized = re.sub(r"\s+", " ", term.lower().strip())
        if normalized.startswith("the "):
            normalized = normalized[4:]
        return normalized

    def get_definitions_for_terms(
        self, term_allowlist: list[str]
    ) -> list[dict]:
        """Return definition rows matching normalized allowlist terms."""
        di = self.definitions_index
        if not di:
            return []
        normalized_allowlist = {
            self.normalize_definition_term(t) for t in term_allowlist
        }
        return [
            defn
            for defn in di.get("definitions", [])
            if self.normalize_definition_term(defn.get("normalized_term", ""))
            in normalized_allowlist
        ]

    # ------------------------------------------------------------------
    # Segment map helpers
    # ------------------------------------------------------------------

    def get_current_segment_id(self) -> str | None:
        """Return the highest-priority (current/base) segment_id."""
        sm = self.segment_map
        if not sm:
            return None
        precedence = sm.get("precedence", [])
        if precedence:
            return precedence[0]
        segments = sm.get("segments", [])
        if segments:
            return segments[0].get("segment_id")
        return None

    def get_segment_kind(self, segment_id: str) -> str | None:
        sm = self.segment_map
        if not sm:
            return None
        for seg in sm.get("segments", []):
            if seg.get("segment_id") == segment_id:
                return seg.get("kind")
        return None

    @classmethod
    def from_dir(cls, path: str | Path) -> "ArtefactLoader":
        return cls(Path(path))

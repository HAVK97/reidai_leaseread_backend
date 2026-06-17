"""preextract — pre-extraction pipeline for ReidAI lease intelligence.

Pipeline stages:
  parse → evidence_manifest → segment_map → structure_map
        → definitions_index → semantic_section_map → validation_report

Usage (CLI):
  python -m preextract build --raw llamaparse_raw_result.json --out preextract_outputs/
  python -m preextract validate --dir preextract_outputs/<doc_id>/
  python -m preextract overlay --manifest preextract_outputs/<doc_id>/evidence_manifest.json --pdf file.pdf
"""

__version__ = "0.1.0"

from .manifest import build_manifest
from .structure_v2 import build_segment_map_v2, build_structure_map_v2
from .definitions import build_definitions_index
from .sections import build_section_map
from .validation import validate_manifest, validate_all

__all__ = [
    "build_manifest",
    "build_segment_map_v2",
    "build_structure_map_v2",
    "build_definitions_index",
    "build_section_map",
    "validate_manifest",
    "validate_all",
]

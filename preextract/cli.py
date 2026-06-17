"""CLI entry-point for the preextract package.

Commands:
  build     Run all pre-extraction stages from a LlamaParse raw JSON.
  validate  Re-validate an existing output directory.
  overlay   Render bbox overlay PNGs from an evidence manifest.

Usage:
  python -m preextract build --raw llamaparse_raw_result.json --out preextract_outputs/ [--doc-id my_lease]
  python -m preextract validate --dir preextract_outputs/my_lease/
  python -m preextract overlay --manifest preextract_outputs/my_lease/evidence_manifest.json --pdf file.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"  wrote {path}")


def _print_stage(result: dict) -> None:
    status = "PASS" if result.get("passed") else "FAIL"
    stage = result.get("stage", "?")
    errs = result.get("errors") or []
    warns = result.get("warnings") or []
    counts = result.get("counts") or {}
    print(f"  [{status}] {stage}  counts={counts}")
    for e in errs:
        print(f"        ERROR: {e}")
    for w in warns[:5]:
        print(f"        WARN:  {w}")
    if len(warns) > 5:
        print(f"        ... and {len(warns) - 5} more warnings")


# ---------------------------------------------------------------------------
# build command
# ---------------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> int:
    from preextract.manifest import build_manifest
    from preextract.structure_v2 import build_segment_map_v2, build_structure_map_v2
    from preextract.definitions import build_definitions_index
    from preextract.sections import build_section_map
    from preextract.validation import validate_all

    raw_path = Path(args.raw)
    if not raw_path.is_file():
        print(f"ERROR: raw JSON not found: {raw_path}", file=sys.stderr)
        return 1

    print(f"Loading parse output: {raw_path}")
    raw = _load_json(raw_path)

    doc_id: str = args.doc_id or raw.get("job", {}).get("id") or raw_path.stem
    out_dir = Path(args.out) / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\ndoc_id: {doc_id}")
    print(f"output: {out_dir}\n")

    # ------------------------------------------------------------------
    # Stage 1: save raw parse (copy if needed)
    # ------------------------------------------------------------------
    parse_out = out_dir / "parse.json"
    if not parse_out.exists():
        _save_json(raw, parse_out)

    # ------------------------------------------------------------------
    # Stage 2: evidence manifest
    # ------------------------------------------------------------------
    print("Stage 1: evidence manifest …")
    manifest = build_manifest(raw, doc_id=doc_id, source_file=str(raw_path))
    _save_json(manifest, out_dir / "evidence_manifest.json")

    # ------------------------------------------------------------------
    # Stage 3: segment map
    # ------------------------------------------------------------------
    print("Stage 2: segment map (structure_v2 detector) …")
    segment_map = build_segment_map_v2(raw, manifest)
    _save_json(segment_map, out_dir / "segment_map.json")

    # ------------------------------------------------------------------
    # Stage 4: structure map (lease-specific structure_v2 detector)
    # ------------------------------------------------------------------
    print("Stage 3: structure map (structure_v2 detector) …")
    structure_map = build_structure_map_v2(raw, manifest, segment_map)
    _save_json(structure_map, out_dir / "structure_map.json")

    # ------------------------------------------------------------------
    # Stage 5: definitions index
    # ------------------------------------------------------------------
    print("Stage 4: definitions index …")
    definitions_index = build_definitions_index(manifest, structure_map)
    _save_json(definitions_index, out_dir / "definitions_index.json")

    # ------------------------------------------------------------------
    # Stage 6: semantic section map
    # ------------------------------------------------------------------
    print("Stage 5: semantic section map …")
    section_map = build_section_map(
        manifest, structure_map, segment_map, definitions_index
    )
    _save_json(section_map, out_dir / "semantic_section_map.json")

    # ------------------------------------------------------------------
    # Stage 7: validation report
    # ------------------------------------------------------------------
    print("\nValidating all artefacts …")
    report = validate_all(
        manifest=manifest,
        segment_map=segment_map,
        structure_map=structure_map,
        definitions_index=definitions_index,
        section_map=section_map,
    )
    _save_json(report, out_dir / "validation_report.json")

    print()
    for stage_result in report.get("stage_results") or []:
        _print_stage(stage_result)

    overall = "PASS" if report.get("passed") else "FAIL"
    print(f"\nOverall: {overall}")
    return 0 if report.get("passed") else 2


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    from preextract.validation import validate_all

    out_dir = Path(args.dir)
    if not out_dir.is_dir():
        print(f"ERROR: directory not found: {out_dir}", file=sys.stderr)
        return 1

    def _maybe(name: str):
        p = out_dir / name
        return _load_json(p) if p.exists() else None

    manifest = _maybe("evidence_manifest.json")
    if manifest is None:
        print("ERROR: evidence_manifest.json not found in directory", file=sys.stderr)
        return 1

    report = validate_all(
        manifest=manifest,
        segment_map=_maybe("segment_map.json"),
        structure_map=_maybe("structure_map.json"),
        definitions_index=_maybe("definitions_index.json"),
        section_map=_maybe("semantic_section_map.json"),
    )

    _save_json(report, out_dir / "validation_report.json")

    for stage_result in report.get("stage_results") or []:
        _print_stage(stage_result)

    overall = "PASS" if report.get("passed") else "FAIL"
    print(f"\nOverall: {overall}")
    return 0 if report.get("passed") else 2


# ---------------------------------------------------------------------------
# overlay command
# ---------------------------------------------------------------------------


def cmd_overlay(args: argparse.Namespace) -> int:
    from preextract.overlays import render_overlays, spot_check_pages

    manifest_path = Path(args.manifest)
    pdf_path = Path(args.pdf)
    out_dir = Path(args.out) if args.out else manifest_path.parent / "overlays"

    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1
    if not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    manifest = _load_json(manifest_path)

    total_pages = len(manifest.get("pages") or [])
    if args.pages:
        pages = [int(p) for p in args.pages.split(",")]
    else:
        pages = spot_check_pages(manifest, total_pages)

    print(f"Rendering overlays for pages: {pages}")
    paths = render_overlays(manifest, pdf_path, out_dir, pages=pages, dpi=args.dpi)
    for p in paths:
        print(f"  {p}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="preextract",
        description="ReidAI pre-extraction pipeline",
    )
    sub = parser.add_subparsers(dest="command")

    # -- build --
    p_build = sub.add_parser("build", help="Run all pre-extraction stages")
    p_build.add_argument(
        "--raw",
        required=True,
        metavar="PATH",
        help="Path to llamaparse_raw_result.json",
    )
    p_build.add_argument(
        "--out",
        default="preextract_outputs",
        metavar="DIR",
        help="Output root directory (default: preextract_outputs/)",
    )
    p_build.add_argument(
        "--doc-id",
        default=None,
        metavar="ID",
        help="Stable document identifier (default: parse job id or filename stem)",
    )

    # -- validate --
    p_val = sub.add_parser("validate", help="Re-validate an existing output directory")
    p_val.add_argument(
        "--dir",
        required=True,
        metavar="DIR",
        help="Output directory containing artefact JSON files",
    )

    # -- overlay --
    p_ov = sub.add_parser("overlay", help="Render bbox overlay PNGs")
    p_ov.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="Path to evidence_manifest.json",
    )
    p_ov.add_argument(
        "--pdf",
        required=True,
        metavar="PATH",
        help="Path to original PDF",
    )
    p_ov.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help="Output directory for overlay PNGs (default: <manifest_dir>/overlays/)",
    )
    p_ov.add_argument(
        "--pages",
        default=None,
        metavar="1,2,5",
        help="Comma-separated page numbers to render (default: auto spot-check)",
    )
    p_ov.add_argument(
        "--dpi",
        type=int,
        default=160,
        metavar="DPI",
        help="Render DPI (default: 160)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        return cmd_build(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "overlay":
        return cmd_overlay(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

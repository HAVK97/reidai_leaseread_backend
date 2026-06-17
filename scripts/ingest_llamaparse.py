#!/usr/bin/env python3
"""
LlamaParse ingest: PDF -> raw JSON, legacy bboxes, page PNGs, optional bbox overlays.

Reads leases from ./Leases/ by default; writes per-document folders under ./parse_outputs/.

Requires: LLAMA_CLOUD_API_KEY
Optional deps: llama-cloud, pymupdf, pillow, requests

Usage:
  python scripts/ingest_llamaparse.py --pdf Leases/my_lease.pdf
  python scripts/ingest_llamaparse.py --all
  python scripts/ingest_llamaparse.py --all --out parse_outputs
  python scripts/ingest_llamaparse.py --pdf Leases/x.pdf --no-overlays --no-assets
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Repo root (parent of scripts/)
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_LEASES_DIR = _REPO_ROOT / "Leases"
DEFAULT_OUT_ROOT = _REPO_ROOT / "parse_outputs"


# ---------------------------------------------------------------------------
# Serialization & bbox helpers (from former ingest_pipeline_v3 notebook)
# ---------------------------------------------------------------------------


def to_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=False)
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    return value


def walk_items(items: list[dict] | None):
    for item in items or []:
        yield item
        yield from walk_items(item.get("items"))


def bbox_to_pixels(
    bbox: dict,
    src_w: float,
    src_h: float,
    rendered_w: int,
    rendered_h: int,
) -> dict:
    x = float(bbox["x"])
    y = float(bbox["y"])
    w = float(bbox["w"])
    h = float(bbox["h"])
    sx = rendered_w / src_w
    sy = rendered_h / src_h
    return {
        "normalized": {
            "x": x / src_w,
            "y": y / src_h,
            "w": w / src_w,
            "h": h / src_h,
        },
        "pixels": {
            "x1": round(x * sx),
            "y1": round(y * sy),
            "x2": round((x + w) * sx),
            "y2": round((y + h) * sy),
        },
    }


def rasterize_pdf(pdf_path: Path, out_dir: Path, dpi: int = 160) -> list[dict]:
    import fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    stem = pdf_path.stem
    pages: list[dict] = []

    try:
        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out_path = out_dir / f"{stem}_page_{page_index + 1:04d}.png"
            pix.save(str(out_path))
            pages.append({
                "page_number": page_index + 1,
                "path": str(out_path),
                "width": pix.width,
                "height": pix.height,
            })
    finally:
        doc.close()
    return pages


def build_legacy_bboxes(
    raw_result: dict,
    rendered_pages: list[dict],
) -> list[dict]:
    """Same shape as legacy llamaparse_bboxes.json (items + layout assets)."""
    page_image_by_number = {p["page_number"]: p for p in rendered_pages}
    item_pages = (raw_result.get("items") or {}).get("pages") or []
    page_by_number = {
        int(page["page_number"]): page
        for page in item_pages
        if page.get("page_number") is not None
    }
    bbox_output: list[dict] = []

    for page_number, page in page_by_number.items():
        rendered = page_image_by_number.get(page_number)
        if not rendered:
            continue
        src_w = float(page.get("page_width") or rendered["width"])
        src_h = float(page.get("page_height") or rendered["height"])

        for item_idx, item in enumerate(walk_items(page.get("items"))):
            for bbox_idx, bbox in enumerate(item.get("bbox") or []):
                if not isinstance(bbox, dict) or not {"x", "y", "w", "h"}.issubset(bbox):
                    continue
                scaled = bbox_to_pixels(
                    bbox,
                    src_w=src_w,
                    src_h=src_h,
                    rendered_w=rendered["width"],
                    rendered_h=rendered["height"],
                )
                bbox_output.append({
                    "id": f"p{page_number}_item_{item_idx}_{bbox_idx}",
                    "page_number": page_number,
                    "source": "items",
                    "label": bbox.get("label") or item.get("type") or "bbox",
                    "type": item.get("type"),
                    "confidence": bbox.get("confidence"),
                    "text_preview": (item.get("value") or item.get("md") or "")[:240],
                    "raw_bbox": bbox,
                    **scaled,
                })

    images = (raw_result.get("images_content_metadata") or {}).get("images") or []
    for image_idx, image_info in enumerate(images):
        if image_info.get("category") != "layout":
            continue
        m = re.match(r"page_(\d+)", image_info.get("filename", ""))
        if not m:
            continue
        page_number = int(m.group(1))
        rendered = page_image_by_number.get(page_number)
        if not rendered:
            continue
        page = page_by_number.get(page_number, {})
        src_w = float(page.get("page_width") or rendered["width"])
        src_h = float(page.get("page_height") or rendered["height"])
        bbox = image_info.get("bbox")
        if not isinstance(bbox, dict) or not {"x", "y", "w", "h"}.issubset(bbox):
            continue
        scaled = bbox_to_pixels(
            bbox,
            src_w=src_w,
            src_h=src_h,
            rendered_w=rendered["width"],
            rendered_h=rendered["height"],
        )
        bbox_output.append({
            "id": f"p{page_number}_layout_image_{image_idx}",
            "page_number": page_number,
            "source": "images_content_metadata",
            "label": image_info.get("filename") or "layout",
            "category": image_info.get("category"),
            "content_type": image_info.get("content_type"),
            "presigned_url": image_info.get("presigned_url"),
            "raw_bbox": bbox,
            **scaled,
        })

    return bbox_output


def draw_overlays(
    bbox_output: list[dict],
    rendered_pages: list[dict],
    overlay_dir: Path,
) -> None:
    from collections import defaultdict

    from PIL import Image, ImageDraw

    COLOR_BY_LABEL = {
        "table": "red",
        "figure": "orange",
        "picture": "orange",
        "title": "dodgerblue",
        "heading": "dodgerblue",
        "text": "lime",
        "paragraph": "lime",
        "list": "violet",
        "bbox": "cyan",
    }

    def color_for_label(label: str) -> str:
        label = (label or "").lower()
        for key, color in COLOR_BY_LABEL.items():
            if key in label:
                return color
        return "cyan"

    boxes_by_page: dict[int, list] = defaultdict(list)
    for box in bbox_output:
        boxes_by_page[box["page_number"]].append(box)

    overlay_dir.mkdir(parents=True, exist_ok=True)

    for page in rendered_pages:
        page_number = page["page_number"]
        image = Image.open(page["path"]).convert("RGB")
        draw = ImageDraw.Draw(image)
        for box in boxes_by_page.get(page_number, []):
            px = box["pixels"]
            label = box.get("label") or "bbox"
            color = color_for_label(label)
            xy = [px["x1"], px["y1"], px["x2"], px["y2"]]
            draw.rectangle(xy, outline=color, width=3)
            text = f"{box['id']} {label}"
            draw.text((px["x1"], max(0, px["y1"] - 16)), text, fill=color)
        out_path = overlay_dir / f"page_{page_number:04d}_bbox_overlay.png"
        image.save(out_path)


def download_llamaparse_assets(raw_result: dict, asset_dir: Path) -> int:
    import requests

    asset_dir.mkdir(parents=True, exist_ok=True)
    images = (raw_result.get("images_content_metadata") or {}).get("images") or []
    n = 0
    for image_info in images:
        url = image_info.get("presigned_url")
        filename = image_info.get("filename") or f"asset_{image_info.get('index', n)}.png"
        if not url:
            continue
        out_path = asset_dir / filename
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        out_path.write_bytes(r.content)
        n += 1
    return n


def parse_pdf_with_llamacloud(
    pdf_path: Path,
    *,
    tier: str,
    version: str,
    expand: list[str],
    images_to_save: list[str],
    extract_layout: bool,
) -> dict:
    from llama_cloud import LlamaCloud

    client = LlamaCloud()
    uploaded_file = client.files.create(file=str(pdf_path), purpose="parse")
    parse_kwargs: dict = {
        "file_id": uploaded_file.id,
        "tier": tier,
        "version": version,
        "expand": expand,
        "output_options": {"images_to_save": images_to_save},
    }
    if extract_layout:
        parse_kwargs["extract_layout"] = True

    try:
        result = client.parsing.parse(**parse_kwargs)
    except TypeError as exc:
        if extract_layout and "extract_layout" in str(exc):
            parse_kwargs.pop("extract_layout", None)
            result = client.parsing.parse(**parse_kwargs)
        else:
            raise
    except Exception as exc:
        if extract_layout and "extract_layout" in str(exc):
            parse_kwargs.pop("extract_layout", None)
            result = client.parsing.parse(**parse_kwargs)
        else:
            raise
    return to_plain(result)


def run_one(
    pdf_path: Path,
    out_root: Path,
    *,
    dpi: int,
    tier: str,
    version: str,
    expand: list[str],
    images_to_save: list[str],
    extract_layout: bool,
    overlays: bool,
    download_assets: bool,
    skip_parse: bool,
    raw_json_path: Path | None = None,
) -> Path:
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    doc_id = pdf_path.stem
    out_dir = out_root / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)

    page_images_dir = out_dir / "page_images"
    overlay_dir = out_dir / "bbox_overlays"
    asset_dir = out_dir / "llamaparse_image_assets"

    raw_path = raw_json_path or (out_dir / "llamaparse_raw_result.json")
    bbox_path = out_dir / "llamaparse_bboxes.json"
    manifest_path = out_dir / "evidence_manifest.json"

    if skip_parse and raw_path.is_file():
        raw_result = json.loads(raw_path.read_text(encoding="utf-8"))
        print(f"[skip-parse] Loaded {raw_path}")
    else:
        if not os.environ.get("LLAMA_CLOUD_API_KEY"):
            raise EnvironmentError(
                "LLAMA_CLOUD_API_KEY is not set. Export it before running ingest."
            )
        print(f"Parsing {pdf_path.name} …")
        raw_result = parse_pdf_with_llamacloud(
            pdf_path,
            tier=tier,
            version=version,
            expand=expand,
            images_to_save=images_to_save,
            extract_layout=extract_layout,
        )
        raw_path.write_text(json.dumps(raw_result, indent=2), encoding="utf-8")
        print(f"Saved raw -> {raw_path}")

    job = raw_result.get("job") or {}
    print(f"Parse job status: {job.get('status')} id={job.get('id')}")

    print("Rasterizing PDF pages …")
    rendered_pages = rasterize_pdf(pdf_path, page_images_dir, dpi=dpi)
    print(f"Rendered {len(rendered_pages)} page image(s) -> {page_images_dir}")

    bbox_output = build_legacy_bboxes(raw_result, rendered_pages)
    bbox_path.write_text(json.dumps(bbox_output, indent=2), encoding="utf-8")
    print(f"Saved legacy bboxes ({len(bbox_output)} rows) -> {bbox_path}")

    from preextract.manifest import build_manifest

    manifest = build_manifest(raw_result, doc_id=doc_id, source_file=str(pdf_path))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Saved evidence manifest -> {manifest_path}")

    if overlays:
        draw_overlays(bbox_output, rendered_pages, overlay_dir)
        print(f"Saved bbox overlays -> {overlay_dir}")

    if download_assets:
        try:
            n = download_llamaparse_assets(raw_result, asset_dir)
            print(f"Downloaded {n} asset file(s) -> {asset_dir}")
        except Exception as exc:
            print(f"Asset download skipped/failed: {exc}")

    return out_dir


def discover_pdfs(leases_dir: Path) -> list[Path]:
    if not leases_dir.is_dir():
        return []
    return sorted(leases_dir.glob("*.pdf"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="LlamaParse ingest: PDFs from Leases/ -> parse_outputs/<name>/",
    )
    p.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="Single PDF path (default: first PDF in --leases-dir if omitted with --all)",
    )
    p.add_argument(
        "--leases-dir",
        type=Path,
        default=DEFAULT_LEASES_DIR,
        help=f"Folder containing PDFs (default: {DEFAULT_LEASES_DIR})",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_ROOT,
        help=f"Output root directory (default: {DEFAULT_OUT_ROOT})",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Process every *.pdf in --leases-dir",
    )
    p.add_argument("--dpi", type=int, default=160, help="Raster DPI for page PNGs / overlays")
    p.add_argument("--tier", default="agentic", help="LlamaParse tier")
    p.add_argument("--version", default="latest", help="LlamaParse API version label")
    p.add_argument(
        "--no-layout",
        action="store_true",
        help="Do not request extract_layout=True",
    )
    p.add_argument(
        "--no-overlays",
        action="store_true",
        help="Skip matplotlib/PIL bbox overlay PNGs",
    )
    p.add_argument(
        "--no-assets",
        action="store_true",
        help="Skip downloading presigned_url image assets",
    )
    p.add_argument(
        "--skip-parse",
        action="store_true",
        help="Load existing llamaparse_raw_result.json in output dir (no API call)",
    )
    p.add_argument(
        "--raw",
        type=Path,
        default=None,
        help="With --skip-parse, path to raw JSON (default: <out>/<stem>/llamaparse_raw_result.json)",
    )

    args = p.parse_args(argv)

    expand = ["markdown", "items", "metadata", "images_content_metadata"]
    images_to_save = ["screenshot", "layout"]

    pdfs: list[Path] = []
    if args.all:
        pdfs = discover_pdfs(args.leases_dir)
        if not pdfs:
            print(f"No PDFs found in {args.leases_dir}", file=sys.stderr)
            return 1
    elif args.pdf:
        pdfs = [args.pdf]
    else:
        found = discover_pdfs(args.leases_dir)
        if len(found) == 1:
            pdfs = found
            print(f"Using single lease: {pdfs[0]}")
        elif not found:
            print(f"No PDF in {args.leases_dir}. Pass --pdf PATH or add PDFs to Leases/.", file=sys.stderr)
            return 1
        else:
            print("Multiple PDFs in Leases/; use --all or --pdf <file>.", file=sys.stderr)
            return 1

    args.out.mkdir(parents=True, exist_ok=True)
    if not args.leases_dir.exists():
        args.leases_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created leases directory: {args.leases_dir}")

    for pdf in pdfs:
        raw_override = args.raw if args.skip_parse and len(pdfs) == 1 else None
        if args.skip_parse and raw_override is None and len(pdfs) == 1:
            candidate = args.out / pdf.stem / "llamaparse_raw_result.json"
            if candidate.is_file():
                raw_override = candidate

        run_one(
            pdf,
            args.out,
            dpi=args.dpi,
            tier=args.tier,
            version=args.version,
            expand=expand,
            images_to_save=images_to_save,
            extract_layout=not args.no_layout,
            overlays=not args.no_overlays,
            download_assets=not args.no_assets,
            skip_parse=args.skip_parse,
            raw_json_path=raw_override,
        )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate bbox overlay images for visual validation.

Reads the evidence manifest (not raw parse output directly) so that overlay
coordinates always come from the canonical source.

Usage:
    from preextract.overlays import render_overlays
    render_overlays(manifest, pdf_path, out_dir, pages=[1, 2, 5])
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# Optional imports — overlay generation requires PyMuPDF + Pillow.
try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False

try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


# ---------------------------------------------------------------------------
# Colour scheme
# ---------------------------------------------------------------------------

_COLOR_BY_LABEL: dict[str, str] = {
    "table": "red",
    "figure": "orange",
    "picture": "orange",
    "title": "dodgerblue",
    "heading": "dodgerblue",
    "paragraph_title": "dodgerblue",
    "section-header": "dodgerblue",
    "text": "lime",
    "paragraph": "lime",
    "list": "violet",
    "list_item": "violet",
    "image": "cyan",
    "reference": "yellow",
    "screenshot": "gray",
    "layout": "orange",
}


def _color_for_label(label: str) -> str:
    label = (label or "").lower().replace("-", "_").replace(" ", "_")
    for key, color in _COLOR_BY_LABEL.items():
        if key in label:
            return color
    return "cyan"


# ---------------------------------------------------------------------------
# Core rendering
# ---------------------------------------------------------------------------


def _rasterize_page(
    pdf_path: str | Path,
    page_number: int,
    dpi: int = 160,
) -> tuple["Image.Image", float, float]:
    """Return (PIL Image, render_width, render_height) for one PDF page."""
    if not _HAS_FITZ:
        raise ImportError("PyMuPDF (fitz) is required for overlay rendering. pip install pymupdf")
    if not _HAS_PIL:
        raise ImportError("Pillow is required for overlay rendering. pip install pillow")

    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(page_number - 1)  # 0-based
        scale = dpi / 72.0
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        import io
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        return img, pix.width, pix.height
    finally:
        doc.close()


def _draw_overlay(
    image: "Image.Image",
    page_rows: list[dict],
    page_width: float,
    page_height: float,
    render_w: float,
    render_h: float,
) -> "Image.Image":
    """Draw bboxes from manifest evidence rows onto a PIL image."""
    draw = ImageDraw.Draw(image)
    sx = render_w / page_width
    sy = render_h / page_height

    for row in page_rows:
        rb = row.get("raw_bbox") or {}
        x = float(rb.get("x", 0)) * sx
        y = float(rb.get("y", 0)) * sy
        w = float(rb.get("w") or rb.get("width") or 0) * sx
        h = float(rb.get("h") or rb.get("height") or 0) * sy
        if w <= 0 or h <= 0:
            continue

        label = row.get("label") or "bbox"
        color = _color_for_label(label)
        xy = [round(x), round(y), round(x + w), round(y + h)]
        draw.rectangle(xy, outline=color, width=2)

        eid = row.get("evidence_id", "")
        tag = f"{eid}"
        draw.text((xy[0], max(0, xy[1] - 14)), tag, fill=color)

    return image


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_overlays(
    manifest: dict,
    pdf_path: str | Path,
    out_dir: str | Path,
    pages: Iterable[int] | None = None,
    dpi: int = 160,
) -> list[Path]:
    """
    Render bbox overlay PNGs from the evidence manifest.

    Args:
        manifest:   evidence_manifest dict.
        pdf_path:   path to the original PDF.
        out_dir:    directory where overlay PNGs are saved.
        pages:      page numbers to render (1-based). None → all pages.
        dpi:        render resolution.

    Returns:
        List of output PNG paths.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build page_dims lookup from manifest
    page_dims: dict[int, tuple[float, float]] = {}
    for p in manifest.get("pages") or []:
        pn = p["page_number"]
        page_dims[pn] = (
            float(p.get("page_width") or 595.276),
            float(p.get("page_height") or 842.0),
        )

    # Index evidence by page
    rows_by_page: dict[int, list[dict]] = defaultdict(list)
    for row in manifest.get("evidence") or []:
        if row.get("source") != "items":
            continue
        rows_by_page[row["page_number"]].append(row)

    target_pages = sorted(pages) if pages else sorted(page_dims)
    output_paths: list[Path] = []

    for pn in target_pages:
        if pn not in page_dims:
            continue
        pw, ph = page_dims[pn]
        page_rows = rows_by_page.get(pn, [])

        img, rw, rh = _rasterize_page(pdf_path, pn, dpi=dpi)
        img = _draw_overlay(img, page_rows, pw, ph, rw, rh)

        out_path = out_dir / f"page_{pn:04d}_overlay.png"
        img.save(str(out_path))
        output_paths.append(out_path)

    return output_paths


def spot_check_pages(manifest: dict, total_pages: int) -> list[int]:
    """
    Suggest representative pages to overlay-check:
    first, last, TOC page, LR clause page, dense table page.

    Returns a list of page numbers (1-based).
    """
    pages = [p["page_number"] for p in (manifest.get("pages") or [])]
    if not pages:
        return []

    # Always include page 1 and last page
    suggestions = {pages[0], pages[-1]}

    # Find a table-dense page (most evidence rows of type table)
    from collections import Counter
    page_table_count: Counter = Counter()
    for row in manifest.get("evidence") or []:
        if (row.get("label") or "").lower() in ("table",) or (row.get("item_type") or "") == "table":
            page_table_count[row.get("page_number", 0)] += 1

    if page_table_count:
        suggestions.add(page_table_count.most_common(1)[0][0])

    # Add page 2, 3 as likely LR / particulars pages
    for p in pages[1:4]:
        suggestions.add(p)

    # Add a middle page
    if len(pages) > 4:
        mid = pages[len(pages) // 2]
        suggestions.add(mid)

    return sorted(p for p in suggestions if p in set(pages))

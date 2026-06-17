"""Build the evidence manifest from a LlamaParse raw result.

The manifest is the stable lookup table between text and PDF geometry.
Every downstream artefact cites evidence_id; geometry (raw_bbox, normalized)
lives here and is never re-invented by extractors.

evidence_id scheme: p{page_number}_item_{dfs_idx}_{bbox_idx}
  - dfs_idx matches enumerate(walk_items(page.get("items"))) from
    scripts/ingest_llamaparse.py, so existing llamaparse_bboxes.json .id fields
    are fully compatible for the same parse run.

Image asset ids: p{page_number}_layout_image_{image_idx}
  where image_idx is the index in images_content_metadata.images[].
"""

from __future__ import annotations

import re
import unicodedata

SCHEMA_VERSION = "1.0"
TEXT_PREVIEW_LEN = 240


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_ws(text: str) -> str:
    """Collapse whitespace and normalise unicode for the text field."""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def _dfs_items(
    items: list[dict] | None,
    path_prefix: str,
):
    """
    DFS pre-order walk matching enumerate(walk_items(items)) from
    scripts/ingest_llamaparse.py.

    Yields (item, dfs_idx, item_path) where dfs_idx is the global enumerate
    counter across the page's full item tree.
    """
    counter: list[int] = [0]

    def _recurse(items: list[dict] | None, prefix: str):
        for i, item in enumerate(items or []):
            item_path = f"{prefix}[{i}]"
            idx = counter[0]
            counter[0] += 1
            yield item, idx, item_path
            yield from _recurse(item.get("items") or [], f"{item_path}.items")

    yield from _recurse(items, path_prefix)


def _normalize_bbox(
    raw: dict,
    page_width: float,
    page_height: float,
) -> dict | None:
    """Return normalized {x, y, w, h} by dividing page-unit coords by page dims."""
    try:
        x = float(raw["x"])
        y = float(raw["y"])
        w = float(raw.get("w") or raw.get("width") or 0)
        h = float(raw.get("h") or raw.get("height") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if page_width <= 0 or page_height <= 0:
        return None
    return {
        "x": round(x / page_width, 6),
        "y": round(y / page_height, 6),
        "w": round(w / page_width, 6),
        "h": round(h / page_height, 6),
    }


# ---------------------------------------------------------------------------
# Item rows
# ---------------------------------------------------------------------------


def _item_rows(
    page_number: int,
    items: list[dict],
    page_width: float,
    page_height: float,
    page_path_prefix: str,
) -> list[dict]:
    """Build evidence rows for all (item × bbox) pairs on a page."""
    rows: list[dict] = []

    for item, dfs_idx, item_path in _dfs_items(items, path_prefix=page_path_prefix):
        bboxes = item.get("bbox") or []
        bbox_count = len(bboxes)
        is_leaf = len(item.get("items") or []) == 0
        text = _norm_ws(item.get("value") or item.get("md") or "")
        item_type = item.get("type") or "text"

        for bbox_idx, bbox in enumerate(bboxes):
            if not isinstance(bbox, dict) or not {"x", "y"}.issubset(bbox):
                continue

            raw_bbox = {
                "x": float(bbox.get("x", 0)),
                "y": float(bbox.get("y", 0)),
                "w": float(bbox.get("w") or bbox.get("width") or 0),
                "h": float(bbox.get("h") or bbox.get("height") or 0),
                "label": bbox.get("label"),
                "confidence": bbox.get("confidence"),
                "start_index": bbox.get("start_index"),
                "end_index": bbox.get("end_index"),
            }
            normalized = _normalize_bbox(raw_bbox, page_width, page_height)

            rows.append({
                "evidence_id": f"p{page_number}_item_{dfs_idx}_{bbox_idx}",
                "page_number": page_number,
                "source": "items",
                "parse_path": f"{item_path}.bbox[{bbox_idx}]",
                "item_type": item_type,
                "label": bbox.get("label") or item_type,
                "text": text,
                "text_preview": text[:TEXT_PREVIEW_LEN],
                "raw_bbox": raw_bbox,
                "normalized": normalized,
                "bbox_index": bbox_idx,
                "bbox_count_for_item": bbox_count,
                "is_leaf": is_leaf,
            })

    return rows


# ---------------------------------------------------------------------------
# Image asset rows
# ---------------------------------------------------------------------------


def _image_rows(
    images: list[dict],
    page_dims: dict[int, tuple[float, float]],
) -> list[dict]:
    """Build evidence rows for all images_content_metadata entries."""
    rows: list[dict] = []

    for image_idx, img in enumerate(images or []):
        filename = img.get("filename") or f"image_{image_idx}"
        m = re.match(r"page_(\d+)", filename)
        page_number = int(m.group(1)) if m else (img.get("index", 0) + 1)
        pw, ph = page_dims.get(page_number, (595.276, 842.0))

        bbox = img.get("bbox") or {}
        raw_bbox = {
            "x": float(bbox.get("x", 0)),
            "y": float(bbox.get("y", 0)),
            "w": float(bbox.get("w") or bbox.get("width") or 0),
            "h": float(bbox.get("h") or bbox.get("height") or 0),
            "label": None,
            "confidence": None,
            "start_index": None,
            "end_index": None,
        }
        normalized = _normalize_bbox(raw_bbox, pw, ph) if bbox else None

        rows.append({
            "evidence_id": f"p{page_number}_layout_image_{image_idx}",
            "page_number": page_number,
            "source": "images_content_metadata",
            "parse_path": f"images_content_metadata.images[{image_idx}].bbox",
            "item_type": "image",
            "label": img.get("category") or "image",
            "text": "",
            "text_preview": "",
            "raw_bbox": raw_bbox,
            "normalized": normalized,
            "bbox_index": 0,
            "bbox_count_for_item": 1,
            "is_leaf": True,
            "asset": {
                "filename": filename,
                "category": img.get("category"),
                "content_type": img.get("content_type"),
                "presigned_url": img.get("presigned_url"),
            },
        })

    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_manifest(
    raw: dict,
    *,
    doc_id: str | None = None,
    source_file: str | None = None,
) -> dict:
    """
    Build the evidence manifest from a LlamaParse raw result dict.

    Args:
        raw: parsed llamaparse_raw_result.json (from LlamaCloud SDK or file).
        doc_id: stable identifier for this document run. Defaults to parse job id.
        source_file: original PDF filename or path for metadata.

    Returns:
        evidence_manifest dict ready for JSON serialisation.

    The returned manifest's evidence[] array has one row per (item × bbox) pair
    from items.pages[].items[] (DFS pre-order) plus one row per image asset.
    All evidence_id values are stable for a given parse tier/version/job.
    """
    job = raw.get("job") or {}
    parse_job_id = job.get("id") or ""

    if doc_id is None:
        doc_id = parse_job_id or "unknown"

    # Build page dimension lookup from items.pages
    items_container = raw.get("items") or {}
    item_pages: list[dict] = items_container.get("pages") or []

    markdown_by_page: dict[int, str] = {}
    for p in (raw.get("markdown") or {}).get("pages") or []:
        pn = int(p.get("page_number") or 0)
        markdown_by_page[pn] = p.get("markdown") or ""

    page_dims: dict[int, tuple[float, float]] = {}
    pages_out: list[dict] = []

    for p in item_pages:
        pn = int(p.get("page_number") or 0)
        pw = float(p.get("page_width") or 595.276)
        ph = float(p.get("page_height") or 842.0)
        page_dims[pn] = (pw, ph)
        pages_out.append({
            "page_number": pn,
            "page_width": pw,
            "page_height": ph,
            "markdown": markdown_by_page.get(pn, ""),
        })

    # Build evidence rows from items (DFS per page)
    evidence: list[dict] = []
    for page_list_idx, p in enumerate(item_pages):
        pn = int(p.get("page_number") or 0)
        pw, ph = page_dims.get(pn, (595.276, 842.0))
        page_path_prefix = f"items.pages[{page_list_idx}].items"
        evidence.extend(
            _item_rows(pn, p.get("items") or [], pw, ph, page_path_prefix)
        )

    # Build evidence rows from image assets
    images_meta = raw.get("images_content_metadata") or {}
    evidence.extend(_image_rows(images_meta.get("images") or [], page_dims))

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": doc_id,
        "source_file": source_file or job.get("name"),
        "parse_job": {
            "id": parse_job_id,
            "status": job.get("status"),
            "tier": job.get("tier"),
            "version": None,
            "created_at": job.get("created_at"),
        },
        "pages": pages_out,
        "evidence": evidence,
    }

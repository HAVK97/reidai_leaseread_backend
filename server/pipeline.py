"""Background pipeline: ingest -> preextract -> extract."""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from extract.groups import list_available_groups
from extract.runner import run_all_groups
from preextract.definitions import build_definitions_index
from preextract.manifest import build_manifest
from preextract.sections import build_section_map
from preextract.structure_v2 import build_segment_map_v2, build_structure_map_v2
from preextract.validation import validate_all
from scripts.ingest_llamaparse import (
    build_legacy_bboxes,
    parse_pdf_with_llamacloud,
    rasterize_pdf,
)

from .config import Settings
from .jobs import JobStore
from .models import JobStage
from .storage import JobStorage

logger = logging.getLogger(__name__)

LLAMAPARSE_EXPAND = ["markdown", "items", "metadata", "images_content_metadata"]
LLAMAPARSE_IMAGES = ["screenshot", "layout"]


def _save_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def run_ingest(
    *,
    job_id: str,
    pdf_path: Path,
    storage: JobStorage,
    settings: Settings,
) -> dict:
    parse_dir = storage.parse_dir(job_id)
    parse_dir.mkdir(parents=True, exist_ok=True)
    raw_path = storage.raw_parse_path(job_id)

    if settings.ingest_skip_parse and raw_path.is_file():
        logger.info("job=%s skip-parse loading %s", job_id, raw_path)
        raw = storage.load_json(raw_path)
    else:
        if not os.environ.get("LLAMA_CLOUD_API_KEY"):
            raise EnvironmentError("LLAMA_CLOUD_API_KEY is not set")
        logger.info("job=%s llamaparse start", job_id)
        raw = parse_pdf_with_llamacloud(
            pdf_path,
            tier=settings.llamaparse_tier,
            version=settings.llamaparse_version,
            expand=LLAMAPARSE_EXPAND,
            images_to_save=LLAMAPARSE_IMAGES,
            extract_layout=True,
        )
        _save_json(raw, raw_path)

    page_images_dir = parse_dir / "page_images"
    rendered_pages = rasterize_pdf(pdf_path, page_images_dir, dpi=settings.ingest_dpi)
    bbox_output = build_legacy_bboxes(raw, rendered_pages)
    _save_json(bbox_output, parse_dir / "llamaparse_bboxes.json")

    doc_id = job_id
    manifest = build_manifest(raw, doc_id=doc_id, source_file=str(pdf_path))
    _save_json(manifest, parse_dir / "evidence_manifest.json")

    return raw


def run_preextract(
    *,
    job_id: str,
    raw: dict,
    storage: JobStorage,
    settings: Settings,
) -> dict:
    out_dir = storage.preextract_dir(job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_id = job_id

    _save_json(raw, out_dir / "parse.json")

    manifest = build_manifest(raw, doc_id=doc_id, source_file=str(storage.input_pdf(job_id)))
    _save_json(manifest, out_dir / "evidence_manifest.json")

    segment_map = build_segment_map_v2(raw, manifest)
    _save_json(segment_map, out_dir / "segment_map.json")

    structure_map = build_structure_map_v2(raw, manifest, segment_map)
    _save_json(structure_map, out_dir / "structure_map.json")

    definitions_index = build_definitions_index(manifest, structure_map)
    _save_json(definitions_index, out_dir / "definitions_index.json")

    section_map = build_section_map(manifest, structure_map, segment_map, definitions_index)
    _save_json(section_map, out_dir / "semantic_section_map.json")

    report = validate_all(
        manifest=manifest,
        segment_map=segment_map,
        structure_map=structure_map,
        definitions_index=definitions_index,
        section_map=section_map,
    )
    _save_json(report, out_dir / "validation_report.json")
    return report


def run_extract_all(
    *,
    job_id: str,
    storage: JobStorage,
    settings: Settings,
    store: JobStore,
    run_id: str,
) -> list[str]:
    preextract_dir = storage.preextract_dir(job_id)
    output_dir = storage.extractions_dir(job_id, run_id)
    groups = list_available_groups()
    completed: list[str] = []
    profile_override = settings.extraction_model_profile
    if profile_override is None and settings.extraction_provider == "fake":
        profile_override = "fake"

    def _on_group_start(group_id: str, idx: int, total: int) -> None:
        store.set_extraction_progress(
            job_id,
            completed=idx - 1,
            total=total,
            current_group=group_id,
        )
        logger.info("job=%s extract %s (%d/%d)", job_id, group_id, idx, total)

    def _on_group_complete(group_id: str, _envelope: dict, _idx: int, _total: int) -> None:
        completed.append(group_id)
        store.update(job_id, groups_completed=completed)

    run_all_groups(
        preextract_dir=preextract_dir,
        group_ids=groups,
        model_profile_override=profile_override,
        dry_run=settings.extraction_dry_run,
        output_dir=output_dir,
        run_id=run_id,
        on_group_start=_on_group_start,
        on_group_complete=_on_group_complete,
    )

    store.set_extraction_progress(
        job_id,
        completed=len(groups),
        total=len(groups),
        current_group=None,
    )
    return completed


def run_pipeline(
    job_id: str,
    *,
    settings: Settings,
    store: JobStore,
    storage: JobStorage,
) -> None:
    pdf_path = storage.input_pdf(job_id)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    try:
        store.update(job_id, doc_id=job_id, run_id=run_id)

        store.set_stage(job_id, JobStage.PARSING)
        raw = run_ingest(job_id=job_id, pdf_path=pdf_path, storage=storage, settings=settings)

        store.set_stage(job_id, JobStage.PREEXTRACTING)
        report = run_preextract(job_id=job_id, raw=raw, storage=storage, settings=settings)
        validation_passed = bool(report.get("passed"))
        store.update(job_id, validation_passed=validation_passed)
        if settings.fail_on_validation and not validation_passed:
            raise RuntimeError("Preextract validation failed")

        store.set_stage(job_id, JobStage.EXTRACTING)
        run_extract_all(
            job_id=job_id,
            storage=storage,
            settings=settings,
            store=store,
            run_id=run_id,
        )

        store.set_stage(job_id, JobStage.COMPLETED)
        logger.info("job=%s completed run_id=%s", job_id, run_id)

    except Exception as exc:
        logger.exception("job=%s failed", job_id)
        store.set_failed(job_id, f"{exc}\n{traceback.format_exc()}")

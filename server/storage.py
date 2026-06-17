"""Filesystem layout for per-job pipeline artefacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import UploadFile

from .models import JobRecord


class JobStorage:
    """Manages on-disk paths for a single job's pipeline outputs."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.jobs_root = data_dir / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / job_id

    def ensure_job_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def meta_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "meta.json"

    def input_pdf(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "input.pdf"

    def parse_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "parse"

    def raw_parse_path(self, job_id: str) -> Path:
        return self.parse_dir(job_id) / "llamaparse_raw_result.json"

    def preextract_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "preextract"

    def extractions_dir(self, job_id: str, run_id: str) -> Path:
        return self.job_dir(job_id) / "extractions" / run_id

    async def save_upload(self, job_id: str, upload: UploadFile) -> Path:
        self.ensure_job_dir(job_id)
        dest = self.input_pdf(job_id)
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        return dest

    def save_meta(self, record: JobRecord) -> None:
        self.ensure_job_dir(record.job_id)
        self.meta_path(record.job_id).write_text(
            record.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def load_meta(self, job_id: str) -> JobRecord | None:
        path = self.meta_path(job_id)
        if not path.is_file():
            return None
        return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_job_ids(self) -> list[str]:
        if not self.jobs_root.is_dir():
            return []
        return sorted(
            p.name
            for p in self.jobs_root.iterdir()
            if p.is_dir() and (p / "meta.json").is_file()
        )

    def load_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def list_preextract_artefacts(self, job_id: str) -> list[str]:
        pre_dir = self.preextract_dir(job_id)
        if not pre_dir.is_dir():
            return []
        return sorted(p.name for p in pre_dir.glob("*.json"))

    def artefact_path(self, job_id: str, name: str) -> Path | None:
        pre_dir = self.preextract_dir(job_id)
        candidate = pre_dir / name
        if candidate.is_file():
            return candidate
        parse_candidate = self.parse_dir(job_id) / name
        if parse_candidate.is_file():
            return parse_candidate
        return None

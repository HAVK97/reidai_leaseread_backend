"""In-memory job registry with filesystem persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from .models import ExtractionProgress, JobRecord, JobStage
from .storage import JobStorage


class JobStore:
    def __init__(self, storage: JobStorage) -> None:
        self.storage = storage
        self._jobs: dict[str, JobRecord] = {}

    def load_existing(self) -> None:
        for job_id in self.storage.list_job_ids():
            record = self.storage.load_meta(job_id)
            if record is not None:
                self._jobs[job_id] = record

    def create_job(self, filename: str) -> JobRecord:
        now = datetime.utcnow()
        job_id = str(uuid.uuid4())
        record = JobRecord(
            job_id=job_id,
            filename=filename or "upload.pdf",
            stage=JobStage.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = record
        self.storage.save_meta(record)
        return record

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def require(self, job_id: str) -> JobRecord:
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        return record

    def update(self, job_id: str, **changes) -> JobRecord:
        record = self.require(job_id)
        for key, value in changes.items():
            setattr(record, key, value)
        record.touch()
        self.storage.save_meta(record)
        return record

    def set_stage(self, job_id: str, stage: JobStage) -> JobRecord:
        return self.update(job_id, stage=stage, error=None)

    def set_failed(self, job_id: str, error: str) -> JobRecord:
        return self.update(job_id, stage=JobStage.FAILED, error=error)

    def set_extraction_progress(
        self,
        job_id: str,
        *,
        completed: int,
        total: int,
        current_group: str | None,
    ) -> JobRecord:
        return self.update(
            job_id,
            extraction_progress=ExtractionProgress(
                completed=completed,
                total=total,
                current_group=current_group,
            ),
        )

    def list_jobs(self) -> list[JobRecord]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

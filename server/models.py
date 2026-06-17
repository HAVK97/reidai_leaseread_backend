"""API request/response models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStage(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    PREEXTRACTING = "preextracting"
    EXTRACTING = "extracting"
    COMPLETED = "completed"
    FAILED = "failed"


class ExtractionProgress(BaseModel):
    completed: int = 0
    total: int = 0
    current_group: str | None = None


class JobRecord(BaseModel):
    job_id: str
    filename: str
    stage: JobStage = JobStage.QUEUED
    created_at: datetime
    updated_at: datetime
    doc_id: str | None = None
    run_id: str | None = None
    error: str | None = None
    extraction_progress: ExtractionProgress | None = None
    validation_passed: bool | None = None
    groups_completed: list[str] = Field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()


class JobCreateResponse(BaseModel):
    job_id: str
    filename: str
    stage: JobStage
    created_at: datetime


class JobStatusResponse(BaseModel):
    job_id: str
    filename: str
    stage: JobStage
    created_at: datetime
    updated_at: datetime
    doc_id: str | None = None
    run_id: str | None = None
    error: str | None = None
    extraction_progress: ExtractionProgress | None = None
    validation_passed: bool | None = None
    groups_completed: list[str] = Field(default_factory=list)

    @classmethod
    def from_record(cls, record: JobRecord) -> JobStatusResponse:
        return cls(**record.model_dump())


class GroupListResponse(BaseModel):
    groups: list[str]


class JobResultsResponse(BaseModel):
    job_id: str
    run_id: str
    groups: dict[str, Any]


class ArtefactListResponse(BaseModel):
    artefacts: list[str]

"""FastAPI application entrypoint."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from extract.groups import list_available_groups

from .config import Settings, get_settings
from .jobs import JobStore
from .models import (
    ArtefactListResponse,
    GroupListResponse,
    JobCreateResponse,
    JobRecord,
    JobResultsResponse,
    JobStage,
    JobStatusResponse,
)
from .pipeline import run_pipeline
from .storage import JobStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    storage = JobStorage(settings.data_dir)
    store = JobStore(storage)
    store.load_existing()
    app.state.settings = settings
    app.state.storage = storage
    app.state.job_store = store
    logger.info("ReidAI server started data_dir=%s jobs=%d", settings.data_dir, len(store.list_jobs()))
    yield


app = FastAPI(
    title="ReidAI API",
    description="Upload lease PDFs and run the ReidAI ingest → preextract → extract pipeline.",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_store(request: Request) -> JobStore:
    return request.app.state.job_store


def _get_storage(request: Request) -> JobStorage:
    return request.app.state.storage


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/groups", response_model=GroupListResponse)
def list_groups() -> GroupListResponse:
    return GroupListResponse(groups=list_available_groups())


@app.post("/api/jobs", response_model=JobCreateResponse, status_code=202)
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> JobCreateResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

    store = _get_store(request)
    storage = _get_storage(request)
    settings = _get_settings(request)

    record = store.create_job(file.filename)
    await storage.save_upload(record.job_id, file)

    background_tasks.add_task(
        run_pipeline,
        record.job_id,
        settings=settings,
        store=store,
        storage=storage,
    )

    return JobCreateResponse(
        job_id=record.job_id,
        filename=record.filename,
        stage=record.stage,
        created_at=record.created_at,
    )


@app.get("/api/jobs", response_model=list[JobStatusResponse])
def list_jobs(request: Request) -> list[JobStatusResponse]:
    store = _get_store(request)
    return [JobStatusResponse.from_record(job) for job in store.list_jobs()]


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str, request: Request) -> JobStatusResponse:
    store = _get_store(request)
    try:
        record = store.require(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    return JobStatusResponse.from_record(record)


@app.get("/api/jobs/{job_id}/results", response_model=JobResultsResponse)
def get_job_results(job_id: str, request: Request) -> JobResultsResponse:
    store = _get_store(request)
    storage = _get_storage(request)

    try:
        record = store.require(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None

    if record.stage != JobStage.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job not complete (stage={record.stage.value})",
        )
    if not record.run_id:
        raise HTTPException(status_code=404, detail="Run id missing for completed job")

    results_dir = storage.extractions_dir(job_id, record.run_id)
    if not results_dir.is_dir():
        raise HTTPException(status_code=404, detail="Extraction results not found")

    groups: dict = {}
    for path in sorted(results_dir.glob("*.json")):
        groups[path.stem] = json.loads(path.read_text(encoding="utf-8"))

    return JobResultsResponse(job_id=job_id, run_id=record.run_id, groups=groups)


@app.get("/api/jobs/{job_id}/results/{group_id}")
def get_group_result(job_id: str, group_id: str, request: Request) -> JSONResponse:
    store = _get_store(request)
    storage = _get_storage(request)

    try:
        record = store.require(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None

    if record.stage != JobStage.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job not complete (stage={record.stage.value})",
        )
    if not record.run_id:
        raise HTTPException(status_code=404, detail="Run id missing")

    path = storage.extractions_dir(job_id, record.run_id) / f"{group_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Group result not found: {group_id}")

    return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/jobs/{job_id}/artefacts", response_model=ArtefactListResponse)
def list_artefacts(job_id: str, request: Request) -> ArtefactListResponse:
    store = _get_store(request)
    storage = _get_storage(request)

    try:
        store.require(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None

    names = storage.list_preextract_artefacts(job_id)
    parse_names = [
        p.name
        for p in storage.parse_dir(job_id).glob("*.json")
        if p.is_file()
    ] if storage.parse_dir(job_id).is_dir() else []

    return ArtefactListResponse(artefacts=sorted(set(names + parse_names)))


@app.get("/api/jobs/{job_id}/artefacts/{artefact_name}")
def get_artefact(job_id: str, artefact_name: str, request: Request) -> JSONResponse:
    store = _get_store(request)
    storage = _get_storage(request)

    try:
        store.require(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None

    if not artefact_name.endswith(".json"):
        artefact_name = f"{artefact_name}.json"

    path = storage.artefact_path(job_id, artefact_name)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Artefact not found: {artefact_name}")

    return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/jobs/{job_id}/pdf")
def get_input_pdf(job_id: str, request: Request) -> FileResponse:
    store = _get_store(request)
    storage = _get_storage(request)

    try:
        record = store.require(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found") from None

    pdf_path = storage.input_pdf(job_id)
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="Uploaded PDF not found")

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=record.filename,
    )


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()

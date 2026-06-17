# ReidAI FastAPI Server

Single FastAPI app that wraps the existing pipeline:

```text
upload PDF -> LlamaParse -> preextract -> extract (all groups)
```

## Quick start

From the repo root:

```bash
pip install -r requirements-server.txt

export LLAMA_CLOUD_API_KEY=...
export GEMINI_API_KEY=...   # or GOOGLE_API_KEY

uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

Dev mode without paid extraction calls:

```bash
export REIDAI_EXTRACTION_PROVIDER=fake
uvicorn server.main:app --reload
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/groups` | List extraction groups |
| `POST` | `/api/jobs` | Upload PDF, returns `job_id` (202) |
| `GET` | `/api/jobs` | List jobs |
| `GET` | `/api/jobs/{job_id}` | Poll job status |
| `GET` | `/api/jobs/{job_id}/results` | All group extraction JSON when complete |
| `GET` | `/api/jobs/{job_id}/results/{group_id}` | One group result |
| `GET` | `/api/jobs/{job_id}/artefacts` | List preextract/parse JSON artefacts |
| `GET` | `/api/jobs/{job_id}/artefacts/{name}` | Fetch one artefact (e.g. `evidence_manifest`) |
| `GET` | `/api/jobs/{job_id}/pdf` | Download uploaded PDF |

### Upload

```bash
curl -X POST http://localhost:8000/api/jobs \
  -F "file=@Leases/my_lease.pdf"
```

### Poll

```bash
curl http://localhost:8000/api/jobs/<job_id>
```

Stages: `queued` → `parsing` → `preextracting` → `extracting` → `completed` | `failed`

### Results

```bash
curl http://localhost:8000/api/jobs/<job_id>/results
```

## On-disk layout

Each job writes under `data/jobs/<job_id>/`:

```text
data/jobs/<job_id>/
  meta.json
  input.pdf
  parse/
    llamaparse_raw_result.json
    evidence_manifest.json
    page_images/
  preextract/
    evidence_manifest.json
    segment_map.json
    structure_map.json
    definitions_index.json
    semantic_section_map.json
    validation_report.json
  extractions/<run_id>/
    parties.json
    rent_review_header.json
    ...
```

## Configuration

Environment variables (prefix `REIDAI_`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `REIDAI_DATA_DIR` | `./data` | Job storage root |
| `REIDAI_CORS_ORIGINS` | `*` | Comma-separated CORS origins |
| `REIDAI_INGEST_SKIP_PARSE` | `false` | Reuse cached parse JSON in job dir |
| `REIDAI_EXTRACTION_PROVIDER` | — | Set to `fake` for dev |
| `REIDAI_EXTRACTION_MODEL_PROFILE` | — | Override model profile for all groups |
| `REIDAI_EXTRACTION_DRY_RUN` | `false` | Build packs only, skip LLM |
| `REIDAI_FAIL_ON_VALIDATION` | `false` | Fail job if preextract validation fails |

External API keys:

- `LLAMA_CLOUD_API_KEY` — required for parse (unless skip-parse)
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` — default extraction provider
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — if using those profiles

## Frontend integration pattern

1. `POST /api/jobs` with multipart PDF upload.
2. Poll `GET /api/jobs/{id}` every few seconds until `stage === "completed"`.
3. Fetch `GET /api/jobs/{id}/results` for all extraction groups.
4. Use `GET /api/jobs/{id}/artefacts/evidence_manifest` for bbox highlights.
5. Use `GET /api/jobs/{id}/pdf` to render the source PDF in the viewer.

## Notes

- Uses FastAPI `BackgroundTasks` — fine for single-instance MVP. Move to Celery/ARQ when you need multiple workers or retries.
- Job state persists to `meta.json`; in-flight jobs are lost on server restart.
- OpenAPI docs: `http://localhost:8000/docs`

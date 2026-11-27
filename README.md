# ReidAI LeaseRead (backend)

Reads UK commercial lease PDFs and returns a standard, reviewable data set. Every value links to the exact page and passage it came from, so a lease advisor can check it in one click.

**Who it's for:** lease advisory and property teams that abstract leases by hand. Co-designed with the head of lease advisory at a major UK real estate consultancy.

**Result:** lease extraction went from about 4 hours to about 15 minutes per lease. Piloted by 8 companies [CONFIRM]. Selected for Series AI, the Microsoft and Seedrs accelerator.

This repo is the Python pipeline and API. The review UI is in [reidai_tabular](https://github.com/HAVK97/reidai_tabular).

## How it works

```text
PDF ─► parse ─► pre-extract ─► extract (17 groups) ─► resolve quotes ─► JSON per group
       LlamaParse  deterministic     targeted evidence pack    page + bounding box
                   structure map     per group, one LLM call   status, confidence, review flag
```

| Stage | Code | What it does |
|---|---|---|
| 1. Parse | `scripts/ingest_llamaparse.py` | LlamaParse returns text, tables and bounding boxes. PyMuPDF renders page images |
| 2. Pre-extract | `preextract/` | No LLM. Builds an evidence manifest with a stable ID per text region, then maps segments (current lease vs annexed old lease), clause structure, schedules, defined terms and topic sections. Writes a validation report |
| 3. Assemble | `extract/assembler.py` | For each extraction group, builds a targeted evidence pack: only the relevant definitions, lease particulars and topic clauses, each row labelled with its evidence ID and page |
| 4. Extract | `extract/runner.py`, `extract/providers.py` | One structured-output call per group. Each field returns `value`, `status` (`found`, `not_present`, `unclear`), verbatim `quotes`, `reasoning` and `confidence` |
| 5. Resolve | `extract/resolver.py` | Matches each quote back to evidence rows, so every value maps to a page and bounding box. No match or an ambiguous match sets `needs_review` |
| 6. Serve | `server/` | FastAPI job API: upload a PDF, poll status, fetch results, artefacts and the source PDF |

The 17 groups (parties, property, financials, rent review, break rights, alienation, insurance, service charge and others) hold 82 fields. Each group is a JSON file in `extract/config/groups/`, with per-field decision rules and allowed values.

## Key design decisions

- **Targeted evidence, not the whole lease.** Long leases are split by structure first, so each group sees only the clauses and definitions it needs. Smaller context, fewer wrong-clause answers and cheaper calls. Trade-off: if sectioning misses a clause, the model never sees it, so pre-extraction writes a validation report.
- **Structure in code, judgement in the model.** Clause numbering, schedules, definitions and old-versus-current lease segments are detected deterministically. The model only reads and decides.
- **The model quotes; code finds the evidence.** The model returns short verbatim quotes, never evidence IDs, which models tend to get wrong. The resolver maps quotes to rows itself. Unverifiable values are flagged for review, not hidden.
- **Groups as config.** Adding or changing fields means editing JSON, not code. Each group carries its own prompt version and model profile.
- **Provider-agnostic.** Model profiles for Gemini (default), OpenAI and Anthropic in `extract/config/model_profiles.json`, plus a `fake` provider for development without API calls. Can be set per group.
- **Simple job runner.** FastAPI background tasks and job state on disk. Fine for one instance. In-flight jobs are lost on restart; multiple workers would need a proper queue.

## Stack

Python 3.10+, FastAPI, Pydantic, LlamaParse (`llama-cloud`), PyMuPDF, Pillow, Google Gen AI SDK (Gemini), OpenAI and Anthropic SDKs, pytest.

## Repo layout

```
scripts/ingest_llamaparse.py   parse a PDF with LlamaParse
preextract/                    deterministic structure, definitions and sections (+ JSON schemas)
extract/                       evidence packs, LLM calls, quote resolver, normalisers
extract/config/                17 group configs, model profiles
server/                        FastAPI app (see server/README.md for the full API)
tests/                         pytest suite
docs/                          approach, field definitions and build guides
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-server.txt
```

API keys, set as environment variables:

- `LLAMA_CLOUD_API_KEY` for parsing
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for the default extraction profiles
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` only if you use those profiles

## Run

API server:

```bash
uvicorn server.main:app --reload --port 8000
# without LLM calls (parsing still needs LLAMA_CLOUD_API_KEY):
REIDAI_EXTRACTION_PROVIDER=fake uvicorn server.main:app --reload --port 8000

curl -X POST http://localhost:8000/api/jobs -F "file=@path/to/lease.pdf"
curl http://localhost:8000/api/jobs/<job_id>
curl http://localhost:8000/api/jobs/<job_id>/results
```

Docs at http://localhost:8000/docs. Other `REIDAI_*` settings are listed in [`server/README.md`](server/README.md).

Pipeline from the command line:

```bash
python scripts/ingest_llamaparse.py --pdf path/to/lease.pdf
python -m preextract build --raw parse_outputs/<doc_id>/llamaparse_raw_result.json --out preextract_outputs
python -m extract run --dir preextract_outputs/<doc_id> --all
python -m extract run --dir preextract_outputs/<doc_id> --group parties --model-profile fake
python -m extract list-groups
```

Tests:

```bash
pytest tests/test_definitions.py tests/test_normalizers.py tests/test_sections.py tests/test_structure_v2.py
```

The rest of the suite runs against parsed lease fixtures that are not in this repo.

## What I'd do next

- Replace the private lease fixtures with synthetic leases so the full test suite runs anywhere.
- Build a labelled set of leases and report accuracy per field and per group.
- Move jobs to a proper queue so runs survive restarts and scale out.
- Feed reviewer corrections back into group decision rules.

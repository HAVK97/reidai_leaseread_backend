# ReidAI Repo Flow

This document explains how the repo works end to end: what each major directory does, how data moves through the pipeline, what artefacts are produced, and how those artefacts are used for grounded lease extraction.

ReidAI is a document intelligence pipeline for commercial leases. The important product constraint is provenance: extracted answers should not be free-floating model output. They should resolve back to stable `evidence_id` rows, which in turn resolve back to PDF text and bounding boxes.

## End-To-End Flow

```text
Lease PDF
  -> scripts/ingest_llamaparse.py
  -> parse_outputs/<doc_id>/
  -> python -m preextract build
  -> preextract_outputs/<doc_id>/
  -> python -m extract run
  -> preextract_outputs/<doc_id>/extractions/<run_id>/<group_id>.json
```

At a high level:

1. `scripts/` gets raw PDF content out of LlamaParse and writes parse artefacts.
2. `preextract/` deterministically turns raw parse output into canonical evidence, structure, definitions, and semantic topic maps.
3. `extract/` builds scoped evidence packs, asks an LLM for structured field values, resolves the model's quotes back to evidence rows, and writes auditable JSON.

The LLM stage depends on the deterministic stages. If `preextract` mis-segments a document or routes the wrong clauses into a topic, `extract` will see the wrong evidence pack.

## Directory Responsibilities

### `scripts/`

`scripts/` is the ingest layer. It currently contains one real production script:

- `scripts/ingest_llamaparse.py`

Its job is to take lease PDFs and produce LlamaParse-backed parse artefacts under `parse_outputs/<doc_id>/`.

Typical command:

```bash
python scripts/ingest_llamaparse.py --pdf Leases/my_lease.pdf
```

Useful variants:

```bash
python scripts/ingest_llamaparse.py --all
python scripts/ingest_llamaparse.py --pdf Leases/my_lease.pdf --skip-parse
python scripts/ingest_llamaparse.py --pdf Leases/my_lease.pdf --no-overlays --no-assets
```

Main outputs per document:

- `llamaparse_raw_result.json`: the raw LlamaParse response.
- `llamaparse_bboxes.json`: legacy flat bbox output with pixel coordinates.
- `evidence_manifest.json`: canonical evidence rows, built through `preextract.manifest.build_manifest`.
- `page_images/`: rasterized PDF pages.
- `bbox_overlays/`: optional debug images with boxes drawn.
- `llamaparse_image_assets/`: optional downloaded assets from LlamaParse presigned URLs.

The most important output is `llamaparse_raw_result.json`. That is the input to the full `preextract` pipeline.

`scripts/ingest_llamaparse.py` also writes an early `evidence_manifest.json`, but the normal full pipeline rebuilds the manifest inside `preextract_outputs/<doc_id>/`.

External dependencies:

- `LLAMA_CLOUD_API_KEY`
- `llama-cloud`
- `pymupdf`
- `pillow`
- `requests`

### `preextract/`

`preextract/` is the deterministic pre-extraction layer. It consumes LlamaParse raw JSON and writes the artefacts that make LLM extraction controlled and auditable.

Typical command:

```bash
python -m preextract build \
  --raw parse_outputs/my_lease/llamaparse_raw_result.json \
  --out preextract_outputs
```

Other commands:

```bash
python -m preextract validate --dir preextract_outputs/my_lease
python -m preextract overlay --manifest preextract_outputs/my_lease/evidence_manifest.json --pdf Leases/my_lease.pdf
```

The pipeline is:

```text
llamaparse_raw_result.json
  -> evidence_manifest.json
  -> segment_map.json
  -> structure_map.json
  -> definitions_index.json
  -> semantic_section_map.json
  -> validation_report.json
```

The major modules are:

- `preextract/manifest.py`: builds the canonical evidence table from LlamaParse output.
- `preextract/structure_detector.py`: analyses lease pages, front pages, tables of contents, clauses, schedules, plans, and instrument spans.
- `preextract/structure_v2.py`: builds `segment_map.json` and `structure_map.json` from the detector output.
- `preextract/definitions.py`: extracts defined terms and interpretation rules.
- `preextract/sections.py`: routes structure/evidence into semantic topics such as `rent_review`, `insurance`, `alienation`, and `service_charge`.
- `preextract/validation.py`: validates the generated artefacts with custom checks.
- `preextract/overlays.py`: renders visual QA overlays from manifest bboxes and the original PDF.
- `preextract/segments.py`: older/alternate segmentation logic; not the active CLI path.

#### `evidence_manifest.json`

This is the core provenance artefact. Each row has a stable `evidence_id`, page number, text, source metadata, and bounding boxes.

Example ID shapes:

```text
p6_item_14_0
p12_layout_image_1
```

Downstream code should cite `evidence_id`, not invented coordinates. The UI can later resolve `evidence_id -> page/text/bbox`.

#### `segment_map.json`

This identifies logical instruments inside the PDF. A bundled lease pack can contain a current lease, annexed old lease, old schedules, or other instrument-like sections.

The active v2 path is focused on current vs annexed/old lease segmentation. This matters because old lease text may conflict with current lease text.

#### `structure_map.json`

This maps the legal structure of each segment:

- cover/front matter
- lease particulars
- prescribed clauses
- numbered clauses
- definitions sections
- schedules
- plans
- tables of contents

It also contains box assignments, linking evidence rows to structural anchors.

#### `definitions_index.json`

This indexes defined terms. Extraction groups can request specific terms by allowlist instead of dumping the whole definitions section into every prompt.

For example, rent-related extraction may pull definitions like `Annual Rent`, `Rent`, or `Review Date`.

#### `semantic_section_map.json`

This maps product topics to evidence IDs. Topics are configured in `preextract/sections.py`.

Examples:

- `premises`
- `rent_review`
- `insurance`
- `service_charge`
- `repairs_decoration`
- `alterations`
- `alienation`
- `permitted_use`
- `landlord_tenant_act_1954`
- `break_option`
- `guarantor_surety`
- `rights_granted`
- `original_rent`
- `third_party_determination`

This map lets `extract/` ask for only the relevant evidence for a group instead of sending the whole lease to the model.

#### `validation_report.json`

This records whether the generated artefacts pass the repo's custom validation checks. It is not a substitute for human legal review, but it catches common structural and provenance problems before extraction.

### `extract/`

`extract/` is the LLM-backed field extraction layer. It consumes a completed `preextract_outputs/<doc_id>/` directory and writes structured extraction results under:

```text
preextract_outputs/<doc_id>/extractions/<run_id>/<group_id>.json
```

Typical commands:

```bash
python -m extract run --dir preextract_outputs/my_lease --group rent_review_header
python -m extract run --dir preextract_outputs/my_lease --all
python -m extract pack --dir preextract_outputs/my_lease --group rent_review_header
python -m extract list-groups
python -m extract list-profiles
```

The major modules are:

- `extract/cli.py`: command-line interface.
- `extract/runner.py`: orchestrates one extraction group.
- `extract/artefacts.py`: loads `preextract_outputs/<doc_id>/` artefacts.
- `extract/groups.py`: loads declarative group configs.
- `extract/assembler.py`: builds the evidence pack sent to the model.
- `extract/schemas.py`: builds response schemas and final output envelopes.
- `extract/providers.py`: provider abstraction for Gemini, OpenAI, Anthropic, and fake test provider.
- `extract/resolver.py`: resolves model quotes back to evidence manifest rows.
- `extract/normalizers.py`: deterministic post-processing rules for specific groups.

Extraction groups are declarative JSON files under:

```text
extract/config/groups/
```

Each group config defines:

- fields to extract
- field output formats
- semantic topics to include
- whole structural sections to include
- definitions to include
- system/user prompt templates
- decision rules

Model profiles live in:

```text
extract/config/model_profiles.json
```

Provider environment variables:

- `GEMINI_API_KEY` or `GOOGLE_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

## Extraction Group Flow

For one group, `extract/runner.py` effectively does this:

```text
load preextract artefacts
  -> load group config
  -> assemble evidence pack
  -> build structured response schema
  -> call provider
  -> resolve returned quotes to evidence IDs
  -> apply deterministic normalizers
  -> build final envelope
  -> write <group_id>.json
```

The evidence pack is grouped by source so the model can reason about authority:

1. Definitions
2. Lease summary / particulars
3. Operative text from the current segment
4. Operative text from other segments, such as annexed or old leases

Pack rows look conceptually like:

```text
[p12_item_4_0] p12 | Lease | Rent Review | The yearly rent shall be reviewed...
```

The model does not return `evidence_id` values. It returns short verbatim quotes. The resolver then matches those quotes against the pack rows and attaches real manifest evidence.

This avoids a common failure mode where models produce plausible but wrong IDs.

## Final Extraction Output

Each group writes an envelope like:

```json
{
  "schema_version": "1.0",
  "doc_id": "my_lease",
  "group_id": "rent_review_header",
  "provider": "gemini",
  "model": "gemini-...",
  "input_evidence_ids": ["p12_item_4_0"],
  "validation_summary": {
    "total_fields": 10,
    "found": 7,
    "not_present": 2,
    "unclear": 1,
    "needs_review": 1
  },
  "fields": {
    "review_frequency": {
      "value": "Every five years",
      "status": "found",
      "quotes": ["reviewed on each fifth anniversary"],
      "supporting_evidence": [
        {
          "evidence_id": "p12_item_4_0",
          "text_verbatim": "The yearly rent shall be reviewed on each fifth anniversary...",
          "page_number": 12
        }
      ],
      "needs_review": false
    }
  }
}
```

The key downstream contract is:

```text
field value -> supporting_evidence[].evidence_id -> evidence_manifest row -> PDF bbox
```

That is how the product can show highlights and audit trails.

## The 17 Extraction Groups

The current group configs cover:

- `parties`: landlord, tenant, guarantor, surety.
- `property`: address and demised premises.
- `lease_details`: date, term, commencement, expiry.
- `financials`: rent figures.
- `permitted_use`: permitted use wording/classes.
- `statutory_status`: Landlord and Tenant Act 1954 status.
- `rent_review_header`: core rent review mechanics.
- `omv_disregards`: open market value disregard factors.
- `omv_assumptions`: open market value assumptions.
- `repairs_decorations`: repair and decoration obligations.
- `alterations`: alterations consent mechanics.
- `alienation`: assignment, underletting, sharing, charging.
- `break_rights`: break options.
- `third_party`: expert/arbitrator determination.
- `rights_granted`: parking, loading, access, and similar rights.
- `insurance`: insurance obligations.
- `service_charge`: service charge mechanics.

## Tests

Important tests include:

- `tests/test_definitions.py`: definition term extraction patterns.
- `tests/test_structure_v2.py`: evidence position/span handling.
- `tests/test_sections.py`: semantic section routing.
- `tests/test_schedule_detection.py`: schedule and structure detection using fixtures.
- `tests/test_extract.py`: extraction loader, assembler, resolver, schemas, fake-provider E2E, and group coverage.
- `tests/test_normalizers.py`: deterministic extraction post-rules.

Useful focused commands:

```bash
python -m pytest tests/test_definitions.py
python -m pytest tests/test_sections.py tests/test_structure_v2.py
python -m pytest tests/test_extract.py tests/test_normalizers.py
```

## Known Edges And Gotchas

- `preextract/segments.py` is not the active segmentation path used by `python -m preextract build`; active code uses `structure_v2.py`.
- `extract/config/task_config.json` appears to be a legacy UI/task config artefact rather than part of the current extraction runtime.
- The current structure and topic routing are deterministic heuristics. They are explainable and testable, but unusual lease formats can still route evidence incorrectly.
- Quote resolution is intentionally conservative. If the model gives a quote that cannot be matched back to the pack, the field should need review.
- The repo has both `parse_outputs/` and `preextract_outputs/`. `parse_outputs/` is ingest/raw-parse territory; `preextract_outputs/` is the canonical input to extraction.
- Running `extract --all` with a real provider can make many paid API calls: one per group.
- Presigned image URLs from LlamaParse can expire. Core extraction should rely on manifest text/geometry, not remote URLs.

## Mental Model

Think of the repo as three increasingly semantic layers:

```text
scripts/     = get the PDF into machine-readable parse output
preextract/  = turn parse output into stable legal/evidence structure
extract/     = turn scoped evidence into structured lease facts
```

The invariant across all layers is provenance. Every useful extracted fact should be traceable back to manifest evidence, and every manifest evidence row should be traceable back to page geometry in the original PDF.

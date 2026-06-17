# Extraction group build guide

This document is the reference for adding new LLM extraction groups to the `extract/` package. Read it before writing any code for a new group.

---

## Architecture in one paragraph

The extraction stage consumes a completed `preextract_outputs/<doc_id>/` directory. It assembles a source-labelled evidence pack from preextract artefacts, calls an LLM via a swappable provider, and resolves the model's verbatim quote strings back to manifest evidence rows deterministically. The model never emits `evidence_id` values directly — those are resolved by code after the call, using quote matching against the assembled pack.

---

## What artefacts exist and which to use for what

| Artefact | File | Use for |
|---|---|---|
| `evidence_manifest.json` | Source of truth for all text rows and bboxes | Verbatim text lookup, `text_verbatim` in output, bbox resolution for UI |
| `segment_map.json` | Segment definitions (base_lease, deed_of_variation, etc.) and precedence order | Knowing which segment is "current" vs "old/annexed" |
| `structure_map.json` | Anchors (sections by family+token) and box_assignments (evidence_id → section) | Resolving whole-section evidence (LEASE_PARTICULARS, PRESCRIBED_CLAUSES, etc.) and adding section context to pack rows |
| `definitions_index.json` | Parsed defined terms with their evidence_ids | Pulling specific definitions into the pack via an allowlist |
| `semantic_section_map.json` | Per-segment topic → evidence_id lists (e.g. `rent_review`, `insurance`) | Scoping the pack to the operative clauses relevant to a group |

**Rule:** never dump the full manifest into a prompt. Always scope via topics + whole sections + definitions allowlist.

---

## How groups are organised

Each group is one JSON file in `extract/config/groups/<group_id>.json`.

A group maps to one LLM call — one evidence pack, one structured response.

**When to split into multiple groups:**

- When the fields fall into naturally distinct topic clusters within the same lease section (e.g. `rent_review_header` vs `omv_disregards` vs `omv_assumptions`).
- When you want to use different model profiles for different subsets of fields.
- When the total evidence would exceed ~100 rows (rough guide; depends on model context).

**When to keep fields in one group:**

- When the fields are interdependent (e.g. `basis_of_valuation` and `upwards_only` both depend on reading the same rent review clause).
- When splitting would force duplication of the same evidence block.

---

## Step-by-step: adding a new group

### 1. Identify the fields

Describe each field in plain English first:
- What is the legal question being answered?
- What are the possible answers and their meanings?
- What section of the lease contains the answer?

Add the fields to `docs/fields.md` in a new section before writing any code.

### 2. Identify the evidence sources

For each field group, decide:

**Semantic topics** (`semantic_topics` list):  
Choose from the eight topic slugs: `premises`, `rent_review`, `insurance`, `service_charge`, `repairs_decoration`, `alterations`, `alienation`, `permitted_use`.

These are the topics from `semantic_section_map.json`. Check the map for your document to confirm evidence IDs are non-empty before relying on a topic.

**Whole sections** (`whole_sections` list):  
Specify as `"family:token"` strings. Common values:

| Value | What it covers |
|---|---|
| `front_matter:LEASE_PARTICULARS` | Lease summary / particulars page |
| `front_matter:PRESCRIBED_CLAUSES` | Land Registry prescribed clauses block |
| `front_matter:COVER` | Cover page |

Use whole sections only when those pages contain information the model needs. Most operative-text-only groups (e.g. OMV disregards) should omit whole sections to keep the pack tight.

**Definition allowlist** (`definition_allowlist` list):  
Include defined terms the model needs to interpret the operative clauses correctly. Use the exact capitalised form as it appears in the lease (e.g. `"Open Market Rent"`, `"Review Date"`). The loader normalises to lowercase for matching.

### 3. Write the group config JSON

Create `extract/config/groups/<group_id>.json`.

Required top-level keys:

```json
{
  "group_id":            "my_new_group",
  "label":               "Human-readable group name",
  "description":         "What this group extracts",
  "schema_version":      "1.0",
  "prompt_version":      "1.0",
  "semantic_topics":     ["insurance"],
  "whole_sections":      ["front_matter:LEASE_PARTICULARS"],
  "definition_allowlist": ["Insured Risks", "Insured Risk"],
  "model_profile":       "gemini-3-flash-preview",
  "source_priority":     ["current_lease", "lease_summary", "definitions", "annexed_old_lease"],
  "system_prompt":       "...",
  "user_prompt_template": "...",
  "fields":              [...]
}
```

### 4. Write field decision rules

Every field must have a `decision_rule`. This is the most important thing to get right. Ambiguous rules produce consistently-wrong values.

A good decision rule:
- States what `Yes`/`No`/`N/A` means in legal terms (not just field-label terms).
- Handles the edge case where the answer is ambiguous or the mechanism is absent.
- Specifies what to return when the relevant section is missing.

Bad (too vague):
```json
"decision_rule": "Return Yes if insurance is present."
```

Good:
```json
"decision_rule": "Return 'Yes' if the lease places an obligation on the Tenant to effect insurance. Return 'No' if insurance is the Landlord's obligation. Return 'N/A' if the lease is silent on which party insures."
```

For `yes_no_na` fields, always define all three values:
- `Yes` = what fact is true / present
- `No` = expressly excluded or not stated
- `N/A` = mechanism / section does not apply to this lease

### 5. Write the prompts

Copy the system prompt from an existing group and adapt it. The system prompt should:
- State the source priority order (current_lease > lease_summary > definitions > annexed/old).
- State the output format rules (JSON only, no prose outside schema, short verbatim quotes, no evidence_id values).
- State what each status value means (found / not_present / unclear).

The user prompt template is injected with three template variables:
- `{field_descriptions}` — auto-generated from field schemas
- `{evidence_pack}` — the assembled evidence pack string
- `{json_schema}` — the JSON schema for the response

Keep the user prompt simple and direct. Do not add extra instructions there; put instructions in the system prompt.

### 6. Choose a model profile

Edit `extract/config/model_profiles.json` and add an entry under `group_defaults` if this group should have a specific default:

```json
"group_defaults": {
  "my_new_group": "gemini-3-flash-preview"
}
```

Available profiles: `gemini_fast`, `gemini_pro`, `gemini-3-flash-preview` ([Gemini 3 Flash Preview](https://ai.google.dev/gemini-api/docs/models/gemini-3-flash-preview)), `gpt_strict`, `gpt_mini`, `claude_reasoning`, `claude_fast`, `fake`.

You can override at runtime with:
```bash
python3 -m extract run --dir preextract_outputs/Henry_Unit_2 --group my_new_group --model-profile gpt_strict
```

### 7. Inspect the pack before running the model

Always check the evidence pack before spending API tokens:

```bash
python3 -m extract pack --dir preextract_outputs/Henry_Unit_2 --group my_new_group
```

Verify:
- The correct evidence blocks are present (definitions / lease_summary / operative_current).
- The operative text rows contain the clauses relevant to your fields.
- There are no spurious image rows or unrelated sections.
- The row count is reasonable (20–200 rows; over 300 warrants splitting the group).

### 8. Run with fake provider first

```bash
python3 -m extract run --dir preextract_outputs/Henry_Unit_2 --group my_new_group --model-profile fake
```

This writes a placeholder output to `preextract_outputs/Henry_Unit_2/extractions/<timestamp>/my_new_group.json`. Check:
- The envelope structure is correct.
- All `input_evidence_ids` are in the manifest.
- Assembly warnings are empty or expected.

### 9. Run with a real provider

```bash
# Gemini
export GEMINI_API_KEY=...
python3 -m extract run --dir preextract_outputs/Henry_Unit_2 --group my_new_group

# GPT
export OPENAI_API_KEY=...
python3 -m extract run --dir preextract_outputs/Henry_Unit_2 --group my_new_group --model-profile gpt_strict

# Claude
export ANTHROPIC_API_KEY=...
python3 -m extract run --dir preextract_outputs/Henry_Unit_2 --group my_new_group --model-profile claude_reasoning
```

### 10. Add tests

Add a test class to `tests/test_extract.py` covering:
- The group config loads and all fields have decision rules.
- The pack assembler includes the expected blocks.
- Known quotes from the real lease resolve to supporting_evidence rows.
- End-to-end fake provider run produces a valid envelope.

---

## Output file location

Extraction runs are written to:

```
preextract_outputs/<doc_id>/extractions/<run_id>/<group_id>.json
```

`run_id` defaults to an ISO timestamp (`20260510T093105Z`). Pass `--run-id` to set it explicitly (useful for comparing model outputs across providers).

---

## Output envelope structure

```json
{
  "schema_version": "1.0",
  "doc_id": "Henry_Unit_2",
  "group_id": "rent_review_header",
  "extracted_at": "2026-05-10T09:31:05Z",
  "provider": "gemini",
  "model": "gemini-2.5-flash",
  "model_profile": "gemini_fast",
  "usage": { "input_tokens": 4200, "output_tokens": 512 },
  "input_evidence_ids": ["p33_item_0_0", "p3_item_1_0", "..."],
  "evidence_block_counts": { "definitions": 1, "lease_summary": 20, "operative_current": 62 },
  "assembly_warnings": [],
  "validation_summary": {
    "total_fields": 6,
    "found": 5,
    "not_present": 1,
    "unclear": 0,
    "needs_review": 0
  },
  "fields": {
    "rent_review_first_date": {
      "value": "29/09/2002",
      "status": "found",
      "quotes": ["RENT REVIEW DATE: The 29th day of September in the years 2002"],
      "reasoning": "From lease particulars; first date in the series.",
      "confidence": "high",
      "supporting_evidence": [
        {
          "evidence_id": "p3_item_16_0",
          "text_verbatim": "RENT REVIEW DATE: The 29th day of September in the years 2002 2007 2012 and 2017",
          "page_number": 3,
          "section_title": "Lease Summary / Particulars"
        }
      ],
      "needs_review": false,
      "review_reason": null
    }
  }
}
```

Key points:
- `quotes` = short verbatim phrases from the pack (returned by model).
- `supporting_evidence[].text_verbatim` = full row text from manifest (added by code, never from model).
- `needs_review = true` flags fields where quotes couldn't be resolved to any evidence row.

---

## Anti-patterns

**Never do these:**

- Do not let the model return `evidence_id` values. The model miscopes structured ids. Use quotes only; code resolves the ids.
- Do not dump the entire manifest or all pages into a prompt. Scope via semantic topics and whole sections.
- Do not put bbox coordinates in prompts. Bboxes stay server-side; the UI resolves them from the manifest after extraction.
- Do not write a field without a `decision_rule`. The field label alone is not enough — the model will invent semantics.
- Do not use `N/A` and `blank` interchangeably. `N/A` = mechanism doesn't apply; `blank` / `unclear` = can't determine from evidence.
- Do not create a group that mixes unrelated lease topics (e.g. insurance + alterations). One group should have coherent context.
- Do not make a group so large (>300 rows) that the operative evidence is diluted. Split instead.

---

## Provider-specific notes

**Gemini** — supports native JSON schema mode via `response_mime_type: application/json`. Most reliable for strict enum constraints. Default rent-review groups use profile `gemini-3-flash-preview` (model id `gemini-3-flash-preview`). Alternatives: `gemini_fast` (2.5-flash), `gemini_pro` for heavier reasoning.

**Gemini API key** — set either environment variable before running `extract`:

```bash
export GEMINI_API_KEY="your-key-here"
```

`GOOGLE_API_KEY` is also accepted (same value). Create a key in [Google AI Studio](https://aistudio.google.com/apikey). Do not commit keys; use your shell profile or a secrets manager.

**GPT** — uses `response_format: json_schema` with `strict: True`. Reliable structured output. Use `gpt_strict` (gpt-4.1) for production quality, `gpt_mini` for cost-sensitive runs.

**Claude** — does not have a strict JSON schema mode in all tiers; the provider adapter appends schema instructions to the user prompt and parses the response. Works well but has slightly higher chance of format drift on complex schemas. Prefer `claude_reasoning` (Sonnet) for groups with difficult legal interpretation.

---

## Related docs

- [EXTRACTION_APPROACH.md](EXTRACTION_APPROACH.md) — architectural contract (authoritative spec)
- [fields.md](fields.md) — per-field output formats by section
- [SEMANTIC_TAGGING_REQUIREMENTS.md](SEMANTIC_TAGGING_REQUIREMENTS.md) — what topics cover and tagging algorithm
- [DEFINITIONS_AIM_AND_ISSUES.md](DEFINITIONS_AIM_AND_ISSUES.md) — definitions index scope and known issues

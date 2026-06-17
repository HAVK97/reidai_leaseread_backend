# LLM extraction approach

This document describes how field extraction runs **after** the preextract pipeline (`evidence_manifest`, `segment_map`, `structure_map`, `definitions_index`, `semantic_section_map`). It is the contract for building evidence packs, calling the model, and producing auditable outputs.

Field-level output formats live in [fields.md](fields.md).

---

## Goals

1. **Grounded answers** — every `found` field ties to real manifest rows; no free-floating `evidence_id` strings from the model.
2. **Provenance** — the model knows whether text came from definitions, lease summary, operative clauses, or an annexed/old segment so it can apply priority when sources disagree.
3. **Audit trail** — downstream UI resolves `evidence_id` → bounding boxes via `evidence_manifest.json`.
4. **One group, one call** — each extraction group assembles one evidence pack and one structured response (e.g. all rent review fields together).

---

## Artefacts (inputs)

| Artefact | Role |
|----------|------|
| `evidence_manifest.json` | `evidence_id`, `page_number`, `text`, `raw_bbox` — source of truth for verbatim text and boxes |
| `segment_map.json` | Segments (`seg_1`, `seg_current`, `seg_annexed_old`, …), page ranges, `kind` — **current vs old lease** |
| `structure_map.json` | `anchors_flat`, `box_assignments` — whole sections (e.g. `LEASE_PARTICULARS`, `PRESCRIBED_CLAUSES`) and hierarchy |
| `semantic_section_map.json` | Per-segment topic → `evidence_ids` for the eight semantic topics (e.g. `rent_review`) |
| `definitions_index.json` | Defined terms, spans, `definition_evidence_ids` — term-level truth |

Semantic topic routing is implemented in `preextract/sections.py`; see [SEMANTIC_TAGGING_REQUIREMENTS.md](SEMANTIC_TAGGING_REQUIREMENTS.md).

---

## Extraction groups (config, not code branches)

Each **group** is declarative data, for example:

- **Which semantic topics** to include (`rent_review`, `insurance`, …).
- **Which whole sections** to include (anchor `family` + `token`, e.g. `LEASE_PARTICULARS`, or the full `PRESCRIBED_CLAUSES` block instead of individual LR tokens).
- **Which definitions** to pull (allowlist of terms resolved via `definitions_index.json`).

Example (rent review header fields): lease summary + `rent_review` topic + selected definitions.  
Example (OMV disregards / assumptions): **`rent_review` topic only** — no lease summary, no definitions, unless you explicitly add them later.

An **assembler** loads these rules, resolves anchors to `evidence_id` lists, dedupes, sorts in reading order, and builds the **evidence pack** string sent to the model.

---

## Evidence pack layout (what the model sees)

Do not dump a flat list of rows in arbitrary order. **Group by source** with clear headers so the model can apply priority:

1. **Definitions** — labelled rows (optional per group); used to interpret terms, not as a substitute for operative covenants unless the group is definition-only.
2. **Lease summary / particulars** — e.g. `LEASE_PARTICULARS` when the group config includes it.
3. **Operative text** — semantic topic rows, optionally split by **segment** (`segment_id`, `segment_kind`) so **current vs annexed/old** lease is obvious.

Within each block, each line should lead with **`evidence_id`** and **`page`**, then **verbatim `text`** from the manifest (no paraphrase). Bboxes are **not** inlined in the prompt; the UI resolves `evidence_id` → `raw_bbox` after extraction.

### Default priority when sources conflict (system prompt)

Highest authority first (tune per product if needed):

1. **Current lease** — operative clauses and schedules for the segment that represents the binding lease text.
2. **Lease summary / particulars** — quick reference; if it conflicts with operative text, **prefer operative text**.
3. **Definitions** — interpret defined terms; do not override clear operative wording.
4. **Annexed / old lease segment** — only when incorporated or when no current equivalent exists; if both address the same point, prefer **current** where applicable.

State this priority in the **system** prompt and briefly repeat it in the **user** message so it stays salient.

---

## Model output contract (no probabilistic `evidence_id`)

The model **must not** emit `evidence_id` values. Models miscopy structured ids (`p40_item_3_0` → `p40_item_8_0`) with no detectable signal.

Instead, the model returns per field:

| Output | Purpose |
|--------|---------|
| `value` | Typed answer per [fields.md](fields.md) (`date`, `yes_no`, `yes_no_na`, `enum`, …) |
| `status` | `found` \| `not_present` \| `unclear` — see below |
| `quotes` | Short **verbatim** phrases copied from the evidence pack (8–25 words is a good target); used only for grounding |
| `reasoning` | Short narrative (why this answer; optional for trivial fields) |
| `confidence` | `high` \| `medium` \| `low` — model self-rating; post-rules may downgrade |

**Post-processing (deterministic):**

1. For each quote, match against rows in the assembled pack (normalise whitespace; optional fuzzy threshold, e.g. ≥90%, for punctuation drift).
2. Collect all matching **`evidence_id`**s.
3. **Enrich**: for each id, append **`text_verbatim`** copied **from the manifest** — never trust the model for full-row text.
4. If no quote matches any row → `needs_review`, optional demotion of `status` to `unclear`.

Final per-field shape (conceptual):

```json
{
  "value": "…",
  "status": "found",
  "reasoning": "…",
  "confidence": "high",
  "quotes": ["short verbatim snippet from pack…"],
  "supporting_evidence": [
    { "evidence_id": "p41_item_8_0", "text_verbatim": "<exact manifest text for that row>" },
    { "evidence_id": "p41_item_9_0", "text_verbatim": "<exact manifest text for that row>" }
  ],
  "needs_review": false,
  "review_reason": null
}
```

- **`supporting_evidence`** may list **multiple** rows when the answer is spread across bullets or continuations.
- **`quotes`** stay short for validation; **`text_verbatim`** is the full row text for UI / audit.

### `status` semantics

- **`found`** — answer is supported; require at least one resolvable quote (or stricter rules per field type).
- **`not_present`** — the lease addresses the topic and the answer is “none / not applicable” in a legally meaningful way; quotes may be empty if the group’s policy allows.
- **`unclear`** — insufficient or contradictory evidence in the pack; `supporting_evidence` may be empty after failed resolution.

Align **`N/A`** vs **blank** vs **`unclear`** with product rules (e.g. `N/A` = mechanism does not apply; blank = not identifiable from text).

---

## Structured output

Use the provider’s **structured output** / schema mode (e.g. Pydantic → JSON schema) so every field conforms to types defined per group. Validate after the call: allowed enums, date formats, citation resolution, etc.

---

## Group envelope (saved JSON)

Each run should record metadata for reproducibility:

- `doc_id`, `group`, `schema_version`, `extracted_at`, model id  
- **`input_evidence_ids`** — full list of ids included in the pack (rebuild the same prompt for debugging)  
- **`fields`** — map of field key → final object (after post-processing)  
- Optional: token counts, validation summary (`fields_needs_review`, …)

---

## Summary

| Concern | Approach |
|---------|----------|
| Wrong bbox ids from LLM | Model outputs **quotes** only; **code** resolves → `evidence_id` + **`text_verbatim` from manifest** |
| Current vs old lease | Segment labels + priority rules in prompt; assembler includes segment-scoped rows |
| Definitions vs clauses | Separate blocks + priority; definitions allowlist per group |
| Verbatim display | **`supporting_evidence[].text_verbatim`** always from manifest, not from model |
| Field formats | [fields.md](fields.md) + shared format ids (`yes_no`, `yes_no_na`, `date`, …) |

---

## Related docs

- [fields.md](fields.md) — per-field output formats and section sources per table  
- [SEMANTIC_TAGGING_REQUIREMENTS.md](SEMANTIC_TAGGING_REQUIREMENTS.md) — what semantic topics cover  
- [DEFINITIONS_AIM_AND_ISSUES.md](DEFINITIONS_AIM_AND_ISSUES.md) — definitions index scope and quality  

Implementation (assembler, resolver, CLI) is not in this repo yet; this file is the **spec** those modules should follow.

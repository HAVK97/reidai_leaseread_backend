# Semantic tagging — requirements and reasoning

This document states **what** should be done for semantic topic tagging in the pre-extract pipeline (`preextract/sections.py` and its relationship to `structure_map`). It is meant for future work in a fresh context. **No implementation steps** — only intent and rationale.

---

## What we want done

### 1. Scope of tagging (which structure applies)

Semantic topic tagging must apply **only** to content that structure already classifies as:

- **`numbered_clauses`** — operative numbered clauses, including the **Definitions and Interpretation** block (that block is still stored as its own anchor with token `DEFINITIONS` under this family in `structure_v2`; it is not a separate `family` enum value today).

- **`schedules`** — schedule headings and nested schedule parts (e.g. schedule token + part references).

**Do not** drive topic buckets from:

- **`front_matter`** — cover, table of contents, lease particulars/summary, prescribed-clause *shell* range, floor plans, etc.

- **`lr_clauses`** — individual Land Registry prescribed clause headings (e.g. `LR3`, `LR4`) as standalone tagging hooks.

Those areas are **not** meant to receive semantic topic slices here. They are supplied **as whole sections** to other extraction flows or dedicated pipelines.

### 2. Topic universe (tags)

Only these **eight** semantic topics (field-registry style slugs):

| Slug | Intent |
|------|--------|
| `premises` | Identity of property, parties, demise, plans/areas — **not** generic “premises” noise |
| `rent_review` | Rent review as a whole: headline clause plus OMV assumptions, disregards, third-party determination — **one** topic, not split sub-tags |
| `insurance` | Insurance obligations, premiums, insured risks, related rent loss |
| `service_charge` | Service charge, sinking fund, estate/common expenses |
| `repairs_decoration` | Repair, decoration, reinstatement covenants |
| `alterations` | Alterations and structural/non-structural works |
| `alienation` | Assignment, underletting, alienation covenants |
| `permitted_use` | Permitted use / user clauses |

Keywords and schedule-heading phrases live on each topic in configuration; **LR token lists (`LR3`, `LR4`, etc.) must not** be used for semantic tagging — routing is **keyword / schedule phrase only**.

### 3. How tagging should behave (conceptual algorithm)

**Pass A — parent-first**

- Consider only anchors in **`numbered_clauses`** and **`schedules`** (using the existing hierarchy in `structure_map`: parent/child relationships on anchors).

- When a **parent** schedule or clause **heading** matches a topic (via that topic’s keywords for clauses, or schedule-specific phrases for schedules), treat that match as owning **the entire subtree** under that anchor: all **descendant** clauses/schedules under it inherit the same topic for evidence collection.

- Reason: body text is assigned to **deepest** matching anchors in box assignment; matching only the parent token would otherwise miss almost all substantive lines under child clause numbers.

**Pass B — orphans**

- After Pass A, scan remaining taggable anchors whose evidence was **not** already covered by a parent match.

- If a **child** heading matches a topic but its **parent** did not match that topic, still attach that subtree to the topic (typically at **lower confidence** than a parent match).

- Reason: nested numbering sometimes puts the “right” heading only on a sub-clause; parents can be generic.

### 4. Definitions and interpretation

- **Structural reality:** In `structure_v2`, Definitions is a **separate anchor** (`token="DEFINITIONS"`, title “Definitions and Interpretation”) but **`family="numbered_clauses"`** — so it is intentionally one “clause-family” bucket for hierarchy and assignment, while still identifiable by token.

- **`definitions_index`** remains the **source of truth** for parsed defined terms and interpretation rows (spans, normalization, segment).

- **`semantic_section_map`** today also fills **`definition_terms`** per topic by overlapping definition **normalized terms** with topic **keywords** — that is **hint metadata**, not the primary way clause-level **`evidence_ids`** are chosen.

**Desired alignment:** semantic topic **`evidence_ids`** should still come from **structure / subtree rules** above; definitions-specific extraction should continue to lean on **`definitions_index`**. Whether every topic should **exclude** the raw DEFINITIONS block from shared `evidence_ids` (and rely only on `definition_terms` + definitions pipeline) is a **product choice** — if excluded, implement an explicit rule for token `DEFINITIONS` rather than overloading `numbered_clauses` semantics.

### 5. What stays out of this layer

- No semantic topic tagging driven by prescribed-clause **LR labels** (`lr_tokens` removed from topic config).

- No reliance on cover, TOC, plans, or LR shell ranges for topic slicing — those remain **whole-document sections** for other extractors.

---

## Reasoning (why)

1. **Precision vs noise:** Limiting tagging to **numbered clauses + schedules** avoids slicing front matter and LR headings into product topics where those regions are either navigational or already handled elsewhere as full payloads.

2. **Subtree inheritance:** Topic headings often sit on **parent** clauses; substantive text sits under **child** clause numbers. Without expanding matches to **descendants** in the anchor tree, topic `evidence_ids` would be mostly empty or heading-only.

3. **Second pass for orphans:** Some leases only expose the relevant heading on a **child**; a strict parent-only rule would miss those. Orphan pass recovers recall without throwing away parent-first discipline.

4. **Single `rent_review` topic:** OMV assumptions, disregards, and third-party mechanics are legally one narrative cluster; splitting them into separate semantic tags duplicated routing and fragile boundary rules.

5. **No LR tokens for topics:** LR numbering is registry-centric and uneven across forms; keyword/schedule alignment tracks **what the clause actually says**, which matches extractor intent better than token IDs.

6. **Definitions split:** **`definitions_index`** is optimized for **term-level** truth; **`semantic_section_map`** is optimized for **topic-scoped clause evidence**. Keeping definitions parsing in the index while optionally excluding the DEFINITIONS block from generic topic bags avoids stuffing definition boilerplate into every insurance/rent-review slice unless intended.

---

## Related artefacts (for orientation)

| Artefact | Role |
|----------|------|
| `structure_map.json` | Anchors, `anchors_flat`, hierarchy, `box_assignments` |
| `definitions_index.json` | Parsed definitions / interpretation rows |
| `semantic_section_map.json` | Per-segment, per-topic `evidence_ids`, `definition_terms`, confidence, warnings |

---

## Non-goals for this document

- No step-by-step implementation or file-by-file refactor plan.
- No prescription of exact threshold numbers beyond “parent match stronger than orphan match.”

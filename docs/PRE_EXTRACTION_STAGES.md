# Stages before extraction

This file answers **what** happens before field extraction and **why** each stage exists. It does not prescribe a single implementation path.

For **approaches** (parsers, sectioning options, extractors, trade-offs), see [`PRODUCT_DESCRIPTION.md`](./PRODUCT_DESCRIPTION.md).

---

## Why order matters

Extraction needs **scoped text** plus **stable citations** to PDF regions. If you extract straight from raw parse JSON or one giant markdown dump, you get weaker accuracy and highlights that do not line up with evidence IDs.

Rough pipeline:

```text
parse → evidence → [segments → structure] → sections → extraction
```

Neo4j and external enrichment come **after** extraction is stable enough to load — see `PRODUCT_DESCRIPTION.md`.

---

## 1. Parse

**What:** Turn the PDF into structured output (text, layout, tables, headings, optional images metadata).

**Why:** You need machine-readable content and geometry before you can assign stable IDs or route by clause/topic.

---

## 2. Evidence manifest

**What:** A flat list (or table) of **evidence units**: each row has a stable `evidence_id`, page, text (or preview), and bounding box info aligned to your viewer.

**Why:** Extractors should cite **`evidence_id`s**, not coordinates they invent. The manifest is the lookup from **id → text + geometry** for highlights and audit.

---

## 3. Segment map (when the PDF is not one clean lease)

**What:** Split the file into **document parts** (e.g. update pages, deed of variation, base lease, schedules) and define **precedence** so later instruments can override earlier text.

**Why:** Amended or bundled packs contradict a simple “read top to bottom” extraction; without segments you mix competing facts (e.g. old rent vs varied rent).

---

## 4. Structure map

**What:** A deterministic **skeleton** inside each segment: clause numbers, LR clauses, contents, definitions block, schedules, tables, plans — the shape of the document.

**Why:** Topic routing and evidence grouping need anchors (headings, clause ranges). Contents pages help **navigation**, not always primary evidence for final values.

---

## 5. Definitions index (supporting layer)

**What:** Index defined terms (term text → `evidence_id`s) so you can attach relevant definitions to later extractions without pasting the entire definitions section every time.

**Why:** Many fields depend on defined terms; pulling the right definitions improves precision and reduces hallucination.

---

## 6. Semantic section map (topic routing)

**What:** Map product topics (e.g. insurance, rent review, alienation) to **sets of `evidence_id`s**, usually scoped by segment and structure.

**Why:** Each extractor run should see **only relevant context** plus an **allowed evidence ID list**, improving accuracy and citation quality.

---

## 7. Extraction contract (before you call the model)

**What:** Locked **field registry** (`field_id`s), JSON schema per field group, rules that returned `evidence_id`s must come from the allowed list, and how **supersession** is represented when a variation overrides the base lease.

**Why:** Without a contract, outputs are inconsistent and hard to merge with highlights or a graph later.

---

## Then: extraction

Run extractors against **scoped evidence + allowed IDs + schema**. Approaches (LlamaExtract, Gemini, hybrid) and provenance patterns are in [`PRODUCT_DESCRIPTION.md`](./PRODUCT_DESCRIPTION.md).

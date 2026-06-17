# ReidAI — Product Description & Approach

## Vision

ReidAI is a **document intelligence system for commercial leases (and similar long-form contracts)**. It turns PDFs into **structured, auditable facts** that can be queried, visualised, and linked to external data — not just “LLM summaries.”

Core promises:

1. **Provenance first** — Every extracted value can be traced to **specific regions on the PDF** (bounding boxes), so users see *where* an answer came from.
2. **Structured extraction at scale** — Dozens of domain fields (parties, term, rent, insurance, alienation, etc.) with a **stable field registry**, not ad-hoc prompts per report.
3. **Knowledge graph** — Facts and entities become a **Neo4j** graph: parties, property, obligations, and how they connect across documents. The graph is **enriched** with external data (e.g. **EPCs** for UK property context) where entity resolution allows.
4. **Section-aware quality** — Long leases are **sectioned** (by headings / structure / routing) so extractors see **targeted context**, improving accuracy over full-document single-shot extraction.

---

## What the user sees

- Upload or select a lease PDF.
- The system **parses** the document (layout + OCR), **extracts** many lease fields, and **highlights** the exact passages and regions that support each value.
- Optional: explore **relationships** in a graph (landlord, property, key dates, covenants) and **enriched** attributes (e.g. energy performance where matched).

---

## System architecture (high level)

```text
                    ┌─────────────────────────────────────────┐
                    │           PDF (lease / bundle)             │
                    └─────────────────────┬───────────────────┘
                                          │
                    ┌─────────────────────▼───────────────────┐
                    │  Parse (LlamaParse agentic, etc.)        │
                    │  • Text, tables, headings, figures       │
                    │  • Geometry: items + optional image crops │
                    └─────────────────────┬───────────────────┘
                                          │
                    ┌─────────────────────▼───────────────────┐
                    │  Evidence manifest                        │
                    │  • Stable evidence_id per spatial unit    │
                    │  • Join images (items ↔ asset URLs)      │
                    │  • Single coordinate policy for UI        │
                    └─────────────────────┬───────────────────┘
                                          │
          ┌───────────────────────────────┼───────────────────────────────┐
          │                               │                               │
          ▼                               ▼                               ▼
┌─────────────────┐             ┌─────────────────┐             ┌─────────────────┐
│ Section map     │             │ Field registry │             │ Neo4j loader    │
│ (split / route) │────────────▶│ 80+ extractions│────────────▶│ Facts + entities│
└─────────────────┘             └────────┬────────┘             └────────┬────────┘
                                         │                               │
                                         │ each fact                     │
                                         │ SUPPORTED_BY                  │
                                         ▼                               ▼
                               ┌──────────────────┐            ┌──────────────────┐
                               │ Evidence nodes   │            │ EPC / external   │
                               │ (shared by many  │            │ enrichment       │
                               │  facts)          │            │ (entity match)   │
                               └──────────────────┘            └──────────────────┘
```

---

## Key concepts

### Parse layer

- **Purpose:** OCR, reading order, tables, headings, embedded figures — and **geometry** for UI transparency.
- **Typical stack:** LlamaCloud **Parse** at **agentic** (or **agentic_plus** for harder scans), with `expand` including at least **`items`** (structured tree + bbox arrays), **`markdown`**, and **`images_content_metadata`** when figures/screenshots matter.
- **Note:** Overlapping boxes in Parse output are **expected** (table shell vs cell vs span). The product does not rely on Parse to “merge” overlaps into one rectangle; **policy + evidence manifest** handle that.

### Evidence manifest

The raw LlamaParse response is **nested** (`items` tree, overlapping boxes). An **evidence manifest** is your **derived layer**: a table (or JSON array) where **each row is one evidence unit** with:

| Concept | Meaning |
|--------|---------|
| **`evidence_id`** | Stable id you assign (e.g. `p6_item_14_1`) — this becomes the join key to UI and Neo4j. |
| **Text** | Snippet from `value` / `md` / table cell text for that item or bbox span. |
| **Geometry** | `raw_bbox` / page coords + **pixels** aligned to your viewer raster. |
| **Provenance** | Optional pointer back into parse (page index, item path). |

**Why it exists:** extraction outputs must cite **`evidence_id`**, not raw coordinates the model invents. The graph stores **`Fact → SUPPORTED_BY → Evidence`** using those ids.

#### What you actually give the LLM at extraction time

The manifest is **not** usually pasted wholesale into every prompt. Typical patterns:

1. **Section-scoped text + constrained citations (recommended)**  
   - Build the section slice from **`markdown`** or concatenated **`items`** text for pages / headings that belong to “Insurance”, “Rent review”, etc.  
   - Attach a **numbered list** or JSON subset of manifest rows **only for lines included in that slice** (`evidence_id`, `text_preview`, page).  
   - Instruct the model: **answer fields using only this context**, and return **`evidence_ids`** (and optionally verbatim quotes) **chosen from that list**.  
   - **Geometry stays server-side:** after the model returns ids, you attach **bbox** from the manifest for highlights.

2. **LlamaExtract-style service**  
   You may send **document + schema + page_range / chunk options**; the service parses internally. You still **map answers back** to your manifest (quotes → fuzzy match → `evidence_id`) unless the product returns native citations tied to parse units.

3. **Gemini / chat LLM**  
   Same as (1): provide **bounded context** + **allowed evidence id list** so citations are **grounded**. Avoid “here is 200 pages of markdown” without ids — highlights will not line up.

**You do not need** to stream every bbox to the model. The model needs **text** for semantics; your app needs **ids → bbox** for UI.

### Overlapping and multiple bounding boxes

- **Overlaps are normal:** Parse emits **container** boxes (e.g. whole table) and **inner** boxes (cell, span). Same region can have several rectangles with different `label`s (`table`, `text`, `paragraph_title`). The service does **not** collapse these into a single “official” box for you.
- **One extracted field → many boxes:** A single answer can cite **`evidence_ids: ["e-12-01", "e-12-02"]`** (e.g. definition in one clause + amount in a table). The UI draws **multiple** highlights or a merged hull — product decision.
- **Policy:** Define rules for display (e.g. prefer **leaf** / smallest area, or prefer `label: text` over `label: table` for prose fields). Store **all** cited ids on the fact for audit.

### Section map (“split” in the product sense)

- **Purpose:** Bound **insurance**, **rent review**, **alienation**, etc. so each extractor sees **relevant context** only — better accuracy than one prompt over the whole lease.
- **Implementation options** (see next major section): use **LlamaCloud Split**, or a **local section map** built from parse output and heuristics (conceptually similar to a structural map built from an existing lease JSON: headings, clause tokens, schedules — without relying on that legacy pipeline for production parse).

### Extractions (~80 fields)

- **Purpose:** Fill a **field registry** (stable `field_id`s): property address, demised premises, parties, term, rent, insurance sub-fields, rent review, service charge, alterations, alienation, permitted use, etc.
- **Provenance rule:** Each extracted value links to **one or more `evidence_id`s** (primary / supporting). That is how bounding boxes attach to answers in the UI and in Neo4j.

### Knowledge graph (Neo4j)

- **Entities:** e.g. `Lease`, `Party`, `Property`, `Obligation` — normalized from extracted text, not one node type per field name.
- **Facts:** `ExtractedFact` / `Observation` nodes with `{field_id, value, confidence, extractor_version}` and **`SUPPORTED_BY` → Evidence**.
- **Sharing:** The same **Evidence** node can link to **multiple** facts (same paragraph proves several fields).

### External enrichment (e.g. EPC)

- **Purpose:** Attach **non-lease** facts (energy certificates) to **`Property`** (or equivalent) when **entity resolution** succeeds (UPRN, normalized address, etc.).
- **Caveat:** Matching is **fuzzy**; store match confidence and method on the relationship.

---

## Approaches: sectioning (splitting)

Two families of solution — pick by **document shape**.

### A. LlamaCloud Split (vendor API)

- **What it does:** Treats the PDF as a sequence of **pages**, classifies each page into **categories you define** with natural-language descriptions, then groups **consecutive** pages into **segments** with page ranges and confidence.
- **Best for:** **Concatenated bundles** (multiple unrelated PDFs stapled together), or collections where “document type” maps cleanly to **page runs**.
- **Limits:** For a **single continuous lease**, internal sections (Insurance vs Rent review) are often **not** aligned with page boundaries; Split alone may not replace **clause-level** routing.

### B. Local section map (structure-style, from parse output)

- **What it does:** After LlamaParse, you build a **section index** without a separate Split API: walk **`items`** (headings, `type`, hierarchy) and/or **`markdown`**, apply **rules** (LR clauses, “Schedule”, “Part”, keywords like “Insurance”, “Rent Review”), optionally add a **small LLM pass** that outputs section boundaries as **page ranges and/or evidence_id ranges**.
- **Best for:** **One lease**, fine-grained **chapter / schedule** routing — same role as a legacy **structural map** built from extracted elements (anchors, clause families), but **sourced from LlamaParse output** instead of a prior custom CV pipeline.
- **Output:** Something like `{ "insurance": { "pages": [...], "evidence_ids": [...] }, ... }` used to **slice context** before extraction.

**Hybrid:** Split **first** if the file might be a bundle; then run a **local section map** **inside** each segment.

---

## Approaches: extraction engine

### LlamaExtract (LlamaCloud)

- Schema-first extraction (JSON Schema / Pydantic), **targets** such as per-document / per-page / per-row depending on configuration, optional citations and confidence depending on tier.
- **Fit:** Production extraction with **stable schemas** per field group (Insurance, Rent review, …); less glue than rolling your own prompts for every group.

### Gemini 3 Flash (or similar general LLM)

- **Fit:** Fast iteration, **section-router** prompts (“list main sections and page spans”), or **field fills** when you pass **scoped text + allowed evidence ids**.
- **Trade-off:** You enforce **JSON schema**, **evidence_id** return format, and **quote matching** yourself; provenance is only as good as your **manifest join**.

**Hybrid:** Router / ambiguous sections with Gemini; **LlamaExtract** for high-stakes tables; or the reverse — team preference and evals decide.

---

## Recommended baseline (provenance-safe)

| Stage | Approach |
|-------|----------|
| Parse | LlamaParse **agentic**, pinned options; `items` + `markdown` + `images_content_metadata` when figures matter |
| Manifest | Derive rows from **`items`** (per bbox dict) + **image assets** joined by `filename` |
| Sections | **Local section map** from parse/heuristics (+ optional LLM outline); use **Llama Split** only for **bundled** PDFs |
| Extract | **LlamaExtract** and/or **Gemini**: scoped context per section + schema per group; outputs must reference **`evidence_id`** from the allowed list |
| Graph | Load **Evidence** once; **Fact** nodes link with **SUPPORTED_BY**; normalize **Party / Property** |

### Variants

| Variant | Idea |
|---------|------|
| **Fast prototype** | Markdown slices to Gemini without ids first; **retro-match** quotes → manifest (drift risk). |
| **Hard scans / exhibits** | **agentic_plus**, multimodal extract, or **page crops** as evidence units for signatures and plans. |

---

## Clean build route before Neo4j

Before building the Neo4j layer, get the document pipeline stable in this order:

```text
raw parse JSON
  -> evidence_manifest.json
  -> segment_map.json
  -> structure_map.json
  -> section_map.json
  -> extraction_results.json
  -> Neo4j load later
```

### 1. Parse

Run LlamaParse and save the raw response without trying to make it the product data model.

Inputs:

- PDF

Outputs:

- `llamaparse_raw_result.json`
- `items`
- `markdown`
- `metadata`
- `images_content_metadata`

The raw parse is the source material. It is nested, vendor-shaped, and may contain overlapping boxes. Keep it for audit/debugging, but do not make downstream extractors depend directly on its full shape.

### 2. Evidence manifest

Build a flat evidence manifest from the parse output. This is the stable lookup table between text and PDF geometry.

Each row should represent one usable evidence unit:

```json
{
  "evidence_id": "p6_item_14_0",
  "page_number": 6,
  "text": "The yearly rent shall be £120,000 exclusive of VAT...",
  "text_preview": "The yearly rent shall be £120,000...",
  "type": "text",
  "label": "text",
  "raw_bbox": { "x": 95.8, "y": 625.4, "w": 361.9, "h": 31.9 },
  "normalized": { "x": 0.16, "y": 0.74, "w": 0.61, "h": 0.04 },
  "pixels": { "x1": 213, "y1": 1390, "x2": 1018, "y2": 1461 },
  "parse_path": "items.pages[5].items[14].bbox[0]"
}
```

Important distinction:

- The **`evidence_id`** is only a stable key.
- The **bounding boxes live on the manifest row** (`raw_bbox`, `normalized`, `pixels`).
- Extractors cite `evidence_id`s; the app resolves those ids back to boxes for highlights.

The evidence manifest answers:

```text
What exact text / bbox units exist in the document?
```

### 3. Segment map

Build a segment map before sectioning. Many lease PDFs are not one clean lease; they may contain update pages, deeds of variation, counterparts, supplemental agreements, old/base lease text, schedules, plans, and execution pages in one file.

The segment map answers:

```text
What document parts are inside this PDF, and which parts take precedence?
```

Example:

```json
{
  "segments": [
    {
      "segment_id": "seg_1",
      "kind": "front_update_pages",
      "title": "Update / cover pages",
      "pages": [1, 2],
      "evidence_ids": ["p1_item_0_0", "p1_item_1_0"],
      "priority": "high"
    },
    {
      "segment_id": "seg_2",
      "kind": "deed_of_variation",
      "title": "Deed of Variation",
      "pages": [3, 8],
      "evidence_ids": ["p3_item_0_0"],
      "priority": "highest"
    },
    {
      "segment_id": "seg_3",
      "kind": "base_lease",
      "title": "Original Lease",
      "pages": [9, 84],
      "evidence_ids": ["p9_item_0_0"],
      "priority": "base"
    }
  ]
}
```

Use deterministic signals first (`deed of variation`, `supplemental lease`, `counterpart`, `licence`, `memorandum`, `lease`, `underlease`, `LR1`, `prescribed clauses`, `contents`) and an LLM only to classify uncertain candidate segments.

Extraction precedence should generally be:

```text
variation / update pages
  -> lease particulars / prescribed clauses
  -> main lease body
  -> schedules
  -> contents as navigation only
```

Base lease facts can still fill gaps, but later variations or update pages can supersede them.

### 4. Structure map

Build a deterministic document skeleton inside each segment. This is the `structure.py`-style layer: headings, clause numbers, LR clauses, contents pages, definitions, schedules, tables, plans, and box/evidence assignments.

The structure map answers:

```text
What is the shape of each document segment?
```

Example structural families:

- `front_matter`
- `contents`
- `definitions`
- `numbered_clauses`
- `lr_clauses`
- `schedules`
- `parts`
- `tables`
- `plans`

This should be mostly rules-based. Use contents pages as navigation hints, not as final evidence for extracted lease facts. Treat definitions as global context that can support many later extractions.

### 5. Section map

Build a local section map over the evidence manifest. A section is not evidence itself; it is a routing layer that says which evidence units belong to a lease topic.

Example:

```json
{
  "rent_review": {
    "title": "Rent Review",
    "pages": [18, 19, 20, 21],
    "start_evidence_id": "p18_item_22_0",
    "end_evidence_id": "p21_item_9_0",
    "evidence_ids": ["p18_item_22_0", "p18_item_23_0"],
    "confidence": 0.93,
    "method": "heading_match"
  },
  "insurance": {
    "title": "Insurance",
    "pages": [34, 35, 36, 37],
    "start_evidence_id": "p34_item_8_0",
    "end_evidence_id": "p37_item_42_0",
    "evidence_ids": ["p34_item_8_0", "p34_item_9_0"],
    "confidence": 0.88,
    "method": "heading_keyword_and_clause_range"
  }
}
```

The section map answers:

```text
Which evidence units belong to Insurance, Rent Review, Alienation, etc.?
```

Best route for sectioning:

1. Detect headings and clause anchors from `items` / `markdown`.
2. Route headings and nearby text using known lease domain keywords.
3. Expand from a matched heading until the next comparable heading.
4. If no clean heading exists, fallback to keyword hits plus nearby evidence windows.
5. Optionally use an LLM only to verify/rank candidate sections, not to invent unsupported boundaries from the whole PDF.

Use **LlamaCloud Split** only when the upload may be a bundle of separate documents. For a single lease, internal topics usually start and stop at clause/schedule level, not clean page boundaries.

### 6. Extractions

Run field extraction against scoped section context, not the whole raw document.

For each field group:

- Select the relevant section from `section_map.json`.
- Send the extractor the text for only those evidence rows.
- Include the allowed `evidence_id` list.
- Require output values to cite one or more ids from that list.

Example extraction result:

```json
{
  "field_id": "annual_rent.original_rent",
  "value": "£120,000 per annum",
  "confidence": 0.91,
  "evidence_ids": ["p6_item_14_0"]
}
```

The extraction layer answers:

```text
What structured lease facts can be extracted, and which evidence proves each fact?
```

For amended or updated lease packs, extraction should preserve both the current value and the superseded/base evidence where relevant:

```json
{
  "field_id": "annual_rent.current_rent",
  "value": "£120,000 per annum",
  "source_status": "varied",
  "evidence_ids": ["p4_item_8_0"],
  "supersedes_evidence_ids": ["p31_item_12_0"]
}
```

### 7. Neo4j later

Only load Neo4j after the previous files are stable:

- `evidence_manifest.json`
- `segment_map.json`
- `structure_map.json`
- `section_map.json`
- `extraction_results.json`

Neo4j should load evidence once, then create fact/entity nodes that link back to evidence:

```text
ExtractedFact -> SUPPORTED_BY -> Evidence
```

This keeps the graph auditable instead of becoming another place where unsupported summaries live.

---

## Current pipeline outputs (parse track — where we are now)

The **`ingest_pipeline_v3`** notebook path produces artefacts under `llamaparse_v3_outputs/` (paths may vary by run):

### 1. Raw parse JSON (`llamaparse_raw_result.json`)

Top-level shape (conceptual):

```json
{
  "job": {
    "id": "string",
    "status": "COMPLETED",
    "name": "filename.pdf",
    "tier": "agentic"
  },
  "images_content_metadata": {
    "images": [
      {
        "filename": "page_4_table_1_v2.jpg",
        "index": 4,
        "bbox": { "x": 0, "y": 0, "w": 595, "h": 842 },
        "category": "screenshot | layout",
        "presigned_url": "https://..."
      }
    ],
    "total_count": 42
  },
  "items": {
    "pages": [
      {
        "page_number": 1,
        "page_width": 595.276,
        "page_height": 842.0,
        "success": true,
        "items": [
          {
            "type": "text",
            "value": "...",
            "md": "...",
            "bbox": [
              {
                "x": 95.8,
                "y": 625.4,
                "w": 361.9,
                "h": 31.9,
                "label": "text",
                "confidence": 0.83,
                "start_index": 0,
                "end_index": 73
              }
            ]
          }
        ]
      }
    ]
  },
  "markdown": { "pages": [ { "page_number": 1, "markdown": "..." } ] },
  "metadata": { "pages": [ ... ] }
}
```

- **`items.pages[].items[]`:** Nested tree (`heading`, `text`, `table`, `image`, `footer`, …). Tables may include `rows`, `html`, `md`, `parse_concerns`. **`bbox`** is often an **array** of rectangles (multi-span / multi-level).
- **`images_content_metadata`:** Per-asset **`presigned_url`**, **`category`** (`screenshot` = full page, `layout` = crop), and **`bbox`** for that asset on the page.

### 2. Flat bbox export (`llamaparse_bboxes.json`)

An array of records derived from **`items`** (and optionally layout image metadata), for overlays and future **`evidence_id`** alignment:

```json
[
  {
    "id": "p1_item_0_0",
    "page_number": 1,
    "source": "items",
    "label": "text",
    "type": "text",
    "confidence": 0.96,
    "text_preview": "Dated [25 January 2022]",
    "raw_bbox": {
      "x": 251.44,
      "y": 114.68,
      "w": 72.85,
      "h": 17.10,
      "label": "text",
      "start_index": 0,
      "end_index": 27
    },
    "normalized": { "x": 0.42, "y": 0.14, "w": 0.12, "h": 0.02 },
    "pixels": { "x1": 559, "y1": 255, "x2": 721, "y2": 293 }
  }
]
```

**Not built yet on this track:** section map JSON, per-field extraction JSON, or Neo4j load files — those layers sit **after** manifest hardening and extractor choice.

---

## Out of scope / follow-ons (explicit)

- **Cross-outs, handwritten amendments, tracked-changes semantics** — Parse may surface text or figure regions; **full legal redline reasoning** may need dedicated VLM / linking stages (similar in spirit to a richer legacy pipeline).
- **Cell-perfect table-to-bbox** for every cell — often harder than paragraph-level evidence; may use table-level + row text match first.

---

## Success criteria (product-level)

- User can click a field and see **correct** highlights for **most** core lease fields on representative PDFs.
- Graph queries return **consistent** parties/property and **traceable** facts to evidence.
- EPC (or other enrichments) attach only with **documented** match quality.

---

## Phase 1 — extraction field catalog (first ship)

These groups and labels come from the lease summary structure (`print_lease_summary_structure.py` output). Each line is a **field slot** for extraction; implement **stable `field_id`s** from the slug / registry name (right-hand side where shown). Every extracted value links to **≥ one `evidence_id`** (bounding-box provenance).

### Property & Parties

- Property Address  
- Demised Premises  

### Additional Demised / Access Rights

- Vehicle  
- Bicycle  
- Motorbike  
- Fire Exit  
- Loading/Unloading  
- Lease Date  
- Original Lessor  
- Current Lessor  
- Original Lessee  
- Current Lessee  
- Guarantor — Guarantor Name  
- Surety — Surety Name  

### Lease fundamentals

- Term  
- Expires — Expiry Date  
- Annual Rent — Original Rent  
- L&T Act 1954  
- Break Options — Tenant Break Conditions  

### Repair & decoration

#### Repairs

- Internal — Internal Repair  
- External — External Repair  

#### Decoration

- Internal — Internal Decoration  
- External — External Decoration  
- Service Charge  

### Insurance & other covenants

- Building — Responsibilities  
- Plate Glass — Responsibilities  
- Engineering — Responsibilities  
- Third Party Liability  
- Loss of Rent  
- Period  
- Cessor of Rent  
- Policy Effected By  
- Policy Paid By — Premium paid by  

### Rent review

- Date — Rent Review Dates  
- Frequency — Rent Review Frequency  
- Time of Essence  
- Notice Provisions — Notice of Provisions  
- Basis of Valuation  
- Upwards Only  

### Open market value — disregards

- Occupation — Open Market Value  
- Goodwill — Open Market Value  
- Improvements — Open Market Value  
- Licenses  
- Reinstatement  
- Rent Free — Open Market Value  

### Open market value — assumptions

- As Whole  
- In Part  
- Without Fine/Premium  
- Vacant Possession — Open Market Value  
- Fit For Immediate Occupation & Use  
- Term — Open Market Value  
- Lease Terms  
- Willing Parties — Open Market Value  

### Third party

- Third Party Determination  
- Application by: — Party Entitled to Appoint Independent Expert  
- Memorandum: — Expert Determination Memorandum Requirement  
- Interest on Backdated Increase: — Interest on Backdated Increase  

### Service charge

- Sinking Fund  
- On Account  
- Adhoc — Ad-Hoc  

### Items covered

- Building  
- Building Services  
- Common Parts of the Building  
- Common Parts of the Estate — Common Parts Of Estate  
- Budget Process  
- Reconciliation Process  

### Alterations

- External — External Alterations  
- Internal — Internal Alterations  
- Structural — Structural Alterations  
- Non-Structural — Non-Structural Alterations  
- Demountable Partitions — Demountable Partition Alterations  
- Electrics — Electrics Alterations  
- Conduits/Services — Conduits/Services Alterations  

### Alienation

- Assign Whole  
- Sublet Whole  
- Sublet Parts  

### Permitted use

- Permitted Use  

---

## Document history

- Initial version: captures ReidAI direction — parse → evidence manifest → section-aware extraction → Neo4j + enrichment — as discussed for lease intelligence use cases.
- Added Phase 1 extraction field catalog (lease summary structure / `stuart_v2`-style registry).
- Expanded: sectioning options (**Llama Split** vs **local section map** / structure-style routing), extraction engines (**LlamaExtract** vs **Gemini 3 Flash**), evidence manifest + **what is sent to the LLM**, **multiple / overlapping** boxes, and **current v3 JSON output shapes** (`llamaparse_raw_result.json`, `llamaparse_bboxes.json`).

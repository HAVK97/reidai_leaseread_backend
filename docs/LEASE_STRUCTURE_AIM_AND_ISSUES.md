# Lease Structure Aim and Current Issues

## What You Are Trying To Achieve

You are building a **pre-extraction pipeline** for commercial leases where the output is not just "parsed text", but a set of **trustworthy structural artifacts** that let extraction run with the right legal context.

Your core intent is:

1. Split each lease pack into the correct logical parts (including amended/composite packs).
2. Build a section map that reflects how a human would read the legal document.
3. Route extraction fields to the right section bundles (with full surrounding context, not isolated lines).
4. Keep provenance strict so every extracted value can be traced back to evidence ids and page geometry.

Extraction quality depends on this stage being right. If structure is wrong, downstream extraction is wrong even if the model is good.

## The Target Sectioning Model (Your Desired Shape)

For a typical lease, you want structure to resemble:

- Cover / front page
- Printed TOC (navigation only, not legal body)
- Lease summary / LR / prescribed clauses area
- Definitions / interpretation
- Main lease body with parent and child clauses
- Schedules
- Plans / floorplans
- Variations / deeds / counterparts (as separate high-precedence segments when present)

In short: **usable legal sections, not parser artifacts**.

## Non-Negotiable Invariants

- **Provenance-first**: every routed chunk and extracted field must map to stable `evidence_id` entries and underlying bbox/page.
- **Context-complete grouping**: field extraction must receive enough adjacent legal context (not only heading titles).
- **Composite-pack aware**: variations/counterparts must override base lease content by precedence.
- **Human-auditable output**: TOC-style output should be quick to inspect and obviously correct.

## What Is Working

- Pipeline stages exist end-to-end (`parse -> manifest -> segment_map -> structure_map -> definitions -> semantic sections`).
- Evidence manifest and schema validations are in place.
- Structure generation is producing clause/schedule trees and TOC-style lines.
- Segment precedence framework exists.

## Current Problems We Have (Observed)

### 1) Duplicate / Conflicting Outputs

There are two output layouts in use:

- `preextract_outputs/<lease>/structure_map.json` (legacy/stale in many cases)
- `preextract_outputs/<lease>/<lease>/structure_map.json` (new build output)

This causes contradictory TOC views and confusion about which result is authoritative.

### 2) TOC / Navigation Leakage Still Appears In Some Documents

Some leases still show TOC-like entries leaking into structural clauses, especially where dense list/table regions are parsed as headings.

### 3) Duplicate Anchors In TOC Lines

Repeated lines still occur (for example duplicate LR rows and duplicate schedule lines with equivalent page ranges).

### 4) Segment Boundaries Still Miss Certain Restarts

In some packs (especially counterparts/amendment-style bundles), clause numbering restarts are still appearing inside a single segment instead of creating/confirming a new segment boundary.

### 5) Cover Detection Is Better But Still Noisy

Some outputs create multiple `COVER` ranges in one segment. This is closer to desired behavior than before, but still over-sensitive.

### 6) Schedule/Internal Restart Noise Not Fully Eliminated

Phantom clause restarts are reduced but still occur in difficult packs with repeated inserted blocks.

## Why This Matters For Extraction

If structure is imperfect:

- fields can be sent to the wrong legal scope,
- variation clauses may not override base text correctly,
- definitions context can be incomplete,
- extraction prompts become brittle and inconsistent across leases.

Your requirement is not "roughly right headings"; it is **reliable legal routing for field extraction**.

## Definition Of "Good" (Acceptance Criteria)

A lease is considered correctly pre-structured when:

1. Printed TOC is present only as navigation (never treated as legal body clauses).
2. Front matter, main body, schedules, plans, and amendments are clearly separated.
3. Clause restarts only occur where they truly belong (new sub-document or valid schedule logic).
4. Duplicate structural lines are removed from the final TOC view.
5. Page ranges shown to the reviewer are absolute and understandable.
6. Output path and naming are unambiguous (single canonical artifact location per run).

## Immediate Priorities

1. Enforce one canonical output path and deprecate stale duplicate layout.
2. Add stronger de-duplication in TOC line emission (token + normalized title + near-range merge).
3. Tighten segment boundary logic for counterpart/variation restarts.
4. Harden TOC leakage filter for table/reference-heavy pages.
5. Merge contiguous/near-contiguous cover detections into one cover section unless a true new sub-doc starts.

## Strategic Direction

The right direction is exactly what you are aiming for:

- keep deterministic structure passes for consistency and auditability,
- but calibrate them against real lease examples until sectioning is stable,
- then route extraction by these trusted section bundles with provenance-backed evidence packs.

This gives you the accuracy and explainability needed for production legal extraction.


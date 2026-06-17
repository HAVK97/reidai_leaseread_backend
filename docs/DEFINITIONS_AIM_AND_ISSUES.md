# Definitions Extraction Aim and Current Issues

## Current Aim

The definitions stage should produce a **pure legal definitions index** that is:

- Segment-aware (`seg_current`, `seg_annexed_old`, etc.).
- Verbatim and evidence-backed (every term maps to exact `evidence_id` rows).
- Bounded to the true definitions/interpretation scope.
- Safe for downstream topic tagging without semantic pollution.

In practice, `definitions_index.json` should contain only true defined terms from the definitions section(s), with stable spans.

## Why This Aim Matters

If definitions are over-broad or mixed with non-definition semantics, downstream extraction quality drops:

- Wrong legal context gets attached to terms.
- Topic routing becomes noisy (false support evidence).
- Current-vs-old lease provenance is harder to trust.
- Auditing becomes slow because reviewers cannot tell what is definition text vs inferred semantic aliasing.

The definitions artifact is intended to be a precision layer, not a catch-all semantic index.

## Relevant Files

- `preextract/definitions.py` — current definitions term detection and span logic.
- `preextract/structure_v2.py` — section assignment and hierarchy that definitions relies on.
- `preextract/sections.py` — downstream topic routing consuming definition terms.
- `preextract_outputs/smoke_v2/definitions_index.json` — active test output (Picton sample).
- `preextract_outputs/counterpart_smoke_v2/definitions_index.json` — active test output (counterpart sample).
- `preextract_outputs/smoke_v2/structure_map.json` — assignment/hierarchy ground truth for debugging span boundaries.
- `preextract_outputs/smoke_v2/evidence_manifest.json` — raw evidence rows and reading-order truth.

## Issues Observed

### 1) Term span bleed into interpretation/new clauses

Some terms (notably `Working Day`) can over-extend beyond their intended definition line/body and absorb unrelated rows.

Observed pattern:

- term starts correctly in definitions block,
- continuation logic runs until next term-like match,
- if no next term appears quickly, span can drift into `1.2` interpretation bullets or later clause content.

This causes inflated `definition_evidence_ids` and incorrect legal scope.

### 2) Continuation handling must include true multi-line bodies

Other terms (for example `Basic Rent`) legitimately continue across multiple rows `(a)`, `(b)` and sometimes page breaks.

So the system must avoid two opposite failures:

- under-capture (term heading only),
- over-capture (entire following clause content).

### 3) Colon-style definitions are common

Counterpart/new lease samples often use:

- `Annual Rent: ...`
- `Contractual Term: ...`

not only `"Term" means ...`.

Without colon-style patterns, valid terms are missed.

### 4) Definitions purity vs semantic mixing

Historically, LR semantic aliases were injected into definitions output (`source_pattern = lr_clause`), mixing two concerns.

Current direction is to keep definitions pure and handle semantic/tag layers separately.

## Current Direction (Target Behavior)

1. Extract terms from definitions section only.
2. Support both `means` and `colon` term styles.
3. Capture deterministic continuation spans for true term bodies.
4. Enforce hard stops on boundary transitions:
   - next term start,
   - subclause/top-level clause transition (e.g. from `1.1` to `1.2` or `3.`),
   - section assignment leaving definitions scope.
5. Keep output segment-scoped for current-vs-old lease separation.
6. Keep definitions artifact pure; apply topic tags afterward.

## Reasoning for This Direction

This gives the best trade-off for legal extraction:

- deterministic and auditable,
- high precision on what a definition actually is,
- resilient across lease styles (`means` vs `colon`),
- clean inputs for later topic tagging and hierarchy-based inheritance.

In short: **definitions first, tags second** is the correct architecture for reliability.


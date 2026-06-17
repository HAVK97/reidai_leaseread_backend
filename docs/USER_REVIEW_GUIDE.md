# When users should review extraction output

This document defines **when a human should check a field** after a group extraction run. It is aimed at product/UI implementers consuming group JSON envelopes (e.g. `alterations.json` under `preextract_outputs/.../extractions/`).

For how fields are produced and post-processed, see [EXTRACTION_APPROACH.md](EXTRACTION_APPROACH.md). For resolver mechanics, see `extract/resolver.py`.

---

## Two different problems

Extraction output carries signals for two separate concerns:

| Concern | Question | Primary signals |
|--------|----------|-----------------|
| **Grounding / audit** | Can we trust that citations resolve to real manifest rows and the right instrument? | `needs_review`, `review_reason`, `supporting_evidence` |
| **Substantive answer** | Did the model actually answer from the lease text? | `status`, `confidence`, `value` |

`needs_review` alone is **not** enough to drive “user must check this field.” It only flags mechanical failures after quote resolution. A field can be substantively uncertain (`status: unclear`) while `needs_review` stays `false` (e.g. empty quotes, nothing to resolve).

---

## Recommended rule: `needs_user_check`

Compute a single UI flag per field:

```text
needs_user_check =
  needs_review
  OR status == "unclear"
  OR (status == "found" AND confidence == "low")
```

Optional stricter variant for `found` fields (more prompts, fewer missed inferences):

```text
needs_user_check =
  needs_review
  OR status == "unclear"
  OR (status == "found" AND confidence IN ("low", "medium"))
```

**Do not** flag on confidence when `status == "not_present"` unless `needs_review` is true — absent-topic answers with no quotes are often valid.

**Do not** require confidence when `status == "unclear"` — status alone is sufficient (confidence is usually already `low`).

---

## Signal reference

### `needs_review` (set in code, not by the model)

Set in `QuoteResolver.resolve_field()` when:

1. **`status == "found"` but no `supporting_evidence`** — model claimed found but no quote matched any evidence pack row (or quotes were empty).
2. **Unresolved quotes** — at least one quote string could not be matched to a pack row (after normalisation, adjacent-span, and fuzzy matching).
3. **Wrong instrument** — field resolved from annexed/old lease (or similar) evidence while current-instrument rows were present in the pack; user should verify the binding lease does not address the point.

When `needs_review` is set and `confidence` was `high`, post-processing downgrades it to `medium`.

Always show `review_reason` when `needs_review` is true.

### `status` (from the model)

| Value | Meaning | Typical user action |
|-------|---------|---------------------|
| `found` | Answer supported from the pack | Check if `confidence` is low (or medium, if using the stricter rule) |
| `not_present` | Topic addressed; answer is legally “none / N/A” | Usually trust; only escalate if `needs_review` |
| `unclear` | Insufficient or ambiguous evidence in the pack | **Always** prompt user to fill in or verify manually |

### `confidence` (from the model; may be downgraded by resolver)

Only use **`confidence` when `status == "found"`**:

| Value | Suggested UI |
|-------|----------------|
| `high` | No extra prompt unless `needs_review` |
| `medium` | Optional “verify” (use if you want to catch inferred answers, e.g. structural from “non-structural only” wording) |
| `low` | **Always** prompt user to verify the answer and citations |

---

## Envelope fields (group JSON)

Per field (under `fields.<field_id>`):

- `needs_review` — boolean, grounding/audit
- `review_reason` — string or null; human-readable why `needs_review` was set
- `status` — `found` | `not_present` | `unclear`
- `confidence` — `high` | `medium` | `low`
- `value`, `quotes`, `reasoning`, `supporting_evidence` — display and audit

Group-level summary in `validation_summary`:

- `needs_review` — count of fields with `needs_review: true` (grounding only)
- `unclear` — count of fields with `status: unclear`

For product banners, prefer a derived count, e.g. fields where `needs_user_check` is true, rather than only `validation_summary.needs_review`.

---

## Examples

### Must check: unclear (no `needs_review`)

```json
{
  "value": "",
  "status": "unclear",
  "quotes": [],
  "confidence": "low",
  "supporting_evidence": [],
  "needs_review": false
}
```

User should supply or confirm the answer; grounding did not fail — there was nothing to ground.

### Must check: grounding failure

```json
{
  "status": "found",
  "quotes": ["text that does not appear in any evidence row"],
  "needs_review": true,
  "review_reason": "Unresolved quotes: [...]"
}
```

User should re-read the lease and fix quotes/citations even if the `value` looks plausible.

### Must check: low confidence on found

```json
{
  "status": "found",
  "confidence": "low",
  "needs_review": false,
  "supporting_evidence": [{ "evidence_id": "..." }]
}
```

Citations resolved; user should still sanity-check the interpretation.

### Usually OK without extra prompt

```json
{
  "status": "found",
  "confidence": "high",
  "needs_review": false,
  "supporting_evidence": [{ "evidence_id": "..." }]
}
```

```json
{
  "status": "not_present",
  "confidence": "high",
  "quotes": [],
  "needs_review": false
}
```

---

## UI copy (suggested)

| Condition | Short label |
|-----------|-------------|
| `needs_review` | Verify citation / source |
| `status == "unclear"` | Not found in lease — confirm manually |
| `found` + `confidence == "low"` | Low confidence — please verify |
| `found` + `confidence == "medium"` (optional) | Review suggested |

You can show one combined badge (“Needs your review”) when `needs_user_check` is true, and a tooltip with the specific reason from the table above plus `review_reason` when present.

---

## Known gaps (implementation)

- **Ambiguous quotes** (matched multiple sections): resolver may set `review_reason` without setting `needs_review` — treat ambiguous `review_reason` as check-worthy until fixed in `extract/resolver.py`.
- **`needs_review` is not persisted as a combined “user check” flag** — UI should derive `needs_user_check` client-side or add it in a future envelope version.

---

## Related

- [EXTRACTION_APPROACH.md](EXTRACTION_APPROACH.md) — pipeline, `status` semantics, post-processing
- [EXTRACTION_GROUP_BUILD_GUIDE.md](EXTRACTION_GROUP_BUILD_GUIDE.md) — output shape examples
- `extract/resolver.py` — when `needs_review` is set
- `extract/schemas.py` — `validation_summary` counts

# Extraction routing (Google OCR vs VLM)

## Google Cloud Vision (Document Text Detection)

**Primary path** for these Heron layout labels:

- `Text`, `Section-header`, `Title`, `List-item`, `Caption`, `Formula`
- **Any** other label that is not handled by a dedicated VLM path (catch-all) also uses the same Google path.

Crops are **padded and upscaled** before the API call. On success, the result is the final string.

**Fallback chain (still “VLM”, not raw Google):** if Google returns no/empty text, or a **confidence anomaly** (word-level dip), or the **semantic audit** flags the string, the crop is re-run with the **forensic VLM** (same model as other VLM steps). So “Google” in the JSON `source` field means clean pass; forensic rescues appear with a VLM source in code.

## VLM (vision model on the crop)

Used **directly** for:

| Label / unit | VLM role |
|--------------|----------|
| `Handwriting` | Forensic transcription (ignore cross-outs). |
| `Table` | Structured JSON: `headers` + `rows` (ignore cross-outs). |
| `Key-Value Region` | Transcribe what you see (ignore cross-outs). |
| `Form` | Same transcribe-style prompt as Key-Value Region. |
| `Floorplan` | Narrative description of the plan. |
| **KV-Group** (horizontal group from layout, not the `Key-Value Region` box) | If **all** group members are printed labels above **and** there is no `Handwriting` in the group → **Google OCR per key/value box** with the same fallback chain. Otherwise → **single VLM** on the merged crop with JSON `{ "key": "...", "value": "..." }`. |
| **Merge-group** | If any member is `Handwriting` → forensic VLM on merged box. Else → Google + fallback chain on merged box. |

## No OCR / no VLM

- `Checkbox-Selected` / `Checkbox-Unselected`: output `checked: true/false` only (no API call).

## JSON shape notes

- **KV-Group** (`type: "kv_group"`) is the **relationship** from horizontal grouping — different from a Heron **`Key-Value Region`** bounding box (`type: "kv_region"`). Keep both concepts distinct in the output.
- Tables: VLM returns **rows** in structured form; bounding boxes are still one box per table region (not one per row). **Multi-page tables** (split across pages) are not merged yet — treat as a follow-up when you have page order and continuation heuristics.

## Cross-outs

Whenever a VLM is used, prompts require **final text only** — **ignore crossed-out** content.

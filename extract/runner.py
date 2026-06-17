"""Prompt runner — orchestrates one extraction group run end-to-end.

Steps:
1. Load artefacts from preextract output dir.
2. Assemble evidence pack.
3. Build response schema + field descriptions.
4. Build prompts from group templates.
5. Call LLM via provider.
6. Resolve quotes → supporting_evidence from manifest.
7. Build final group envelope and write to disk.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .artefacts import ArtefactLoader
from .assembler import EvidencePackAssembler
from .groups import GroupConfig, get_model_profile, list_available_groups, load_group_config
from .providers import (
    GeminiProvider,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    _strip_unsupported_json_schema_for_gemini,
    build_provider_from_profile,
)
from .normalizers import (
    build_empty_pack_field_results,
    build_omv_not_applicable_field_results,
    normalize_alterations_fields,
    normalize_insurance_fields,
    normalize_rent_review_header_fields,
    normalize_third_party_fields,
)
from .resolver import QuoteResolver, resolve_all_fields
from .schemas import (
    build_field_descriptions,
    build_group_envelope,
    build_response_schema,
)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class PromptRunner:
    """Run a single extraction group against a document directory."""

    def __init__(
        self,
        loader: ArtefactLoader,
        provider: LLMProvider,
    ) -> None:
        self.loader = loader
        self.provider = provider
        self.assembler = EvidencePackAssembler(loader)

    def run(
        self,
        group: GroupConfig,
        dry_run: bool = False,
    ) -> dict:
        """Execute extraction for one group. Returns the final envelope dict.

        Args:
            group:    GroupConfig to run.
            dry_run:  If True, build the pack and schema but skip the LLM call,
                      returning a placeholder envelope.
        """
        # Step 1: assemble evidence pack
        pack_text, pack_meta = self.assembler.build(group)

        if dry_run:
            return {
                "dry_run": True,
                "doc_id": self.loader.doc_id,
                "group_id": group.group_id,
                "input_evidence_ids": pack_meta.input_evidence_ids,
                "evidence_block_counts": pack_meta.block_counts,
                "assembly_warnings": pack_meta.warnings,
                "pack_text": pack_text,
            }

        if not pack_meta.input_evidence_ids:
            field_results = build_empty_pack_field_results(group)
            return build_group_envelope(
                doc_id=self.loader.doc_id,
                group_id=group.group_id,
                group_schema_version=group.schema_version,
                prompt_version=group.prompt_version,
                extracted_at=datetime.now(timezone.utc).isoformat(),
                provider=self.provider.provider_name,
                model=self.provider.model,
                model_profile=group.model_profile,
                usage={"input_tokens": 0, "output_tokens": 0},
                input_evidence_ids=pack_meta.input_evidence_ids,
                block_counts=pack_meta.block_counts,
                warnings=pack_meta.warnings,
                fields=field_results,
                empty_pack_short_circuit=True,
            )

        # Step 2: build schema and descriptions
        response_schema = build_response_schema(group)
        field_descriptions = build_field_descriptions(group)

        # Gemini user-facing schema must match API schema (blank enums → sentinel).
        prompt_schema = (
            _strip_unsupported_json_schema_for_gemini(response_schema)
            if isinstance(self.provider, GeminiProvider)
            else response_schema
        )

        # Step 3: render prompts from templates
        user_prompt = group.user_prompt_template.format(
            field_descriptions=field_descriptions,
            evidence_pack=pack_text,
            json_schema=json.dumps(prompt_schema, indent=2),
        )

        # Step 4: call model
        request = LLMRequest(
            system_prompt=group.system_prompt,
            user_prompt=user_prompt,
            json_schema=response_schema,
            model=self.provider.model,
            group_id=group.group_id,
            prompt_id=f"{group.group_id}_v{group.prompt_version}",
            prompt_version=group.prompt_version,
        )
        response: LLMResponse = self.provider.structured_completion(request)

        if response.error:
            print(f"  [WARN] Provider error for {group.group_id}: {response.error}")

        # Step 5: resolve quotes → supporting_evidence
        # Reconstruct PackRow objects from the pack_meta ids (avoids re-assembling the pack)
        pack_rows_list = _rows_from_meta(pack_meta, self.loader)

        resolver = QuoteResolver(
            loader=self.loader,
            pack_rows=pack_rows_list,
            current_segment_id=self.loader.get_current_segment_id(),
        )
        field_ids = [fs.field_id for fs in group.fields]
        field_results = resolve_all_fields(
            model_response=response.content,
            resolver=resolver,
            group_field_ids=field_ids,
        )

        if group.group_id == "insurance":
            normalize_insurance_fields(field_results)

        if group.group_id == "rent_review_header":
            normalize_rent_review_header_fields(field_results)

        if group.group_id == "third_party":
            normalize_third_party_fields(field_results, block_counts=pack_meta.block_counts)

        if group.group_id == "alterations":
            normalize_alterations_fields(field_results, block_counts=pack_meta.block_counts)

        # Step 6: build envelope
        envelope = build_group_envelope(
            doc_id=self.loader.doc_id,
            group_id=group.group_id,
            group_schema_version=group.schema_version,
            prompt_version=group.prompt_version,
            extracted_at=datetime.now(timezone.utc).isoformat(),
            provider=self.provider.provider_name,
            model=self.provider.model,
            model_profile=group.model_profile,
            usage=response.usage,
            input_evidence_ids=pack_meta.input_evidence_ids,
            block_counts=pack_meta.block_counts,
            warnings=pack_meta.warnings,
            fields=field_results,
        )

        return envelope


def _rows_from_meta(pack_meta, loader: ArtefactLoader):
    """Reconstruct minimal PackRow-like objects from pack_meta for the resolver."""
    from .assembler import PackRow, render_pack_row_text

    manifest_index = loader.build_evidence_index()
    box_index = loader.build_structure_box_index()

    rows = []
    for eid in pack_meta.input_evidence_ids:
        ev = manifest_index.get(eid, {})
        ba = box_index.get(eid, {})
        row = PackRow(
            evidence_id=eid,
            page_number=ev.get("page_number", 0),
            section_token=ba.get("section_token"),
            section_title=ba.get("section_title"),
            segment_id=ba.get("segment_id"),
            segment_kind=ba.get("segment_kind"),
            source_block=pack_meta.source_map.get(eid, "operative_current"),
            text=ev.get("text", ""),
        )
        rows.append(row._replace(text=render_pack_row_text(row)))
    return rows


# ---------------------------------------------------------------------------
# High-level convenience functions
# ---------------------------------------------------------------------------


OMV_DEPENDENT_GROUP_IDS = {"omv_disregards", "omv_assumptions"}
OMV_BASIS_FIELD_ID = "rent_review_basis_of_valuation"
OMV_BASIS_VALUE = "Open Market Value"


def is_open_market_value_rent_review(rent_review_envelope: dict | None) -> bool:
    """True only when rent_review_header found an Open Market Value basis."""
    if not rent_review_envelope:
        return False
    field = (rent_review_envelope.get("fields") or {}).get(OMV_BASIS_FIELD_ID) or {}
    return field.get("value") == OMV_BASIS_VALUE and field.get("status") == "found"


def _write_envelope(envelope: dict, output_dir: Path, group_id: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{group_id}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(envelope, f, indent=2, ensure_ascii=False)
    print(f"  wrote {out_path}")


def _omv_short_circuit_envelope(
    *,
    loader: ArtefactLoader,
    group: GroupConfig,
    basis_value: str,
) -> dict:
    field_results = build_omv_not_applicable_field_results(group)
    envelope = build_group_envelope(
        doc_id=loader.doc_id,
        group_id=group.group_id,
        group_schema_version=group.schema_version,
        prompt_version=group.prompt_version,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        provider="deterministic",
        model="omv-short-circuit",
        model_profile="omv-short-circuit",
        usage={"input_tokens": 0, "output_tokens": 0},
        input_evidence_ids=[],
        block_counts={},
        warnings=[],
        fields=field_results,
    )
    envelope["omv_short_circuit"] = True
    envelope["omv_short_circuit_reason"] = (
        f"{OMV_BASIS_FIELD_ID} is {basis_value!r}, not {OMV_BASIS_VALUE!r}."
    )
    return envelope


def run_group(
    *,
    preextract_dir: Path,
    group_id: str,
    model_profile_override: str | None = None,
    canned_response: dict | None = None,
    dry_run: bool = False,
    output_dir: Path | None = None,
    run_id: str | None = None,
) -> dict:
    """Load everything from disk, run one group, save output, return envelope.

    Args:
        preextract_dir:        Path to preextract_outputs/<doc_id>/ directory.
        group_id:              Group id (must match a JSON in config/groups/).
        model_profile_override: Override the group's default model profile.
        canned_response:       For testing — feed a fake model response.
        dry_run:               Build pack only, no model call.
        output_dir:            Where to write the output JSON (default: inside preextract_dir).
        run_id:                Unique run identifier (default: ISO timestamp).
    """
    loader = ArtefactLoader.from_dir(preextract_dir)
    group = load_group_config(group_id)

    profile_name = model_profile_override or group.model_profile
    profile = get_model_profile(profile_name)
    group.model_profile = profile_name  # record what was actually used

    provider = build_provider_from_profile(profile, canned_response=canned_response)

    runner = PromptRunner(loader=loader, provider=provider)
    envelope = runner.run(group, dry_run=dry_run)

    if dry_run:
        return envelope

    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if output_dir is None:
        output_dir = preextract_dir / "extractions" / run_id
    _write_envelope(envelope, output_dir, group_id)

    return envelope


def run_all_groups(
    *,
    preextract_dir: Path,
    group_ids: list[str] | None = None,
    model_profile_override: str | None = None,
    model_overrides: dict[str, str] | None = None,
    dry_run: bool = False,
    output_dir: Path | None = None,
    run_id: str | None = None,
    canned_response: dict | None = None,
    on_group_start: Callable[[str, int, int], None] | None = None,
    on_group_complete: Callable[[str, dict, int, int], None] | None = None,
) -> list[dict]:
    """Run all requested groups, short-circuiting OMV groups unless basis is OMV."""
    group_ids = group_ids or list_available_groups()
    model_overrides = model_overrides or {}
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_dir or preextract_dir / "extractions" / run_id

    loader = ArtefactLoader.from_dir(preextract_dir)
    rent_review_envelope: dict | None = None
    envelopes: list[dict] = []

    for index, group_id in enumerate(group_ids, start=1):
        if on_group_start:
            on_group_start(group_id, index, len(group_ids))

        if (
            group_id in OMV_DEPENDENT_GROUP_IDS
            and not dry_run
            and not is_open_market_value_rent_review(rent_review_envelope)
        ):
            group = load_group_config(group_id)
            basis_field = (
                (rent_review_envelope or {}).get("fields", {}).get(OMV_BASIS_FIELD_ID, {})
            )
            basis_value = basis_field.get("value") or "not found"
            envelope = _omv_short_circuit_envelope(
                loader=loader,
                group=group,
                basis_value=basis_value,
            )
            _write_envelope(envelope, output_dir, group_id)
        else:
            profile_override = (
                model_overrides.get(group_id)
                or model_profile_override
            )
            envelope = run_group(
                preextract_dir=preextract_dir,
                group_id=group_id,
                model_profile_override=profile_override,
                canned_response=canned_response,
                dry_run=dry_run,
                output_dir=output_dir,
                run_id=run_id,
            )

        envelopes.append(envelope)
        if group_id == "rent_review_header":
            rent_review_envelope = envelope
        if on_group_complete:
            on_group_complete(group_id, envelope, index, len(group_ids))

    return envelopes

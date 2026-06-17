"""Tests for the extract package.

Run with:
  python3 -m pytest tests/test_extract.py -v

All tests use the real preextract_outputs/Henry_Unit_2/ fixture and a fake
provider — no paid API calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HENRY_DIR = Path(__file__).parent.parent / "preextract_outputs" / "Henry_Unit_2"
COUNTERPART_DIR = Path(__file__).parent.parent / "preextract_outputs" / "Henry_Counterpart_Lease_20.06.2017"
LANCASTER_DIR = Path(__file__).parent.parent / "preextract_outputs" / "Henry_Lease_dated_25.01.2022_Lancaster"
PICTON_DIR = Path(__file__).parent.parent / "preextract_outputs" / "Henry_Picton_with_plans_signed_by_DLAP_(UK)_LLP"
Q_GLAZING_DIR = Path(__file__).parent.parent / "preextract_outputs" / "Henry_Lease_Q_Glazing_to_Automerc"
GROUPS = ["parties", "property", "lease_details", "financials", "permitted_use", "statutory_status", "rent_review_header", "omv_disregards", "omv_assumptions", "repairs_decorations", "alterations", "alienation", "break_rights", "third_party", "rights_granted", "insurance", "service_charge"]


# ---------------------------------------------------------------------------
# ArtefactLoader
# ---------------------------------------------------------------------------


class TestArtefactLoader:
    def setup_method(self):
        from extract.artefacts import ArtefactLoader
        self.loader = ArtefactLoader.from_dir(HENRY_DIR)

    def test_doc_id(self):
        assert self.loader.doc_id == "Henry_Unit_2"

    def test_manifest_loads(self):
        m = self.loader.manifest
        assert "evidence" in m
        assert len(m["evidence"]) > 0

    def test_evidence_index(self):
        idx = self.loader.build_evidence_index()
        # Known rows from Henry_Unit_2
        assert "p3_item_1_0" in idx
        assert "p30_item_1_0" in idx

    def test_get_evidence_row(self):
        row = self.loader.get_evidence_row("p3_item_16_0")
        assert row is not None
        assert "RENT REVIEW DATE" in row.get("text", "")

    def test_rent_review_topic_evidence_ids(self):
        eids = self.loader.get_topic_evidence_ids("rent_review")
        assert len(eids) > 10
        # All returned ids should be in manifest
        idx = self.loader.build_evidence_index()
        for eid in eids:
            assert eid in idx, f"topic eid not in manifest: {eid}"

    def test_lease_particulars_section_ids(self):
        eids = self.loader.get_section_evidence_ids("front_matter", "LEASE_PARTICULARS")
        assert len(eids) > 0

    def test_whole_section_includes_lr_descendants(self):
        from extract.artefacts import ArtefactLoader

        loader = ArtefactLoader.from_dir(PICTON_DIR)
        eids = loader.get_section_evidence_ids("front_matter", "PRESCRIBED_CLAUSES")
        texts = [
            (loader.get_evidence_row(eid) or {}).get("text", "")
            for eid in eids
        ]

        assert any("LR3 Parties to this lease" in text for text in texts)
        assert any("Surety None" in text for text in texts)

    def test_get_current_segment_id(self):
        seg_id = self.loader.get_current_segment_id()
        assert seg_id is not None

    def test_definitions_for_terms(self):
        defns = self.loader.get_definitions_for_terms(["review date"])
        assert len(defns) >= 1
        found_terms = [d["normalized_term"] for d in defns]
        assert "review date" in found_terms

    def test_definitions_for_terms_strips_leading_the(self):
        from extract.artefacts import ArtefactLoader

        assert ArtefactLoader.normalize_definition_term("The Initial Rent") == "initial rent"
        assert ArtefactLoader.normalize_definition_term("the initial rent") == "initial rent"

    def test_structure_box_index(self):
        idx = self.loader.build_structure_box_index()
        # Should have thousands of assignments
        assert len(idx) > 100

    def test_no_missing_artefact_raises(self):
        from extract.artefacts import ArtefactLoader
        with pytest.raises(FileNotFoundError):
            ArtefactLoader.from_dir(Path("/nonexistent/path")).manifest


# ---------------------------------------------------------------------------
# EvidencePackAssembler
# ---------------------------------------------------------------------------


class TestEvidencePackAssembler:
    def setup_method(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        self.loader = ArtefactLoader.from_dir(HENRY_DIR)
        self.assembler = EvidencePackAssembler(self.loader)
        self.group = load_group_config("rent_review_header")

    def test_pack_builds(self):
        pack_text, meta = self.assembler.build(self.group)
        assert pack_text
        assert len(meta.input_evidence_ids) > 0

    def test_pack_has_all_blocks(self):
        pack_text, meta = self.assembler.build(self.group)
        # Headers are segment-aware: multi-segment packs use LEASE / ANNEXED LEASE prefixes.
        has_definitions = "DEFINITIONS" in pack_text
        has_summary = "LEASE SUMMARY" in pack_text or "PARTICULARS" in pack_text
        has_operative = "OPERATIVE TEXT" in pack_text
        assert has_definitions, "No definitions block in pack"
        assert has_summary, "No lease summary block in pack"
        assert has_operative, "No operative text block in pack"

    def test_no_bbox_in_pack(self):
        pack_text, _ = self.assembler.build(self.group)
        # Bboxes should never be in the pack
        assert "raw_bbox" not in pack_text
        assert '"x":' not in pack_text

    def test_pack_evidence_ids_in_manifest(self):
        pack_text, meta = self.assembler.build(self.group)
        manifest_idx = self.loader.build_evidence_index()
        for eid in meta.input_evidence_ids:
            assert eid in manifest_idx, f"Pack eid not in manifest: {eid}"

    def test_no_image_ids_in_pack(self):
        _, meta = self.assembler.build(self.group)
        for eid in meta.input_evidence_ids:
            assert "layout_image" not in eid, f"Image eid leaked into pack: {eid}"

    def test_lease_particulars_in_pack(self):
        pack_text, _ = self.assembler.build(self.group)
        # Rent review date should appear in particulars
        assert "RENT REVIEW DATE" in pack_text or "Rent Review Date" in pack_text

    def test_lancaster_property_schedule_in_pack(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        loader = ArtefactLoader.from_dir(LANCASTER_DIR)
        assembler = EvidencePackAssembler(loader)
        group = load_group_config("property")
        pack_text, meta = assembler.build(group)

        assert "p29_item_2_0" in meta.input_evidence_ids
        assert "p29_item_3_0" in meta.input_evidence_ids
        assert "shown edged red on Plan 1" in pack_text
        assert "excludes Conduits that do not serve it exclusively" in pack_text

    def test_picton_parties_pack_includes_surety_none(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        loader = ArtefactLoader.from_dir(PICTON_DIR)
        assembler = EvidencePackAssembler(loader)
        group = load_group_config("parties")
        pack_text, _ = assembler.build(group)

        assert "LR3 Parties to this lease" in pack_text
        assert "Surety None" in pack_text
        assert "SCHEDULE 8: SURETY'S COVENANTS" in pack_text

    def test_counterpart_financials_pack_includes_annual_rent(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        loader = ArtefactLoader.from_dir(COUNTERPART_DIR)
        assembler = EvidencePackAssembler(loader)
        group = load_group_config("financials")
        pack_text, meta = assembler.build(group)

        assert meta.input_evidence_ids
        assert "p5_item_13_0" in meta.input_evidence_ids
        assert "33,000" in pack_text or "33000" in pack_text.replace(",", "")

    def test_q_glazing_financials_pack_includes_initial_rent(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        loader = ArtefactLoader.from_dir(Q_GLAZING_DIR)
        assembler = EvidencePackAssembler(loader)
        group = load_group_config("financials")
        pack_text, meta = assembler.build(group)

        assert meta.input_evidence_ids
        assert "27,500" in pack_text or "27500" in pack_text.replace(",", "")

    def test_q_glazing_break_rights_pack_includes_break_clause(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        loader = ArtefactLoader.from_dir(Q_GLAZING_DIR)
        assembler = EvidencePackAssembler(loader)
        group = load_group_config("break_rights")
        pack_text, meta = assembler.build(group)

        assert meta.input_evidence_ids
        assert "six months" in pack_text.lower() or "6 months" in pack_text.lower()
        assert "break" in pack_text.lower()

    def test_lancaster_break_rights_pack_includes_fee_and_repay(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        loader = ArtefactLoader.from_dir(LANCASTER_DIR)
        assembler = EvidencePackAssembler(loader)
        group = load_group_config("break_rights")
        pack_text, meta = assembler.build(group)

        assert meta.input_evidence_ids
        assert "12,150" in pack_text or "12150" in pack_text.replace(",", "")
        assert "repay" in pack_text.lower()

    def test_rent_review_operative_text_in_pack(self):
        pack_text, _ = self.assembler.build(self.group)
        # Core rent review clause text should be present
        assert "RENT REVIEW" in pack_text.upper()

    def test_no_duplicate_ids(self):
        _, meta = self.assembler.build(self.group)
        assert len(meta.input_evidence_ids) == len(set(meta.input_evidence_ids))

    def test_omv_disregards_no_lease_summary(self):
        from extract.groups import load_group_config
        group = load_group_config("omv_disregards")
        pack_text, meta = self.assembler.build(group)
        # omv_disregards has no whole_sections, so no LEASE SUMMARY block
        assert "# LEASE SUMMARY / PARTICULARS" not in pack_text
        assert "# OPERATIVE TEXT — lease" in pack_text

    def test_source_map_coverage(self):
        _, meta = self.assembler.build(self.group)
        for eid in meta.input_evidence_ids:
            assert eid in meta.source_map


class TestEvidencePackAssemblerQGlazing:
    @pytest.fixture(autouse=True)
    def setup(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        if not Q_GLAZING_DIR.is_dir():
            pytest.skip("Q Glazing preextract fixture missing")
        self.loader = ArtefactLoader.from_dir(Q_GLAZING_DIR)
        self.assembler = EvidencePackAssembler(self.loader)
        self.group = load_group_config("rent_review_header")

    def test_rent_review_schedule_body_in_pack(self):
        pack_text, meta = self.assembler.build(self.group)
        assert "whichever shall be the greater" in pack_text
        assert "open market" in pack_text.lower()
        rent_review_eids = set(self.loader.get_topic_evidence_ids("rent_review"))
        assert "p42_item_4_0" in rent_review_eids
        assert "First Review Date" in pack_text or "27" in pack_text
        assert meta.input_evidence_ids


# ---------------------------------------------------------------------------
# QuoteResolver
# ---------------------------------------------------------------------------


class TestQuoteResolver:
    def setup_method(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config
        from extract.resolver import QuoteResolver
        from extract.runner import _rows_from_meta

        self.loader = ArtefactLoader.from_dir(HENRY_DIR)
        self.group = load_group_config("rent_review_header")
        assembler = EvidencePackAssembler(self.loader)
        _, meta = assembler.build(self.group)
        self.pack_rows = _rows_from_meta(meta, self.loader)
        self.resolver = QuoteResolver(self.loader, self.pack_rows)

    def test_exact_quote_resolves(self):
        # This text appears verbatim in the Henry_Unit_2 lease particulars
        result = self.resolver.resolve_field("rent_review_first_date", {
            "value": "29/09/2002",
            "status": "found",
            "quotes": ["RENT REVIEW DATE: The 29th day of September in the years 2002"],
            "reasoning": "From particulars",
            "confidence": "high",
        })
        assert len(result.supporting_evidence) >= 1
        assert result.needs_review is False

    def test_unresolved_quote_flags_review(self):
        result = self.resolver.resolve_field("rent_review_first_date", {
            "value": "01/01/2030",
            "status": "found",
            "quotes": ["This text does not exist in any evidence row at all xyzabc123"],
            "reasoning": "Test",
            "confidence": "high",
        })
        assert result.needs_review is True
        assert result.review_reason is not None

    def test_empty_quotes_with_found_status_flags_review(self):
        result = self.resolver.resolve_field("rent_review_upwards_only", {
            "value": "Yes",
            "status": "found",
            "quotes": [],
            "reasoning": "No quote",
            "confidence": "high",
        })
        assert result.needs_review is True

    def test_quote_spanning_adjacent_rows_resolves(self):
        result = self.resolver.resolve_field("omv_disregard_rent_free", {
            "value": "Yes",
            "status": "found",
            "quotes": ["as would be negotiated in the open market between a landlord and a tenant"],
            "reasoning": "Phrase is split across adjacent evidence rows.",
            "confidence": "high",
        })
        evidence_ids = {se.evidence_id for se in result.supporting_evidence}
        assert {"p30_item_9_0", "p30_item_10_0"}.issubset(evidence_ids)
        assert result.needs_review is False

    def test_not_present_status_no_review_needed(self):
        result = self.resolver.resolve_field("rent_review_time_of_essence", {
            "value": "No",
            "status": "not_present",
            "quotes": [],
            "reasoning": "No time of essence wording found",
            "confidence": "high",
        })
        # not_present with no quotes is valid — no needs_review
        assert result.needs_review is False

    def test_confidence_downgrade_on_unresolved_quote(self):
        result = self.resolver.resolve_field("rent_review_first_date", {
            "value": "29/09/2002",
            "status": "found",
            "quotes": ["completely fabricated text that cannot match anything xyz"],
            "reasoning": "Test",
            "confidence": "high",
        })
        # Confidence should have been downgraded
        assert result.confidence in ("medium", "low")

    def test_verbatim_text_from_manifest_not_model(self):
        result = self.resolver.resolve_field("rent_review_first_date", {
            "value": "29/09/2002",
            "status": "found",
            "quotes": ["RENT REVIEW DATE: The 29th day of September in the years 2002"],
            "reasoning": "From particulars",
            "confidence": "high",
        })
        # text_verbatim must come from manifest, not model
        for se in result.supporting_evidence:
            manifest_row = self.loader.get_evidence_row(se.evidence_id)
            assert manifest_row is not None
            assert se.text_verbatim == manifest_row.get("text", "")


# ---------------------------------------------------------------------------
# GroupConfig loader
# ---------------------------------------------------------------------------


class TestGroupConfig:
    @pytest.mark.parametrize("group_id", GROUPS)
    def test_all_groups_load(self, group_id):
        from extract.groups import load_group_config
        group = load_group_config(group_id)
        assert group.group_id == group_id
        assert len(group.fields) > 0

    def test_parties_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("parties")
        field_ids = [f.field_id for f in group.fields]
        assert "parties_original_lessor" in field_ids
        assert "parties_current_lessor" in field_ids
        assert "parties_original_lessee" in field_ids
        assert "parties_current_lessee" in field_ids
        assert "parties_guarantor_exists" in field_ids
        assert "parties_guarantor_name" in field_ids
        assert "parties_surety_exists" in field_ids
        assert "parties_surety_name" in field_ids
        assert group.semantic_topics == ["guarantor_surety"]

    def test_property_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("property")
        field_ids = [f.field_id for f in group.fields]
        assert "property_address" in field_ids
        assert "property_demised_premises" in field_ids
        assert group.semantic_topics == ["premises"]
        assert len(group.whole_sections) == 3

    def test_parties_guarantor_surety_rules_reject_future_mechanics(self):
        from extract.groups import load_group_config

        group = load_group_config("parties")
        guarantor_exists = group.get_field("parties_guarantor_exists")
        guarantor_name = group.get_field("parties_guarantor_name")
        surety_exists = group.get_field("parties_surety_exists")
        surety_name = group.get_field("parties_surety_name")

        assert guarantor_exists is not None
        assert guarantor_name is not None
        assert surety_exists is not None
        assert surety_name is not None
        assert "replacement/new guarantor provisions" in guarantor_exists.decision_rule
        assert "New Surety" in surety_exists.decision_rule
        assert "Surety: None" in surety_exists.decision_rule
        assert "Never return 'Name not found'" in guarantor_name.decision_rule
        assert "Never return 'Name not found'" in surety_name.decision_rule

    def test_lease_details_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("lease_details")
        field_ids = [f.field_id for f in group.fields]
        assert "lease_details_lease_date" in field_ids
        assert "lease_details_term" in field_ids
        assert "lease_details_expiry_date" in field_ids
        assert group.semantic_topics == []
        assert len(group.whole_sections) == 2

    def test_financials_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("financials")
        field_ids = [f.field_id for f in group.fields]
        assert "financials_original_rent" in field_ids
        assert group.semantic_topics == ["original_rent"]
        assert len(group.whole_sections) == 2
        assert "Annual Rent" in group.definition_allowlist
        assert "Insurance Rent" not in group.definition_allowlist

    def test_permitted_use_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("permitted_use")
        field_ids = [f.field_id for f in group.fields]
        assert "permitted_use_description" in field_ids
        assert group.semantic_topics == ["permitted_use"]
        assert len(group.whole_sections) == 1

    def test_statutory_status_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("statutory_status")
        field_ids = [f.field_id for f in group.fields]
        assert "statutory_status_lt_act_1954" in field_ids
        assert group.semantic_topics == ["landlord_tenant_act_1954"]
        assert group.prompt_version == "1.3"
        assert len(group.whole_sections) == 2
        assert "1954 Act" in group.definition_allowlist
        fs = group.get_field("statutory_status_lt_act_1954")
        assert fs is not None
        assert fs.allowed_values == ["Inside", "Outside"]

    def test_rent_review_header_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("rent_review_header")
        field_ids = [f.field_id for f in group.fields]
        assert "rent_review_first_date" in field_ids
        assert "rent_review_upwards_only" in field_ids
        assert "rent_review_basis_of_valuation" in field_ids

    def test_omv_disregards_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("omv_disregards")
        field_ids = [f.field_id for f in group.fields]
        assert "omv_disregard_occupation" in field_ids
        assert "omv_disregard_improvements" in field_ids

    def test_repairs_decorations_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("repairs_decorations")
        field_ids = [f.field_id for f in group.fields]
        assert "repairs_internal_repair" in field_ids
        assert "repairs_external_repair" in field_ids
        assert "repairs_internal_decoration" in field_ids
        assert "repairs_external_decoration" in field_ids

    def test_alterations_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("alterations")
        field_ids = [f.field_id for f in group.fields]
        assert "alterations_external" in field_ids
        assert "alterations_internal" in field_ids
        assert "alterations_structural" in field_ids
        assert "alterations_non_structural" in field_ids
        assert "alterations_demountable_partitions" in field_ids
        assert "alterations_electrics" in field_ids
        assert "alterations_conduits_services" in field_ids

    def test_break_rights_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("break_rights")
        field_ids = [f.field_id for f in group.fields]
        assert "break_rights_tenant_conditions" in field_ids
        assert "break_rights_landlord_conditions" in field_ids
        assert group.semantic_topics == ["break_option"]
        assert len(group.whole_sections) == 2
        assert "Break Fee" in group.definition_allowlist

    def test_third_party_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("third_party")
        field_ids = [f.field_id for f in group.fields]
        assert "third_party_determination" in field_ids
        assert "third_party_application_by" in field_ids
        assert "third_party_memorandum" in field_ids
        assert "third_party_interest_backdated_increase" in field_ids
        assert group.semantic_topics == ["third_party_determination", "rent_review"]
        det = group.get_field("third_party_determination")
        assert det is not None
        assert "Independent Expert" in det.allowed_values

    def test_rights_granted_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("rights_granted")
        field_ids = [f.field_id for f in group.fields]
        assert "rights_granted_vehicle_parking" in field_ids
        assert "rights_granted_bicycle_parking" in field_ids
        assert "rights_granted_motorbike_parking" in field_ids
        assert "rights_granted_fire_exit" in field_ids
        assert "rights_granted_loading_unloading" in field_ids
        assert group.semantic_topics == ["rights_granted"]
        assert group.whole_sections == []

    def test_insurance_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("insurance")
        field_ids = [f.field_id for f in group.fields]
        assert "insurance_building" in field_ids
        assert "insurance_building_responsibility" in field_ids
        assert "insurance_plate_glass" in field_ids
        assert "insurance_plate_glass_responsibility" in field_ids
        assert "insurance_engineering" in field_ids
        assert "insurance_engineering_responsibility" in field_ids
        assert "insurance_third_party_liability" in field_ids
        assert "insurance_loss_of_rent" in field_ids
        assert "insurance_loss_of_rent_period" in field_ids
        assert "insurance_cessor_of_rent" in field_ids
        assert "insurance_policy_effected_by" in field_ids
        assert "insurance_policy_paid_by" in field_ids

    def test_service_charge_fields(self):
        from extract.groups import load_group_config
        group = load_group_config("service_charge")
        field_ids = [f.field_id for f in group.fields]
        assert "service_charge_sinking_fund" in field_ids
        assert "service_charge_on_account" in field_ids
        assert "service_charge_ad_hoc_payments" in field_ids
        assert "service_charge_building" in field_ids
        assert "service_charge_building_services" in field_ids
        assert "service_charge_common_parts_building" in field_ids
        assert "service_charge_common_parts_estate" in field_ids
        assert "service_charge_budget_process" in field_ids
        assert "service_charge_reconciliation_process" in field_ids

    def test_unknown_group_raises(self):
        from extract.groups import load_group_config
        with pytest.raises(FileNotFoundError):
            load_group_config("nonexistent_group")

    def test_decision_rules_present(self):
        from extract.groups import load_group_config
        for group_id in GROUPS:
            group = load_group_config(group_id)
            for fs in group.fields:
                assert fs.decision_rule, f"No decision_rule for {group_id}.{fs.field_id}"


# ---------------------------------------------------------------------------
# Fake provider end-to-end
# ---------------------------------------------------------------------------


class TestFakeProviderEndToEnd:
    """Full pipeline test using fake provider — no API calls."""

    def test_run_group_fake(self, tmp_path):
        from extract.runner import run_group

        envelope = run_group(
            preextract_dir=HENRY_DIR,
            group_id="rent_review_header",
            model_profile_override="fake",
            output_dir=tmp_path,
            run_id="test_run",
        )

        assert envelope["doc_id"] == "Henry_Unit_2"
        assert envelope["group_id"] == "rent_review_header"
        assert envelope["provider"] == "fake"
        assert "fields" in envelope
        assert "rent_review_first_date" in envelope["fields"]
        assert "validation_summary" in envelope

    def test_output_file_written(self, tmp_path):
        from extract.runner import run_group

        run_group(
            preextract_dir=HENRY_DIR,
            group_id="rent_review_header",
            model_profile_override="fake",
            output_dir=tmp_path,
            run_id="test_run",
        )

        out_file = tmp_path / "rent_review_header.json"
        assert out_file.exists()

        with out_file.open() as f:
            data = json.load(f)
        assert data["schema_version"] == "1.0"
        assert "input_evidence_ids" in data

    def test_all_groups_fake(self, tmp_path):
        from extract.runner import run_group

        for group_id in GROUPS:
            envelope = run_group(
                preextract_dir=HENRY_DIR,
                group_id=group_id,
                model_profile_override="fake",
                output_dir=tmp_path,
                run_id="test_run",
            )
            assert envelope["group_id"] == group_id
            assert len(envelope["fields"]) > 0

    def test_q_glazing_third_party_pack_includes_rent_review_schedule(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        loader = ArtefactLoader.from_dir(Q_GLAZING_DIR)
        assembler = EvidencePackAssembler(loader)
        group = load_group_config("third_party")
        pack_text, meta = assembler.build(group)

        assert meta.input_evidence_ids
        assert meta.block_counts.get("operative_current", 0) > 0
        assert "independent Valuer" in pack_text or "independent valuer" in pack_text.lower()
        assert "acting as an expert and not as an arbitrator" in pack_text

    def test_counterpart_third_party_pack_definitions_only(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        loader = ArtefactLoader.from_dir(COUNTERPART_DIR)
        assembler = EvidencePackAssembler(loader)
        group = load_group_config("third_party")
        _, meta = assembler.build(group)

        assert meta.block_counts.get("operative_current", 0) == 0
        assert meta.block_counts.get("operative_other", 0) == 0

    def test_dry_run(self):
        from extract.runner import run_group

        result = run_group(
            preextract_dir=HENRY_DIR,
            group_id="rent_review_header",
            model_profile_override="fake",
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert "pack_text" in result
        assert "input_evidence_ids" in result

    def test_empty_pack_short_circuit(self, tmp_path):
        from extract.runner import run_group

        envelope = run_group(
            preextract_dir=COUNTERPART_DIR,
            group_id="omv_disregards",
            model_profile_override="fake",
            output_dir=tmp_path,
            run_id="empty_pack_test",
        )
        assert envelope.get("empty_pack_short_circuit") is True
        assert envelope["usage"]["input_tokens"] == 0
        assert envelope["usage"]["output_tokens"] == 0
        assert envelope["input_evidence_ids"] == []
        assert envelope["validation_summary"]["not_present"] == len(envelope["fields"])
        assert envelope["validation_summary"]["unclear"] == 0
        for field_data in envelope["fields"].values():
            assert field_data["status"] == "not_present"
            assert field_data["value"] == "N/A"
            assert field_data["confidence"] == "high"

    def test_run_all_groups_short_circuits_omv_when_basis_is_not_omv(self, tmp_path):
        from extract.runner import run_all_groups

        response = {
            "rent_review_basis_of_valuation": {
                "value": "Indexation (RPI)",
                "status": "found",
                "quotes": [],
                "reasoning": "Fake non-OMV basis.",
                "confidence": "high",
            }
        }

        envelopes = run_all_groups(
            preextract_dir=HENRY_DIR,
            group_ids=["rent_review_header", "omv_disregards", "omv_assumptions"],
            model_profile_override="fake",
            canned_response=response,
            output_dir=tmp_path,
            run_id="non_omv",
        )

        by_group = {envelope["group_id"]: envelope for envelope in envelopes}
        for group_id in ("omv_disregards", "omv_assumptions"):
            envelope = by_group[group_id]
            assert envelope["omv_short_circuit"] is True
            assert envelope["provider"] == "deterministic"
            assert envelope["usage"] == {"input_tokens": 0, "output_tokens": 0}
            assert envelope["validation_summary"]["found"] == len(envelope["fields"])
            for field_data in envelope["fields"].values():
                assert field_data["value"] == "N/A"
                assert field_data["status"] == "found"
                assert field_data["quotes"] == []
                assert field_data["confidence"] == "high"
            assert (tmp_path / f"{group_id}.json").is_file()

    def test_run_all_groups_runs_omv_when_basis_is_omv(self, tmp_path):
        from extract.runner import run_all_groups

        response = {
            "rent_review_basis_of_valuation": {
                "value": "Open Market Value",
                "status": "found",
                "quotes": [],
                "reasoning": "Fake OMV basis.",
                "confidence": "high",
            }
        }

        envelopes = run_all_groups(
            preextract_dir=HENRY_DIR,
            group_ids=["rent_review_header", "omv_disregards", "omv_assumptions"],
            model_profile_override="fake",
            canned_response=response,
            output_dir=tmp_path,
            run_id="omv",
        )

        by_group = {envelope["group_id"]: envelope for envelope in envelopes}
        for group_id in ("omv_disregards", "omv_assumptions"):
            envelope = by_group[group_id]
            assert "omv_short_circuit" not in envelope
            assert envelope["provider"] == "fake"
            assert (tmp_path / f"{group_id}.json").is_file()


# ---------------------------------------------------------------------------
# Gemini schema sanitizer (no API)
# ---------------------------------------------------------------------------


class TestGeminiSchemaSanitizer:
    def test_strips_additional_properties_nested(self):
        from extract.groups import load_group_config
        from extract.providers import _strip_unsupported_json_schema_for_gemini
        from extract.schemas import build_response_schema

        group = load_group_config("rent_review_header")
        schema = build_response_schema(group)
        assert "additionalProperties" in json.dumps(schema)

        cleaned = _strip_unsupported_json_schema_for_gemini(schema)
        blob = json.dumps(cleaned)
        assert "additionalProperties" not in blob

    def test_empty_enum_strings_become_sentinel(self):
        from extract.providers import (
            GEMINI_BLANK_ENUM_SENTINEL,
            _normalize_gemini_blank_sentinel,
            _strip_unsupported_json_schema_for_gemini,
        )
        from extract.schemas import build_response_schema
        from extract.groups import load_group_config

        group = load_group_config("omv_disregards")
        schema = build_response_schema(group)
        cleaned = _strip_unsupported_json_schema_for_gemini(schema)
        blob = json.dumps(cleaned)
        assert '""' not in blob or GEMINI_BLANK_ENUM_SENTINEL in blob
        assert GEMINI_BLANK_ENUM_SENTINEL in blob

        content = {
            "omv_disregard_occupation": {
                "value": GEMINI_BLANK_ENUM_SENTINEL,
                "status": "found",
                "quotes": [],
                "reasoning": "",
                "confidence": "high",
            }
        }
        _normalize_gemini_blank_sentinel(content)
        assert content["omv_disregard_occupation"]["value"] == ""


# ---------------------------------------------------------------------------
# Schema builder
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_response_schema_valid(self):
        from extract.groups import load_group_config
        from extract.schemas import build_response_schema

        group = load_group_config("rent_review_header")
        schema = build_response_schema(group)
        assert schema["type"] == "object"
        assert "rent_review_first_date" in schema["properties"]

    def test_yes_no_enum_constraint(self):
        from extract.groups import load_group_config
        from extract.schemas import build_response_schema

        group = load_group_config("rent_review_header")
        schema = build_response_schema(group)
        val_schema = schema["properties"]["rent_review_time_of_essence"]["properties"]["value"]
        assert set(val_schema["enum"]) >= {"Yes", "No"}

    def test_enum_field_allowed_values(self):
        from extract.groups import load_group_config
        from extract.schemas import build_response_schema

        group = load_group_config("rent_review_header")
        schema = build_response_schema(group)
        val_schema = schema["properties"]["rent_review_basis_of_valuation"]["properties"]["value"]
        assert "Open Market Value" in val_schema["enum"]

    def test_field_descriptions_non_empty(self):
        from extract.groups import load_group_config
        from extract.schemas import build_field_descriptions

        group = load_group_config("rent_review_header")
        desc = build_field_descriptions(group)
        assert "rent_review_first_date" in desc
        assert "Format:" in desc
        assert "Rule:" in desc


# ---------------------------------------------------------------------------
# Multi-segment (lease + annexed lease) tests
# ---------------------------------------------------------------------------


class TestMultiSegmentPack:
    """Tests using Henry_Counterpart_Lease_20.06.2017 which has seg_current + seg_annexed_old."""

    def setup_method(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config

        self.loader = ArtefactLoader.from_dir(COUNTERPART_DIR)
        self.assembler = EvidencePackAssembler(self.loader)
        self.group_insurance = load_group_config("insurance")
        self.group_alterations = load_group_config("alterations")

    def test_two_segments_detected(self):
        seg_id = self.loader.get_current_segment_id()
        assert seg_id == "seg_current"
        sm = self.loader.segment_map
        assert sm is not None
        seg_ids = [s["segment_id"] for s in sm.get("segments", [])]
        assert "seg_current" in seg_ids
        assert "seg_annexed_old" in seg_ids

    def test_pack_has_current_and_old_operative_blocks(self):
        pack_text, meta = self.assembler.build(self.group_insurance)
        # The counterpart lease has all insurance operative text in the annexed old lease;
        # current instrument has none. Verify at least old operative text is present.
        has_operative = (
            "# OPERATIVE TEXT — lease" in pack_text
            or "# OPERATIVE TEXT — annexed lease" in pack_text
            or "# LEASE —" in pack_text
            or "# ANNEXED LEASE —" in pack_text
        )
        assert has_operative, f"No operative/segment block found in insurance pack"

    def test_whole_sections_split_by_segment(self):
        from extract.groups import load_group_config
        group = load_group_config("lease_details")
        pack_text, meta = self.assembler.build(group)
        # With two segments, whole sections should use current/old blocks
        # At minimum, segment_kind should appear in the rendered rows
        assert "Lease" in pack_text or "Annexed lease" in pack_text

    def test_definitions_carry_segment_kind(self):
        from extract.groups import load_group_config
        group = load_group_config("rent_review_header")
        pack_text, meta = self.assembler.build(group)
        # current instrument definitions should be in a current block
        # and rows should show segment_kind inline
        if meta.input_evidence_ids:
            from extract.assembler import EvidencePackAssembler
            # segment_kind is rendered per row — confirm at least one row has it
            has_seg_kind = any(
                kind in pack_text
                for kind in ("Lease", "Annexed lease", "Deed of variation")
            )
            assert has_seg_kind

    def test_pack_source_map_has_current_and_old(self):
        pack_text, meta = self.assembler.build(self.group_insurance)
        source_blocks = set(meta.source_map.values())
        # Should include operative_current and/or operative_other
        operative_blocks = {b for b in source_blocks if "operative" in b}
        assert operative_blocks, f"No operative blocks found; got {source_blocks}"

    def test_no_duplicate_ids_multi_segment(self):
        _, meta = self.assembler.build(self.group_insurance)
        assert len(meta.input_evidence_ids) == len(set(meta.input_evidence_ids))

    def test_no_image_ids_multi_segment(self):
        _, meta = self.assembler.build(self.group_alterations)
        for eid in meta.input_evidence_ids:
            assert "layout_image" not in eid

    def test_supersession_rule_in_all_group_prompts(self):
        from extract.groups import load_group_config
        for group_id in GROUPS:
            group = load_group_config(group_id)
            assert "SUPERSESSION (lease vs annexed lease)" in group.system_prompt, (
                f"Missing supersession block in {group_id} system_prompt"
            )
            assert "comparative reasoning" in group.system_prompt
            assert "current instrument" not in group.system_prompt.lower()

    def test_lease_details_supersession_decision_rules(self):
        from extract.groups import load_group_config
        group = load_group_config("lease_details")
        assert group.prompt_version == "1.2"
        for fs in group.fields:
            assert "quotes must include one phrase from each segment" in fs.decision_rule or (
                "quotes must include one phrase from each segment when both exist"
                in fs.decision_rule
            ), fs.field_id


class TestSegmentAwareResolver:
    """Tests for the applied_segment_kind derivation and old-evidence post-check."""

    def setup_method(self):
        from extract.artefacts import ArtefactLoader
        from extract.assembler import EvidencePackAssembler
        from extract.groups import load_group_config
        from extract.resolver import QuoteResolver
        from extract.runner import _rows_from_meta

        self.loader = ArtefactLoader.from_dir(COUNTERPART_DIR)
        group = load_group_config("alterations")
        assembler = EvidencePackAssembler(self.loader)
        _, meta = assembler.build(group)
        self.pack_rows = _rows_from_meta(meta, self.loader)
        current_seg_id = self.loader.get_current_segment_id()
        self.resolver = QuoteResolver(
            self.loader,
            self.pack_rows,
            current_segment_id=current_seg_id,
        )

    def test_applied_segment_kind_populated_on_resolved_field(self):
        # Find a real text fragment from the old lease operative text
        old_rows = [r for r in self.pack_rows if r.segment_kind == "annexed_old_lease" and r.text]
        if not old_rows:
            pytest.skip("No annexed_old_lease rows in alterations pack for this fixture")
        sample_text = old_rows[0].text[:40]
        result = self.resolver.resolve_field("alterations_internal", {
            "value": "Tenant",
            "status": "found",
            "quotes": [sample_text],
            "reasoning": "From old lease",
            "confidence": "high",
        })
        assert result.applied_segment_kind is not None

    def test_old_evidence_flags_needs_review_when_current_present(self):
        # Resolver has current_segment_id set and pack has current rows
        old_rows = [r for r in self.pack_rows if r.segment_kind == "annexed_old_lease" and r.text]
        current_rows = [r for r in self.pack_rows if r.segment_id == self.loader.get_current_segment_id()]
        if not old_rows or not current_rows:
            pytest.skip("Need both current and old rows for this test")
        sample_text = old_rows[0].text[:40]
        result = self.resolver.resolve_field("alterations_internal", {
            "value": "Tenant",
            "status": "found",
            "quotes": [sample_text],
            "reasoning": "From old lease",
            "confidence": "high",
        })
        if result.applied_segment_kind == "annexed_old_lease":
            assert result.needs_review is True
            assert "lease evidence" in (result.review_reason or "")

    def test_current_evidence_does_not_flag_review(self):
        current_rows = [r for r in self.pack_rows if r.segment_id == self.loader.get_current_segment_id() and r.text]
        if not current_rows:
            pytest.skip("No current rows in pack")
        sample_text = current_rows[0].text[:40]
        result = self.resolver.resolve_field("alterations_internal", {
            "value": "Tenant",
            "status": "found",
            "quotes": [sample_text],
            "reasoning": "From current instrument",
            "confidence": "high",
        })
        if result.applied_segment_kind == "current_instrument":
            # Should NOT flag needs_review due to old-evidence rule
            assert "lease evidence was available" not in (result.review_reason or "") or (
                "Verify the lease does not" not in (result.review_reason or "")
            )

    def test_applied_segment_kind_in_envelope(self, tmp_path):
        from extract.runner import run_group
        envelope = run_group(
            preextract_dir=COUNTERPART_DIR,
            group_id="alterations",
            model_profile_override="fake",
            output_dir=tmp_path,
            run_id="test_counterpart",
        )
        for field_id, field_data in envelope["fields"].items():
            assert "applied_segment_kind" in field_data, (
                f"applied_segment_kind missing from field {field_id}"
            )
            assert "applied_segment_label" in field_data

    def test_segment_kind_label_mapping(self):
        from extract.assembler import segment_kind_label
        assert segment_kind_label("current_instrument") == "Lease"
        assert segment_kind_label("annexed_old_lease") == "Annexed lease"
        assert segment_kind_label("deed_of_variation") == "Deed of variation"

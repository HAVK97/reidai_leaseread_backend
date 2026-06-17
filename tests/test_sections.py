"""Tests for preextract semantic section routing."""

from __future__ import annotations

from preextract.sections import (
    _collect_body_phrase_eids,
    _keyword_in_heading,
    _match_topic,
    _route_segment,
)


def test_keyword_in_heading_rejects_roof_and_floor_loading():
    text = "4. roof and floor loading"
    assert not _keyword_in_heading(text, "loading")
    assert not _keyword_in_heading(text, "unloading")


def test_keyword_in_heading_accepts_loading_bay_phrase():
    text = "a right to use the loading bay"
    assert _keyword_in_heading(text, "loading bay")


def test_match_topic_fire_precautions_schedule():
    anchor = {
        "family": "schedules",
        "title": "fire precautions",
        "raw_text": "11. Fire precautions",
        "token": "Schedule 2::11",
    }
    cfg = {
        "schedule_keywords": ["fire precautions", "means of escape"],
        "keywords": [],
    }
    assert _match_topic(anchor, cfg) == "schedule_heading"


def test_collect_body_phrase_eids_third_party_requires_rent_review_context():
    """Generic surveyor boilerplate must not match without rent-review context."""
    manifest_eids = {"e_interest", "e_review"}
    eid_lookup = {
        "e_interest": {
            "evidence_id": "e_interest",
            "page_number": 16,
            "text": (
                '"Interest" means interest at 4% above base rate or as determined by '
                "the Surveyor acting as an expert and not as an arbitrator"
            ),
        },
        "e_review": {
            "evidence_id": "e_review",
            "page_number": 42,
            "text": (
                "Such revised rent for the relevant Rent Review Period determined by "
                "an independent Valuer (acting as an expert and not as an arbitrator)"
            ),
        },
    }
    phrases = [r"acting as an expert and not as an arbitrator"]
    requires = [
        r"\brent review\b",
        r"\brevised rent\b",
        r"\brent review period\b",
    ]
    hits = _collect_body_phrase_eids(
        body_phrases=phrases,
        box_assignments=[{"evidence_id": "e_interest"}, {"evidence_id": "e_review"}],
        manifest_eids=manifest_eids,
        eid_lookup=eid_lookup,
        seg_page_range=(1, 60),
        seen=set(),
        body_phrase_requires=requires,
    )
    assert hits == ["e_review"]


def test_collect_body_phrase_eids_finds_means_of_escape():
    manifest_eids = {"e1", "e2"}
    eid_lookup = {
        "e1": {
            "evidence_id": "e1",
            "page_number": 34,
            "text": "not obstruct the means of escape from the Premises or lock any fire door",
        },
        "e2": {
            "evidence_id": "e2",
            "page_number": 34,
            "text": "roof and floor loading limits apply",
        },
    }
    hits = _collect_body_phrase_eids(
        body_phrases=[r"\bmeans of escape\b", r"\bfire door\b"],
        box_assignments=[
            {"evidence_id": "e1"},
            {"evidence_id": "e2"},
        ],
        manifest_eids=manifest_eids,
        eid_lookup=eid_lookup,
        seg_page_range=(1, 50),
        seen=set(),
    )
    assert hits == ["e1"]


def test_route_segment_rights_granted_body_phrase_without_heading_match():
    seg_struct = {
        "segment_id": "seg_1",
        "anchors_flat": [],
        "box_assignments": [
            {"evidence_id": "e_fire", "anchor_id": None},
        ],
    }
    segment = {"segment_id": "seg_1", "page_range": [1, 50]}
    manifest_eids = {"e_fire"}
    eid_lookup = {
        "e_fire": {
            "evidence_id": "e_fire",
            "page_number": 34,
            "text": "7.6.3.2. not obstruct access to fire equipment or the means of escape",
        },
    }
    results = _route_segment(
        seg_struct,
        segment,
        manifest_eids,
        eid_lookup,
        {"definitions": []},
    )
    rg = results["rights_granted"]
    assert "e_fire" in rg["evidence_ids"]
    assert "body_phrase" in rg["method"]


def test_keyword_in_heading_rejects_repairing_as_repair():
    text = (
        "of entry on to adjoining property for the purpose of repairing "
        "maintaining and renewing conduits"
    )
    assert not _keyword_in_heading(text, "repair")
    assert _keyword_in_heading("4.8 repair", "repair")


def test_match_topic_repair_clause_heading():
    anchor = {
        "family": "numbered_clauses",
        "title": "<u>repair</u>",
        "raw_text": "4.8 <u>Repair</u>",
        "token": "4.8",
    }
    cfg = {"keywords": ["repair", "decoration"], "schedule_keywords": []}
    assert _match_topic(anchor, cfg) == "heading_keyword"


def test_match_topic_common_matters_service_charge():
    anchor = {
        "family": "numbered_clauses",
        "title": "common matters",
        "raw_text": "4.6 Common Matters",
        "token": "4.6",
    }
    cfg = {"keywords": ["common matters"], "schedule_keywords": []}
    assert _match_topic(anchor, cfg) == "heading_keyword"


def test_route_segment_break_option_body_phrase_without_heading_match():
    seg_struct = {
        "segment_id": "seg_1",
        "anchors_flat": [],
        "box_assignments": [
            {"evidence_id": "e_break", "anchor_id": None},
        ],
    }
    segment = {"segment_id": "seg_1", "page_range": [1, 50]}
    manifest_eids = {"e_break"}
    eid_lookup = {
        "e_break": {
            "evidence_id": "e_break",
            "page_number": 40,
            "text": (
                "If the Tenant wishes to determine this lease on the day of 2022 it shall "
                "give to the Landlord not less than six months prior written notice in the "
                "form set out in the Schedule 5"
            ),
        },
    }
    results = _route_segment(
        seg_struct,
        segment,
        manifest_eids,
        eid_lookup,
        {"definitions": []},
    )
    bo = results["break_option"]
    assert "e_break" in bo["evidence_ids"]
    assert "body_phrase" in bo["method"]

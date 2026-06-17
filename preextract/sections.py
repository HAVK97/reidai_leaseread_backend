"""Build the semantic section map.

The section map routes extraction topics to scoped sets of evidence_ids.
Each extractor run receives only the evidence for its topic (e.g. insurance,
rent_review) plus the allowed evidence_id list, improving accuracy and
preventing model-invented citations.

Routing algorithm (deterministic, tree-based):
  1. Restrict topic matching to anchors with family in {numbered_clauses,
     schedules}. front_matter and lr_clauses are never used for semantic
     topic routing; they are surfaced as whole sections to other extraction
     flows by the next pipeline stage.
  2. Pass A (parent-first): for each topic, scan top-level eligible anchors.
     If the heading matches a topic keyword (or schedule_keywords for
     schedules), claim the entire descendant subtree at parent confidence.
  3. Pass B (orphan child): any eligible anchor not yet covered whose
     heading still matches the topic claims its subtree at lower
     confidence. Recovers cases where the parent heading is generic
     (e.g. "TENANT'S COVENANTS") and the topical heading sits on a child.
  4. Pass C (body phrase): for topics with body_phrases, scan manifest
     text in the segment and add evidence rows whose text matches (e.g.
     means of escape / fire door covenants that sit under unrelated
     anchor headings).
  5. Evidence collection walks the matched subtree and pulls evidence_ids
     from box_assignments. The DEFINITIONS subtree is intentionally
     excluded from topic evidence; defined terms remain in
     definitions_index.json.
  6. Evidence is sorted by document order and clipped to the segment's
     page range.

Topics are keyed by the field-registry slugs from the product description.
"""

from __future__ import annotations

import re
from collections import defaultdict

SCHEMA_VERSION = "1.0"

# Families eligible for semantic topic tagging.
ALLOWED_FAMILIES = {"numbered_clauses", "schedules"}

# Anchor tokens whose subtree is excluded from topic evidence by default.
# Definitions are extracted separately into definitions_index.json; mixing
# them into every topic bag pollutes operative-clause evidence.
EXCLUDED_TOKENS = {"DEFINITIONS"}

# ---------------------------------------------------------------------------
# Topic definitions
# ---------------------------------------------------------------------------

# Thirteen topics only: premises, rent_review, insurance, service_charge,
# repairs_decoration, alterations, alienation, permitted_use,
# landlord_tenant_act_1954, break_option, guarantor_surety,
# rights_granted, third_party_determination.
#
# - keywords:           matched against numbered_clauses anchor heading text.
# - schedule_keywords:  matched against schedules anchor heading text.
#
# LR tokens (LR3, LR4, ...) are intentionally NOT used for semantic routing.
# Generic schedule labels like "tenant's covenants" are intentionally NOT
# used as parent matches; orphan-pass child headings recover specific topics.
SECTION_TOPICS: dict[str, dict] = {
    "premises": {
        "keywords": [
            "demise",
            "demised",
            "property description",
            "identity of the property",
            "the premises",
            "the property",
        ],
        "schedule_keywords": [
            "demise",
            "demised",
            "property description",
            "identity of the property",
            "the premises",
            "the property",
        ],
        "description": "Parties, demise, plans and identity of property",
    },
    "rent_review": {
        "keywords": [
            "rent review",
            "review of rent",
            "review clause",
            "open market value",
            "willing parties",
            "fit for immediate",
            "occupation disregard",
            "independent expert",
            "expert determination",
            "arbitration",
            "arbitrator",
            "third party determination",
        ],
        "schedule_keywords": [
            "rent review",
            "review of rent",
            "rent and rent review",
            "the rent and rent review",
        ],
        "description": "Rent review, OMV assumptions and disregards, third-party determination",
    },
    "insurance": {
        "keywords": [
            "insurance",
            "insured risk",
            "uninsured risk",
            "uninsured damage",
            "insurance proceeds",
            "policy money",
            "full reinstatement cost",
            "reinstatement cost",
            "cost of reinstatement",
            "insurance rent",
            "insurance premium",
            "insurance premiums",
            "insurance premium tax",
            "premium tax",
            "loss of rent",
            "loss of yearly rent",
            "loss of annual rent",
            "loss of rental income",
            "rent insurance",
            "indemnity period",
            "cessor",
            "cesser",
            "rent suspension",
            "suspension of rent",
            "rent abatement",
            "abatement of rent",
            "plate glass",
            "plate-glass",
            "plateglass",
            "plate glass insurance",
            "breakage",
            "engineering insurance",
            "engineering risks",
            "engineering risk",
            "plant and machinery insurance",
            "pressure plant",
            "boilers",
            "statutory engineering inspection",
            "third party liability",
            "public liability",
            "public liability risks",
            "employers' liability",
            "employers liability",
            "employers third party and public liability risks",
            "occupiers liability",
            "occupier's liability",
            "occupiers liability risks",
            "occupier's liability risks",
            "liability to third parties",
            "property owners liability",
            "defective premises act",
            "policy effected by the landlord",
            "insurance effected by the landlord",
            "landlord shall insure",
            "landlord's obligation to insure",
            "to insure and keep insured",
            "insurance office approved by the landlord",
            "insurer of good repute",
            "substantial insurer",
            "retention of insurance proceeds",
            "application of insurance proceeds",
            "damage by an insured risk",
            "damage by an uninsured risk",
            "termination following uninsured damage",
            "termination on failure to reinstate",
        ],
        "schedule_keywords": [
            "insurance",
            "insured risk",
            "uninsured risk",
            "uninsured damage",
            "insurance proceeds",
            "policy money",
            "full reinstatement cost",
            "reinstatement cost",
            "cost of reinstatement",
            "insurance rent",
            "insurance premium",
            "insurance premiums",
            "insurance premium tax",
            "premium tax",
            "loss of rent",
            "loss of yearly rent",
            "loss of annual rent",
            "loss of rental income",
            "rent insurance",
            "indemnity period",
            "cessor",
            "cesser",
            "rent suspension",
            "suspension of rent",
            "rent abatement",
            "abatement of rent",
            "plate glass",
            "plate-glass",
            "plateglass",
            "plate glass insurance",
            "breakage",
            "engineering insurance",
            "engineering risks",
            "engineering risk",
            "plant and machinery insurance",
            "pressure plant",
            "boilers",
            "statutory engineering inspection",
            "third party liability",
            "public liability",
            "public liability risks",
            "employers' liability",
            "employers liability",
            "employers third party and public liability risks",
            "occupiers liability",
            "occupier's liability",
            "occupiers liability risks",
            "occupier's liability risks",
            "liability to third parties",
            "property owners liability",
            "defective premises act",
            "policy effected by the landlord",
            "insurance effected by the landlord",
            "landlord shall insure",
            "landlord's obligation to insure",
            "to insure and keep insured",
            "insurance office approved by the landlord",
            "insurer of good repute",
            "substantial insurer",
            "retention of insurance proceeds",
            "application of insurance proceeds",
            "damage by an insured risk",
            "damage by an uninsured risk",
            "termination following uninsured damage",
            "termination on failure to reinstate",
        ],
        "description": "Insurance obligations, insured and uninsured risks, premiums, reinstatement, loss of rent, rent cesser, plate glass, engineering cover and liability insurance",
    },
    "service_charge": {
        "keywords": [
            "service charge",
            "sinking fund",
            "common expenses",
            "common matters",
            "estate expenses",
        ],
        "schedule_keywords": ["service charge"],
        "description": "Service charge, sinking fund, reconciliation, budget",
    },
    "repairs_decoration": {
        "keywords": [
            "repair and decoration",
            "repair, decoration",
            "repair cleaning",
            "repair and condition",
            "repair",
            "redecoration",
            "decoration",
            "decorate",
            "reinstatement",
            "dilapidations",
            "yielding up",
            "maintain landlord's fixtures",
            "maintain landlords fixtures",
        ],
        "schedule_keywords": [
            "repair",
            "decoration",
            "decorate",
            "reinstatement",
            "dilapidations",
            "yielding up",
        ],
        "body_phrases": [
            r"\bto keep the (?:property|premises) in (?:good )?(?:and )?(?:substantial )?repair\b",
            r"\bkeep the (?:property|premises) in repair\b",
            r"\bmaintain in good and serviceable condition the landlord'?s fixtures\b",
        ],
        "description": "Repair and decoration covenants",
    },
    "alterations": {
        "keywords": [
            "alterations",
            "alteration",
            "structural works",
            "non-structural",
            "demountable partition",
        ],
        "schedule_keywords": ["alterations"],
        "description": "Alterations: structural, non-structural, demountable partitions",
    },
    "alienation": {
        "keywords": [
            "alienation",
            "assignment of the",
            "underletting",
            "sublet",
            "assign the whole",
        ],
        "schedule_keywords": ["alienation", "assignment", "underletting"],
        "description": "Alienation: assignment, subletting",
    },
    "permitted_use": {
        "keywords": [
            "permitted use",
            "permitted user",
            "prohibited user",
            "user",
            "use classes order",
            "town and country planning",
            "town and country planning (use classes) order",
            "class a1",
            "class b1",
            "class e",
            "office covenants",
            "industrial covenants",
            "permitted part",
            "no undesirable activities",
            "undesirable activities",
            "use of the premises",
            "use clause",
        ],
        "schedule_keywords": [
            "permitted use",
            "permitted user",
            "prohibited user",
            "user",
            "use classes order",
            "town and country planning",
            "town and country planning (use classes) order",
            "class a1",
            "class b1",
            "class e",
            "office covenants",
            "industrial covenants",
            "permitted part",
            "no undesirable activities",
            "undesirable activities",
            "use of the premises",
            "use clause",
        ],
        "description": "Permitted and prohibited use, use classes, planning use categories and user covenants",
    },
    "landlord_tenant_act_1954": {
        "keywords": [
            "landlord and tenant act 1954",
            "landlord & tenant act 1954",
            "landlord and tenant act, 1954",
            "landlord & tenant act, 1954",
            "landlord and tenant act of 1954",
            "1954 act",
            "lta 1954",
            "lta1954",
            "part ii lta 1954",
            "part 2 lta 1954",
            "security of tenure",
            "contracting out",
            "contracted out",
            "agreement to exclude security of tenure",
            "excluded from security of tenure",
            "exclusion of security of tenure",
            "without security of tenure",
            "prescribed statements",
            "contracting out of the 1954 act",
            "contracting out of security of tenure",
            "protected tenancy",
            "regulatory reform (business tenancies) (england and wales) order 2003",
            "regulatory reform order 2003",
            "business tenancies order 2003",
            "2003 order",
        ],
        "schedule_keywords": [
            "landlord and tenant act 1954",
            "landlord & tenant act 1954",
            "landlord and tenant act, 1954",
            "landlord & tenant act, 1954",
            "landlord and tenant act of 1954",
            "1954 act",
            "lta 1954",
            "lta1954",
            "part ii lta 1954",
            "part 2 lta 1954",
            "security of tenure",
            "contracting out",
            "contracted out",
            "agreement to exclude security of tenure",
            "excluded from security of tenure",
            "exclusion of security of tenure",
            "without security of tenure",
            "prescribed statements",
            "contracting out of the 1954 act",
            "contracting out of security of tenure",
            "protected tenancy",
            "regulatory reform (business tenancies) (england and wales) order 2003",
            "regulatory reform order 2003",
            "business tenancies order 2003",
            "2003 order",
        ],
        "body_phrases": [
            r"sections?\s+24\s*(?:to|-)\s*28.*exclud",
            r"provisions of sections?\s+24\s*(?:to|-)\s*28",
            r"section\s+38\s*A",
            r"section\s+38\s*\(\s*4\s*\)",
            r"statutory declaration",
            r"contracting\s+out",
            r"contracted\s+out",
            r"security of tenure",
            r"without security of tenure",
            r"sections?\s+30\s*\(\s*1\s*\)",
            r"section\s+24.*(?:application|1954\s+act)",
            r"pending or intended step under the 1954 act",
            r"continuation of the tenancy",
            r"grant of a new tenancy",
            r"prescribed statement",
            r"landlord and tenant act 1954.*protect",
        ],
        "description": "Landlord and Tenant Act 1954 security of tenure and contracting out",
    },
    "break_option": {
        "keywords": [
            "break clause",
            "break option",
            "break right",
            "right to break",
            "right of break",
            "option to break",
            "tenant's break",
            "tenant break",
            "tenant only break",
            "landlord's break",
            "landlord break",
            "landlord only break",
            "mutual break",
            "rolling break",
            "one-off break",
            "break date",
            "break notice",
            "notice to break",
            "notice exercising the break",
            "exercise the break",
            "exercising the break",
            "break fee",
            "break premium",
            "break payment",
            "option to determine",
            "right to determine",
            "early termination",
            "right to terminate",
            "right of termination",
            "termination date",
            "tenant's option to break",
        ],
        "body_phrases": [
            r"\bbreak clause\b",
            r"wish(?:es)? to determine this (?:lease|sublease|tenancy)\b",
            r"terminate this (?:lease|sublease).*\bbreak\b",
            r"(?:six|3|three|6)\s+months?\s+(?:prior\s+)?(?:written\s+)?notice\b.*\bbreak\b",
            r"exercise\s+(?:the\s+)?tenant'?s?\s+right\s+to\s+break\b",
            r"right\s+to\s+break\s+(?:the\s+)?(?:lease|sublease)\b",
            r"not\s+entitled\s+to\s+implement\b.*\bbreak\b",
            r"break\s+notice\b.*\bform\b",
            r"pay\s+(?:the\s+)?break\s+(?:fee|premium)\b",
            r"break\s+date\b.*\bnotice\b",
        ],
        "schedule_keywords": [
            "break clause",
            "break option",
            "break right",
            "right to break",
            "right of break",
            "option to break",
            "tenant's break",
            "tenant break",
            "tenant only break",
            "landlord's break",
            "landlord break",
            "landlord only break",
            "mutual break",
            "rolling break",
            "one-off break",
            "break date",
            "break notice",
            "notice to break",
            "notice exercising the break",
            "exercise the break",
            "exercising the break",
            "break fee",
            "break premium",
            "break payment",
            "option to determine",
            "right to determine",
            "early termination",
            "right to terminate",
            "right of termination",
            "termination date",
            "tenant's option to break",
            "the notice",
            "notice of break",
        ],
        "description": "Break options, break dates, break notices, break fees and break conditions",
    },
    "guarantor_surety": {
        "keywords": [
            "guarantor",
            "surety",
            "sureties",
            "guarantee",
            "principal debtor",
            "primary obligation",
            "primary obligor",
            "secured obligations",
        ],
        "schedule_keywords": [
            "guarantor",
            "surety",
            "sureties",
            "guarantee",
            "principal debtor",
            "primary obligation",
            "primary obligor",
            "secured obligations",
        ],
        "description": "Guarantors, sureties, guarantees, primary liability wording and secured obligations",
    },
    "rights_granted": {
        "keywords": [
            "rights granted",
            "rights and easements",
            "rights granted to the tenant",
            "easements",
            "vehicle access",
            "vehicular",
            "vehicles",
            "car parking",
            "motor car",
            "motor car parking",
            "parking",
            "parking space",
            "parking spaces",
            "bicycle",
            "bicycles",
            "bicycle racks",
            "bicycle parking",
            "cycle racks",
            "cycle parking",
            "motorcycle",
            "motorcycles",
            "motorcycle parking",
            "fire exit",
            "fire escape",
            "fire door",
            "fire precautions",
            "fire safety",
            "means of escape",
            "right of escape",
            "delivery bay",
            "loading bay",
            "unloading bay",
            "loading and unloading",
            "load and unload",
        ],
        "body_phrases": [
            r"\bmeans of escape\b",
            r"\bfire door\b",
            r"\bfire exit\b",
            r"\bfire escape\b",
            r"\bemergency exit\b",
            r"\bright of escape\b",
        ],
        "schedule_keywords": [
            "rights granted",
            "rights and easements",
            "rights granted to the tenant",
            "easements",
            "vehicle access",
            "vehicular",
            "vehicles",
            "car parking",
            "motor car",
            "motor car parking",
            "parking",
            "parking space",
            "parking spaces",
            "bicycle",
            "bicycles",
            "bicycle racks",
            "bicycle parking",
            "cycle racks",
            "cycle parking",
            "motorcycle",
            "motorcycles",
            "motorcycle parking",
            "fire exit",
            "fire escape",
            "fire door",
            "fire precautions",
            "fire safety",
            "means of escape",
            "right of escape",
            "delivery bay",
            "loading bay",
            "unloading bay",
            "loading and unloading",
            "load and unload",
        ],
        "description": "Rights granted to the tenant, easements, vehicle access, parking, bicycle and motorcycle facilities, fire escape routes and loading rights",
    },
    "original_rent": {
        "keywords": [
            "annual rent",
            "yearly rent",
            "initial rent",
            "basic rent",
            "principal rent",
            "reserved rent",
            "the annual rent",
            "the yearly rent",
            "the initial rent",
        ],
        "body_phrases": [
            r"(?:annual rent|yearly rent|initial rent|basic rent|principal rent|reserved rent)\s*:\s*[^.£\n]*£",
            r"(?:annual rent|yearly rent|initial rent|basic rent|principal rent|reserved rent)\s+means[^.£\n]*£",
            r'["\']?(?:the )?(?:annual rent|initial rent|yearly rent|basic rent|principal rent)["\']?\s*means[^.£\n]*£',
            r'["\']?rent["\']?\s*means\s*£[\d,]+',
        ],
        "schedule_keywords": [
            "yearly rent",
            "annual rent",
            "initial rent",
        ],
        "description": "Original or initial reserved rent amounts and rent definition clauses",
    },
    "third_party_determination": {
        "keywords": [
            "third party determination",
            "expert determination",
            "independent expert",
            "appointed expert",
            "expert appointed",
            "independent valuer",
            "arbitrator",
            "arbitration",
            "referred to arbitration",
            "rent review determination",
            "determination of the reviewed rent",
            "determination of the revised rent",
            "determination of the open market rent",
            "late determination",
            "entitled to appoint",
            "appointed by the president",
            "president of the royal institution of chartered surveyors",
            "president of rics",
            "royal institution of chartered surveyors",
            "rics",
            "memorandum",
            "interest on backdated increase",
            "interest on revised rent",
            "interest on reviewed rent",
            "interim payments",
        ],
        "schedule_keywords": [
            "third party determination",
            "expert determination",
            "independent expert",
            "appointed expert",
            "expert appointed",
            "independent valuer",
            "arbitrator",
            "arbitration",
            "referred to arbitration",
            "rent review determination",
            "determination of the reviewed rent",
            "determination of the revised rent",
            "determination of the open market rent",
            "late determination",
            "entitled to appoint",
            "appointed by the president",
            "president of the royal institution of chartered surveyors",
            "president of rics",
            "royal institution of chartered surveyors",
            "rics",
            "memorandum",
            "interest on backdated increase",
            "interest on revised rent",
            "interest on reviewed rent",
            "interim payments",
        ],
        "body_phrases": [
            r"acting as an expert and not as an arbitrator",
            r"determined by an (?:independent )?(?:valuer|surveyor|arbitrator)",
            r"determined by a single arbitrator",
            r"nominated .{0,120}president .{0,80}royal institution of chartered surveyors",
            r"on the application of (?:either )?the landlord or the tenant",
            r"rent(?:al)? value shall be determined by an arbitrator",
            r"in the absence of agreement.{0,120}determined by an independent valuer",
        ],
        # Same-row gate: ignore generic surveyor/expert boilerplate outside rent review.
        "body_phrase_requires": [
            r"\brent review\b",
            r"\breviewed rent\b",
            r"\brevised rent\b",
            r"\bopen market rent\b",
            r"\brent review (?:date|period)\b",
            r"\brelevant rent review\b",
        ],
        "description": "Third-party rent review and dispute mechanics, including arbitrators, independent experts, RICS appointment, memoranda and backdated interest",
    },
}

# Confidence tiers — parent matches always rank above orphan child matches.
_CONF_PARENT_SCHEDULE = 0.95
_CONF_PARENT_HEADING = 0.85
_CONF_ORPHAN = 0.65
_CONF_BODY_PHRASE = 0.55
_CONF_LOW_WARN = 0.55

# Heading keywords that must not match as bare substrings (e.g. "loading"
# inside "roof and floor loading").
_BOUNDARY_HEADING_KEYWORDS = frozenset({"loading", "unloading", "repair"})


# ---------------------------------------------------------------------------
# Anchor-tree helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _build_anchor_index(anchors_flat: list[dict]) -> dict[int, dict]:
    """Map anchor_id -> anchor dict."""
    return {int(a["anchor_id"]): a for a in anchors_flat if "anchor_id" in a}


def _build_descendants_map(anchors_flat: list[dict]) -> dict[int, set[int]]:
    """Map anchor_id -> set of all descendant anchor_ids (inclusive of self)."""
    by_id = _build_anchor_index(anchors_flat)
    cache: dict[int, set[int]] = {}

    def walk(aid: int, stack: set[int]) -> set[int]:
        if aid in cache:
            return cache[aid]
        if aid in stack:
            return {aid}
        stack.add(aid)
        result: set[int] = {aid}
        for child_id in (by_id.get(aid) or {}).get("children_ids") or []:
            try:
                result |= walk(int(child_id), stack)
            except (TypeError, ValueError):
                continue
        stack.discard(aid)
        cache[aid] = result
        return result

    return {aid: walk(aid, set()) for aid in by_id}


def _build_anchor_evidence_map(box_assignments: list[dict]) -> dict[int, list[str]]:
    """Map anchor_id -> ordered evidence_ids assigned to that anchor."""
    out: dict[int, list[str]] = defaultdict(list)
    for a in box_assignments:
        aid = a.get("anchor_id")
        eid = a.get("evidence_id")
        if aid is None or not eid:
            continue
        try:
            out[int(aid)].append(eid)
        except (TypeError, ValueError):
            continue
    return dict(out)


def _build_eid_position(box_assignments: list[dict]) -> dict[str, int]:
    """Map evidence_id -> position index in document order."""
    pos: dict[str, int] = {}
    for i, a in enumerate(box_assignments):
        eid = a.get("evidence_id")
        if eid and eid not in pos:
            pos[eid] = i
    return pos


def _page_range_for_eids(eids: list[str], eid_lookup: dict[str, dict]) -> list[int]:
    pages: set[int] = set()
    for eid in eids:
        row = eid_lookup.get(eid)
        if row:
            pn = row.get("page_number")
            if pn:
                pages.add(int(pn))
    return sorted(pages)


# ---------------------------------------------------------------------------
# Heading matching
# ---------------------------------------------------------------------------


def _heading_text(anchor: dict) -> str:
    """Best-effort heading text for keyword matching.

    structure_v2 truncates `title` for very long headers, so we also include
    `raw_text` to recover full heading content where possible.
    """
    parts = [
        _normalize(anchor.get("title") or ""),
        _normalize(anchor.get("raw_text") or ""),
    ]
    return " ".join(p for p in parts if p)


def _keyword_in_heading(text: str, keyword: str) -> bool:
    """Match a topic keyword against normalized anchor heading text."""
    kw = (keyword or "").strip().lower()
    if not kw or not text:
        return False

    if kw in _BOUNDARY_HEADING_KEYWORDS:
        if re.search(r"\b(?:roof|floor)\s+loading\b", text):
            return False
        return bool(re.search(rf"\b{re.escape(kw)}\b", text))

    return kw in text


def _match_topic(anchor: dict, cfg: dict) -> str:
    """Return match method tag ("schedule_heading"|"heading_keyword") or "" for no match."""
    family = anchor.get("family") or ""
    if family not in ALLOWED_FAMILIES:
        return ""
    text = _heading_text(anchor)
    if not text:
        return ""

    if family == "schedules":
        for kw in cfg.get("schedule_keywords") or []:
            if _keyword_in_heading(text, kw):
                return "schedule_heading"
        # Schedules do not fall through to clause keywords; topic-specific
        # schedule_keywords are the only way to claim a schedule subtree.
        return ""

    # numbered_clauses
    for kw in cfg.get("keywords") or []:
        if _keyword_in_heading(text, kw):
            return "heading_keyword"
    return ""


def _collect_body_phrase_eids(
    *,
    body_phrases: list[str],
    box_assignments: list[dict],
    manifest_eids: set[str],
    eid_lookup: dict[str, dict],
    seg_page_range: tuple[int, int] | None,
    seen: set[str],
    body_phrase_requires: list[str] | None = None,
) -> list[str]:
    """Return evidence_ids whose manifest text matches topic body_phrases."""
    if not body_phrases:
        return []

    compiled = [re.compile(p, re.I) for p in body_phrases]
    requires = [re.compile(p, re.I) for p in (body_phrase_requires or [])]
    matched: list[str] = []

    for ba in box_assignments:
        eid = ba.get("evidence_id")
        if not eid or eid in seen or eid not in manifest_eids:
            continue

        row = eid_lookup.get(eid) or {}
        page = int(row.get("page_number") or 0)
        if seg_page_range and not (seg_page_range[0] <= page <= seg_page_range[1]):
            continue

        text = _normalize(row.get("text") or "")
        if not text:
            continue
        if not any(pat.search(text) for pat in compiled):
            continue
        if requires and not any(req.search(text) for req in requires):
            continue
        matched.append(eid)

    return matched


def _term_topic_hint(norm_term: str, keyword: str) -> bool:
    """Whether a normalized definition term plausibly relates to a topic keyword.

    Uses whole-word containment in either direction. This avoids substring
    bugs like "term" matching "determination".
    """
    norm = (norm_term or "").strip().lower()
    kw = (keyword or "").strip().lower()
    if not norm or not kw:
        return False
    if norm == kw:
        return True
    if re.search(rf"\b{re.escape(norm)}\b", kw):
        return True
    if re.search(rf"\b{re.escape(kw)}\b", norm):
        return True
    return False


# ---------------------------------------------------------------------------
# Per-segment routing
# ---------------------------------------------------------------------------


def _route_segment(
    seg_struct: dict,
    segment: dict | None,
    manifest_eids: set[str],
    eid_lookup: dict[str, dict],
    definitions_index: dict,
) -> dict[str, dict]:
    """Route all topics for one segment using the anchor tree.

    Returns dict: topic_key -> topic_entry dict.
    """
    anchors_flat: list[dict] = seg_struct.get("anchors_flat") or []
    box_assignments: list[dict] = seg_struct.get("box_assignments") or []
    segment_id: str = seg_struct.get("segment_id", "whole_doc")

    by_id = _build_anchor_index(anchors_flat)
    descendants_map = _build_descendants_map(anchors_flat)
    anchor_evidence = _build_anchor_evidence_map(box_assignments)
    eid_position = _build_eid_position(box_assignments)

    seg_page_range: tuple[int, int] | None = None
    if segment:
        pr = segment.get("page_range")
        if pr and len(pr) == 2:
            seg_page_range = (int(pr[0]), int(pr[1]))

    # Definition-term hints: definitions_index normalized terms whose phrase
    # overlaps a topic keyword (as a whole word). Hint metadata only — does
    # not drive evidence selection.
    def_terms_by_topic: dict[str, list[str]] = defaultdict(list)
    for defn in definitions_index.get("definitions") or []:
        if defn.get("segment_id") != segment_id:
            continue
        norm = defn.get("normalized_term") or ""
        term = defn.get("term") or ""
        if not norm or not term:
            continue
        for topic_key, cfg in SECTION_TOPICS.items():
            for kw in cfg.get("keywords") or []:
                if _term_topic_hint(norm, kw) and term not in def_terms_by_topic[topic_key]:
                    def_terms_by_topic[topic_key].append(term)
                    break

    eligible = [a for a in anchors_flat if a.get("family") in ALLOWED_FAMILIES]
    eligible_by_level1 = sorted(
        (a for a in eligible if int(a.get("level") or 0) == 1),
        key=lambda a: int(a.get("anchor_id") or 0),
    )
    eligible_other = sorted(
        (a for a in eligible if int(a.get("level") or 0) != 1),
        key=lambda a: int(a.get("anchor_id") or 0),
    )

    topic_results: dict[str, dict] = {}

    for topic_key, cfg in SECTION_TOPICS.items():
        matched_anchor_ids: set[int] = set()
        anchor_match_method: dict[int, str] = {}
        method_parts: set[str] = set()
        max_conf: float = 0.0

        # Pass A: parent-first (level-1 anchors only).
        for anchor in eligible_by_level1:
            aid = int(anchor["anchor_id"])
            if aid in matched_anchor_ids:
                continue
            if (anchor.get("token") or "") in EXCLUDED_TOKENS:
                continue
            method = _match_topic(anchor, cfg)
            if not method:
                continue

            subtree = descendants_map.get(aid, {aid})
            matched_anchor_ids |= subtree
            for sub_aid in subtree:
                anchor_match_method.setdefault(sub_aid, method)
            method_parts.add(method)

            if method == "schedule_heading":
                max_conf = max(max_conf, _CONF_PARENT_SCHEDULE)
            else:
                max_conf = max(max_conf, _CONF_PARENT_HEADING)

        # Pass B: orphan child anchors not covered by any parent match.
        for anchor in eligible_other:
            aid = int(anchor["anchor_id"])
            if aid in matched_anchor_ids:
                continue
            if (anchor.get("token") or "") in EXCLUDED_TOKENS:
                continue
            method = _match_topic(anchor, cfg)
            if not method:
                continue

            subtree = descendants_map.get(aid, {aid})
            matched_anchor_ids |= subtree
            for sub_aid in subtree:
                anchor_match_method.setdefault(sub_aid, "orphan_heading")
            method_parts.add("orphan_heading")
            max_conf = max(max_conf, _CONF_ORPHAN)

        # Collect evidence_ids from the claimed subtree, skipping DEFINITIONS.
        eids_unsorted: list[str] = []
        seen: set[str] = set()
        for aid in matched_anchor_ids:
            anchor = by_id.get(aid) or {}
            if (anchor.get("token") or "") in EXCLUDED_TOKENS:
                continue
            for eid in anchor_evidence.get(aid) or []:
                if eid in manifest_eids and eid not in seen:
                    eids_unsorted.append(eid)
                    seen.add(eid)

        # Pass C: body-phrase scan for operative text not reached by headings.
        body_hits = _collect_body_phrase_eids(
            body_phrases=cfg.get("body_phrases") or [],
            box_assignments=box_assignments,
            manifest_eids=manifest_eids,
            eid_lookup=eid_lookup,
            seg_page_range=seg_page_range,
            seen=seen,
            body_phrase_requires=cfg.get("body_phrase_requires"),
        )
        if body_hits:
            eids_unsorted.extend(body_hits)
            seen.update(body_hits)
            method_parts.add("body_phrase")
            max_conf = max(max_conf, _CONF_BODY_PHRASE)

        evidence_ids = sorted(
            eids_unsorted, key=lambda e: eid_position.get(e, 10**9)
        )

        if seg_page_range and evidence_ids:
            evidence_ids = [
                eid
                for eid in evidence_ids
                if seg_page_range[0]
                <= int((eid_lookup.get(eid) or {}).get("page_number") or 0)
                <= seg_page_range[1]
            ]

        # Reduce matched anchors to roots (drop descendants whose ancestor is
        # also matched) so structure_anchor_ids reads as the topic's anchor
        # entry points, not every descendant.
        match_methods = {
            "schedule_heading",
            "heading_keyword",
            "orphan_heading",
        }
        matched_match_only = {
            aid
            for aid in matched_anchor_ids
            if anchor_match_method.get(aid) in match_methods
            and aid in by_id
            and (by_id[aid].get("token") or "") not in EXCLUDED_TOKENS
        }
        roots: list[int] = []
        for aid in sorted(matched_match_only):
            anc = by_id.get(aid) or {}
            parent_id = anc.get("parent_id")
            if parent_id is not None and int(parent_id) in matched_match_only:
                continue
            roots.append(aid)
        structure_anchor_ids = [
            by_id[aid].get("node_id")
            for aid in roots
            if by_id[aid].get("node_id")
        ]

        pages = _page_range_for_eids(evidence_ids, eid_lookup)
        method = "+".join(sorted(method_parts)) if method_parts else "no_match"
        warnings: list[str] = []

        if not evidence_ids:
            method = "no_match"
            max_conf = 0.0
            warnings.append(
                f"No evidence found for topic '{topic_key}' in segment '{segment_id}'"
            )
        elif max_conf < _CONF_LOW_WARN:
            warnings.append(
                f"Low confidence ({max_conf:.2f}) for topic '{topic_key}' in segment '{segment_id}'"
            )

        start_eid = evidence_ids[0] if evidence_ids else None
        end_eid = evidence_ids[-1] if evidence_ids else None

        topic_results[topic_key] = {
            "title": cfg.get("description", topic_key),
            "segment_id": segment_id,
            "pages": pages,
            "start_evidence_id": start_eid,
            "end_evidence_id": end_eid,
            "evidence_ids": evidence_ids,
            "structure_anchor_ids": structure_anchor_ids,
            "definition_terms": def_terms_by_topic.get(topic_key) or [],
            "confidence": round(max_conf, 3),
            "method": method,
            "warnings": warnings,
        }

    return topic_results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_section_map(
    manifest: dict,
    structure_map: dict,
    segment_map: dict | None = None,
    definitions_index: dict | None = None,
) -> dict:
    """Build semantic_section_map.json.

    Args:
        manifest:          evidence_manifest dict.
        structure_map:     structure_map dict (anchors_flat + box_assignments).
        segment_map:       segment_map dict (optional; used for page-range clipping).
        definitions_index: definitions_index dict (optional; provides hint metadata only).

    Returns:
        semantic_section_map dict ready for JSON serialisation.
    """
    doc_id = manifest.get("doc_id", "unknown")
    evidence: list[dict] = manifest.get("evidence") or []
    manifest_eids: set[str] = {r["evidence_id"] for r in evidence}
    eid_lookup: dict[str, dict] = {r["evidence_id"]: r for r in evidence}

    if definitions_index is None:
        definitions_index = {"definitions": []}

    seg_lookup: dict[str, dict] = {}
    if segment_map:
        for seg in segment_map.get("segments") or []:
            seg_lookup[seg["segment_id"]] = seg

    topics_per_segment: list[dict] = []

    for seg_struct in structure_map.get("segments") or []:
        seg_id = seg_struct.get("segment_id", "whole_doc")
        segment = seg_lookup.get(seg_id)

        topic_results = _route_segment(
            seg_struct,
            segment,
            manifest_eids,
            eid_lookup,
            definitions_index,
        )

        topics_per_segment.append(
            {
                "segment_id": seg_id,
                "topics": topic_results,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": doc_id,
        "segments": topics_per_segment,
    }

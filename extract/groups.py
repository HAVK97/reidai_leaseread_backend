"""Extraction group configuration — dataclasses and loader.

A group is a declarative unit that specifies:
- which semantic topics to include (from semantic_section_map)
- which whole sections to include (family:token pairs from structure_map)
- which definitions to pull (normalized term allowlist)
- fields to extract with typed schemas and decision rules
- model profile to use (overridable at CLI)
- prompt text (system + user templates)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Root config directory (relative to this file)
_CONFIG_ROOT = Path(__file__).parent / "config"
_GROUPS_DIR = _CONFIG_ROOT / "groups"


GROUP_RUN_ORDER = [
    "parties",
    "property",
    "lease_details",
    "financials",
    "permitted_use",
    "statutory_status",
    "rent_review_header",
    "omv_disregards",
    "omv_assumptions",
    "repairs_decorations",
    "alterations",
    "alienation",
    "break_rights",
    "third_party",
    "rights_granted",
    "insurance",
    "service_charge",
]


@dataclass
class FieldSchema:
    """Schema and decision rule for a single extracted field."""

    field_id: str
    label: str
    format: str  # yes_no | yes_no_na | yes_no_with_detail | date | frequency | enum | text
    allowed_values: list[str] = field(default_factory=list)  # for enum fields
    decision_rule: str = ""  # natural-language rule for the model
    required: bool = True


@dataclass
class SectionRef:
    """Reference to a whole structural section via family + token."""

    family: str  # e.g. front_matter
    token: str  # e.g. LEASE_PARTICULARS


@dataclass
class GroupConfig:
    """Full configuration for one extraction group."""

    group_id: str
    label: str
    description: str

    # --- evidence sources ---
    semantic_topics: list[str] = field(default_factory=list)
    whole_sections: list[SectionRef] = field(default_factory=list)
    definition_allowlist: list[str] = field(default_factory=list)

    # --- field definitions ---
    fields: list[FieldSchema] = field(default_factory=list)

    # --- model / prompts ---
    model_profile: str = "gemini-3-flash-preview"  # default; overridable at CLI
    system_prompt: str = ""
    user_prompt_template: str = ""

    # --- extraction policy ---
    # When evidence is found in multiple segments, prefer current_lease by default
    source_priority: list[str] = field(
        default_factory=lambda: [
            "current_lease",
            "lease_summary",
            "definitions",
            "annexed_old_lease",
        ]
    )

    # --- schema / versioning ---
    schema_version: str = "1.0"
    prompt_version: str = "1.0"

    def get_field(self, field_id: str) -> FieldSchema | None:
        for f in self.fields:
            if f.field_id == field_id:
                return f
        return None


def _parse_field(raw: dict[str, Any]) -> FieldSchema:
    return FieldSchema(
        field_id=raw["field_id"],
        label=raw["label"],
        format=raw["format"],
        allowed_values=raw.get("allowed_values", []),
        decision_rule=raw.get("decision_rule", ""),
        required=raw.get("required", True),
    )


def _parse_section_ref(raw: str | dict) -> SectionRef:
    """Accept 'family:token' string or {'family':..,'token':..} dict."""
    if isinstance(raw, str):
        parts = raw.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"whole_sections entry must be 'family:token', got: {raw!r}")
        return SectionRef(family=parts[0], token=parts[1])
    return SectionRef(family=raw["family"], token=raw["token"])


def load_group_config(group_id: str) -> GroupConfig:
    """Load a group config from extract/config/groups/<group_id>.json."""
    path = _GROUPS_DIR / f"{group_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Group config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    return GroupConfig(
        group_id=raw.get("group_id", group_id),
        label=raw.get("label", group_id),
        description=raw.get("description", ""),
        semantic_topics=raw.get("semantic_topics", []),
        whole_sections=[_parse_section_ref(s) for s in raw.get("whole_sections", [])],
        definition_allowlist=raw.get("definition_allowlist", []),
        fields=[_parse_field(f) for f in raw.get("fields", [])],
        model_profile=raw.get("model_profile", "gemini-3-flash-preview"),
        system_prompt=raw.get("system_prompt", ""),
        user_prompt_template=raw.get("user_prompt_template", ""),
        source_priority=raw.get(
            "source_priority",
            ["current_lease", "lease_summary", "definitions", "annexed_old_lease"],
        ),
        schema_version=raw.get("schema_version", "1.0"),
        prompt_version=raw.get("prompt_version", "1.0"),
    )


def list_available_groups() -> list[str]:
    """Return group_ids in extraction dependency order."""
    if not _GROUPS_DIR.exists():
        return []
    available = {p.stem for p in _GROUPS_DIR.glob("*.json")}
    ordered = [group_id for group_id in GROUP_RUN_ORDER if group_id in available]
    extras = sorted(available - set(ordered))
    return ordered + extras


def load_model_profiles() -> dict:
    """Load model_profiles.json and return full dict."""
    path = _CONFIG_ROOT / "model_profiles.json"
    if not path.exists():
        raise FileNotFoundError(f"model_profiles.json not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_model_profile(profile_name: str) -> dict:
    """Return the named model profile dict."""
    profiles = load_model_profiles()
    p = profiles.get("profiles", {}).get(profile_name)
    if p is None:
        available = list(profiles.get("profiles", {}).keys())
        raise ValueError(
            f"Unknown model profile {profile_name!r}. Available: {available}"
        )
    return p

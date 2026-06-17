"""CLI entry-point for the extract package.

Commands:
  run           Run LLM extraction for one or all groups on a document.
  pack          Assemble and print the evidence pack without calling the LLM.
  list-groups   Print available extraction groups.
  list-profiles Print available model profiles.

Usage:
  python -m extract run  --dir preextract_outputs/Henry_Unit_2 --group rent_review_header
  python -m extract run  --dir preextract_outputs/Henry_Unit_2 --all
  python -m extract run  --dir preextract_outputs/Henry_Unit_2 --group omv_disregards --model-profile gpt_strict
  python -m extract pack --dir preextract_outputs/Henry_Unit_2 --group rent_review_header
  python -m extract list-groups
  python -m extract list-profiles
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    from .groups import list_available_groups
    from .runner import run_all_groups, run_group

    preextract_dir = Path(args.dir)
    if not preextract_dir.is_dir():
        print(f"ERROR: directory not found: {preextract_dir}", file=sys.stderr)
        return 1

    if args.all:
        group_ids = list_available_groups()
        if not group_ids:
            print("ERROR: no groups found in extract/config/groups/", file=sys.stderr)
            return 1
    elif args.group:
        group_ids = [args.group]
    else:
        print("ERROR: specify --group <id> or --all", file=sys.stderr)
        return 1

    # Build model override map from --set-model key=value pairs
    model_overrides: dict[str, str] = {}
    for spec in args.set_model or []:
        if "=" not in spec:
            print(f"ERROR: --set-model must be 'group_id=profile', got: {spec!r}", file=sys.stderr)
            return 1
        gid, profile = spec.split("=", 1)
        model_overrides[gid.strip()] = profile.strip()

    if args.all:
        def _on_group_start(group_id: str, _index: int, _total: int) -> None:
            _print_section(f"Group: {group_id}")

        def _on_group_complete(group_id: str, envelope: dict, _index: int, _total: int) -> None:
            if args.dry_run:
                print(f"Pack ({len(envelope.get('input_evidence_ids', []))} rows):")
                print(envelope.get("pack_text", ""))
                return
            vs = envelope.get("validation_summary", {})
            print(
                f"  found={vs.get('found',0)}  not_present={vs.get('not_present',0)}  "
                f"unclear={vs.get('unclear',0)}  needs_review={vs.get('needs_review',0)}"
            )
            if envelope.get("omv_short_circuit"):
                print(f"  [INFO] {envelope.get('omv_short_circuit_reason')}")
            for w in envelope.get("assembly_warnings", []):
                print(f"  [WARN] {w}")

        try:
            run_all_groups(
                preextract_dir=preextract_dir,
                group_ids=group_ids,
                model_profile_override=args.model_profile,
                model_overrides=model_overrides,
                dry_run=args.dry_run,
                run_id=args.run_id,
                on_group_start=_on_group_start,
                on_group_complete=_on_group_complete,
            )
            return 0
        except Exception as e:
            print(f"ERROR running all groups: {e}", file=sys.stderr)
            return 1

    exit_code = 0
    for group_id in group_ids:
        _print_section(f"Group: {group_id}")
        profile_override = model_overrides.get(group_id) or args.model_profile

        try:
            envelope = run_group(
                preextract_dir=preextract_dir,
                group_id=group_id,
                model_profile_override=profile_override,
                dry_run=args.dry_run,
                run_id=args.run_id,
            )
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            exit_code = 1
            continue
        except Exception as e:
            print(f"ERROR running {group_id}: {e}", file=sys.stderr)
            exit_code = 1
            continue

        if args.dry_run:
            print(f"Pack ({len(envelope.get('input_evidence_ids', []))} rows):")
            print(envelope.get("pack_text", ""))
        else:
            vs = envelope.get("validation_summary", {})
            print(
                f"  found={vs.get('found',0)}  not_present={vs.get('not_present',0)}  "
                f"unclear={vs.get('unclear',0)}  needs_review={vs.get('needs_review',0)}"
            )
            warnings = envelope.get("assembly_warnings", [])
            for w in warnings:
                print(f"  [WARN] {w}")

    return exit_code


def cmd_pack(args: argparse.Namespace) -> int:
    """Assemble and print (or save) the evidence pack without calling the LLM."""
    from .artefacts import ArtefactLoader
    from .assembler import EvidencePackAssembler
    from .groups import load_group_config

    preextract_dir = Path(args.dir)
    if not preextract_dir.is_dir():
        print(f"ERROR: directory not found: {preextract_dir}", file=sys.stderr)
        return 1

    try:
        loader = ArtefactLoader.from_dir(preextract_dir)
        group = load_group_config(args.group)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    assembler = EvidencePackAssembler(loader)
    pack_text, meta = assembler.build(group)

    if meta.warnings:
        for w in meta.warnings:
            print(f"[WARN] {w}")

    print(f"\n--- Evidence pack: {args.group} ({len(meta.input_evidence_ids)} rows) ---\n")
    print(pack_text)
    print(f"\n--- Block counts: {meta.block_counts} ---")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "doc_id": meta.doc_id,
                    "group_id": meta.group_id,
                    "input_evidence_ids": meta.input_evidence_ids,
                    "source_map": meta.source_map,
                    "block_counts": meta.block_counts,
                    "warnings": meta.warnings,
                    "pack_text": pack_text,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"\n  wrote {out_path}")

    return 0


def cmd_list_groups(_args: argparse.Namespace) -> int:
    from .groups import list_available_groups

    groups = list_available_groups()
    if not groups:
        print("No groups found in extract/config/groups/")
        return 0
    print("Available extraction groups:")
    for g in groups:
        print(f"  {g}")
    return 0


def cmd_list_profiles(_args: argparse.Namespace) -> int:
    from .groups import load_model_profiles

    try:
        profiles = load_model_profiles()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print("Available model profiles:")
    for name, p in profiles.get("profiles", {}).items():
        print(f"  {name:25s}  provider={p['provider']}  model={p['model']}")

    print("\nGroup defaults:")
    for gid, profile in profiles.get("group_defaults", {}).items():
        print(f"  {gid:30s} -> {profile}")

    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract",
        description="ReidAI LLM extraction stage",
    )
    sub = parser.add_subparsers(dest="command")

    # -- run --
    p_run = sub.add_parser("run", help="Run LLM extraction for a group or all groups")
    p_run.add_argument("--dir", required=True, metavar="DIR",
                       help="Path to preextract_outputs/<doc_id>/ directory")
    grp = p_run.add_mutually_exclusive_group(required=False)
    grp.add_argument("--group", metavar="GROUP_ID",
                     help="Specific group to run (e.g. rent_review_header)")
    grp.add_argument("--all", action="store_true",
                     help="Run all available groups")
    p_run.add_argument("--model-profile", default=None, metavar="PROFILE",
                       help="Override model profile for all groups (e.g. gpt_strict)")
    p_run.add_argument("--set-model", action="append", default=[], metavar="GROUP=PROFILE",
                       help="Override model profile for a specific group, repeatable")
    p_run.add_argument("--run-id", default=None, metavar="RUN_ID",
                       help="Unique run ID (default: ISO timestamp)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Build evidence pack only, skip LLM call")

    # -- pack --
    p_pack = sub.add_parser("pack", help="Assemble evidence pack without calling LLM")
    p_pack.add_argument("--dir", required=True, metavar="DIR",
                        help="Path to preextract_outputs/<doc_id>/ directory")
    p_pack.add_argument("--group", required=True, metavar="GROUP_ID",
                        help="Group to assemble")
    p_pack.add_argument("--out", default=None, metavar="PATH",
                        help="Save pack JSON to this path")

    # -- list-groups --
    sub.add_parser("list-groups", help="List available extraction groups")

    # -- list-profiles --
    sub.add_parser("list-profiles", help="List available model profiles")

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return cmd_run(args)
    if args.command == "pack":
        return cmd_pack(args)
    if args.command == "list-groups":
        return cmd_list_groups(args)
    if args.command == "list-profiles":
        return cmd_list_profiles(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

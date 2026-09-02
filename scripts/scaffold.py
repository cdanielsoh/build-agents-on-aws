#!/usr/bin/env python3
"""Materialize a project template into a destination directory.

Templates live in `templates/<name>/` at the plugin root. This script copies
one into place so the files land on disk verbatim instead of being re-typed
from a skill document.

Usage:
    scaffold.py --list
    scaffold.py <dest> [--template strands-agentcore] [--force] [--dry-run]

Exit codes:
    0  success
    1  usage / template error
    2  destination conflict (use --force to overwrite)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PLUGIN_ROOT / "templates"

# Never copied into a scaffolded project.
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}

DEFAULT_TEMPLATE = "strands-agentcore"


def list_templates() -> list[str]:
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir())


def iter_template_files(root: Path):
    """Yield (source_path, relative_path) for every file to copy."""
    for path in sorted(root.rglob("*")):
        if any(part in EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        if path.is_file():
            yield path, path.relative_to(root)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="scaffold.py",
        description="Copy a build-agents-on-aws project template into a directory.",
    )
    parser.add_argument(
        "dest",
        nargs="?",
        help="Destination directory. Created if missing. Use '.' for the current directory.",
    )
    parser.add_argument(
        "--template",
        default=DEFAULT_TEMPLATE,
        help=f"Template name (default: {DEFAULT_TEMPLATE}). See --list.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files that already exist in the destination.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching the filesystem.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available templates and exit.",
    )
    args = parser.parse_args()

    available = list_templates()

    if args.list:
        if not available:
            print(f"No templates found in {TEMPLATES_DIR}", file=sys.stderr)
            return 1
        print("Available templates:")
        for name in available:
            manifest = TEMPLATES_DIR / name / "TEMPLATE.md"
            summary = ""
            if manifest.is_file():
                summary = manifest.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
            print(f"  {name}" + (f" — {summary}" if summary else ""))
        return 0

    if not args.dest:
        parser.error("dest is required unless --list is given")

    if args.template not in available:
        print(
            f"Unknown template {args.template!r}. Available: {', '.join(available) or '(none)'}",
            file=sys.stderr,
        )
        return 1

    src_root = TEMPLATES_DIR / args.template
    dest_root = Path(args.dest).expanduser().resolve()

    files = list(iter_template_files(src_root))
    if not files:
        print(f"Template {args.template!r} contains no files.", file=sys.stderr)
        return 1

    conflicts = [rel for _, rel in files if (dest_root / rel).exists()]
    if conflicts and not args.force:
        print(
            f"{len(conflicts)} file(s) already exist in {dest_root}:",
            file=sys.stderr,
        )
        for rel in conflicts[:10]:
            print(f"  {rel}", file=sys.stderr)
        if len(conflicts) > 10:
            print(f"  ... and {len(conflicts) - 10} more", file=sys.stderr)
        print("\nRe-run with --force to overwrite, or choose an empty directory.", file=sys.stderr)
        return 2

    verb = "Would write" if args.dry_run else "Wrote"
    for src, rel in files:
        target = dest_root / rel
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    print(f"{verb} {len(files)} file(s) from template {args.template!r} to {dest_root}")

    guide = dest_root / "TEMPLATE.md"
    if not args.dry_run and guide.is_file():
        print(f"\nNext steps are in {guide}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

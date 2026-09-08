#!/usr/bin/env python3
"""Every relative link in every markdown file, including its `#anchor`.

Why anchors and not just paths: a path check passes on `playbook.md#the-phase-that-moved`, so a
heading rename silently turns a cross-reference into a link that lands at the top of a 200-line
file. Seven such links were already broken when this was first run, in three different skills —
each written correctly and then orphaned by an edit to the heading it pointed at.

Slug rules follow GitHub's `github-slugger`, and two of its details are what made the first
version of this script report false failures. Both are worth stating because they are easy to get
wrong in the other direction:

  - **Underscores survive.** `LOG_ONLY` slugs to `log_only`, not `logonly`. Stripping `_` as an
    emphasis marker made a correct link look broken, and "fixing" it broke a working one.
  - **Runs of whitespace are not collapsed.** Each whitespace character becomes its own hyphen, so
    a heading containing an em dash — which is deleted, leaving the spaces either side — slugs to a
    DOUBLE hyphen. `## A — B` is `#a--b`.

Exit 1 on any break, so this can gate a commit.
"""
from __future__ import annotations

import pathlib
import re
import sys

SKIP = ("node_modules", "local-plans", ".git")


def slug(heading: str) -> str:
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)   # [text](url) -> text
    h = re.sub(r"[`*]", "", h)                             # code ticks and emphasis
    a = re.sub(r"[^\w\s-]", "", h.lower()).strip()         # keep word chars, spaces, hyphens
    return re.sub(r"\s", "-", a)                           # each space -> one hyphen


def anchors(text: str) -> set[str]:
    # Fenced blocks only. Inline code must survive here, because GitHub drops the backticks and
    # KEEPS what is inside them: `## Native Tool Search (`x_amz_...`)` anchors to
    # `#native-tool-search-x_amz_...`. Blanking inline code made that correct link report broken,
    # and the obvious "fix" was to edit a link that had been right all along.
    return {slug(h) for h in re.findall(r"^#{1,6}\s+(.*?)\s*$", strip_fenced(text), re.M)}


def strip_fenced(text: str) -> str:
    """Blank fenced blocks, preserving line count. A `#` comment inside a Python sample is not a
    heading, and a fenced block is where link-shaped code lives."""
    out, fenced = [], False
    for line in text.splitlines():
        if re.match(r"\s*(```|~~~)", line):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else line)
    return "\n".join(out)


def strip_code(text: str) -> str:
    """Fenced blocks and inline code both go, for LINK extraction only.

    Necessary, not tidiness: `PROFILES["support"](app_ctx)` in a Python sample is a valid markdown
    link as far as any regex is concerned, and it reported as a missing file called `app_ctx` for as
    long as this check has existed. A checker that cries wolf on real code gets ignored, which costs
    more than the seven genuine breaks it found.
    """
    return re.sub(r"`[^`]*`", "", strip_fenced(text))


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    bad: list[str] = []
    n_links = 0
    for md in sorted(root.rglob("*.md")):
        if any(s in str(md) for s in SKIP):
            continue
        for m in re.finditer(r"\[[^\]]*\]\(([^)\s]*?)(#[^)\s]+)?\)", strip_code(md.read_text())):
            path, frag = m.group(1), m.group(2)
            if path.startswith(("http", "mailto", "tel:")):
                continue
            n_links += 1
            target = md if not path else (md.parent / path)
            if not target.exists():
                bad.append(f"{md}: no such file: {path}")
                continue
            if frag and target.suffix == ".md" and frag[1:] not in anchors(target.read_text()):
                bad.append(f"{md} -> {path or md.name}{frag}: no heading slugs to that anchor")
    for line in bad:
        print(f"BROKEN  {line}")
    print(f"\n{n_links} relative links checked, {len(bad)} broken")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

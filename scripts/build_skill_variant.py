#!/usr/bin/env python3
"""Generate one isolated single-line variant of SKILL.md and verify it before use.

A/B-comparing a SKILL.md edit needs two arms that differ in exactly one line, so
the measured delta can be attributed to that line alone. This builder applies one
edit (add / edit / remove) from a base SKILL.md, writes the result under
<out>/<variant-name>/SKILL.md as a ready-to-pass --skill-dir, and refuses to
proceed unless difflib shows the variant differs from the base by exactly one
contiguous hunk. A naive per-line position count is wrong for an insertion:
shifting lines make every line after it look different, so the gate counts hunks
and changed content lines instead (the same difflib approach check_eval_cases.py
and local_model_eval.py rely on; do not switch to PowerShell diff, which mangles
long table rows).

The edit is anchored on content, never an absolute line index: SKILL.md shifts
over time and a positional key reintroduced a confound at L138/L159 in the prior
session. If no line or more than one matches the anchor, this aborts rather than
guessing.

After writing, it also parses the variant's frontmatter with PyYAML and reports
the resulting `description` length against the skill's 1024-char cap (flagged if
over). The field is a YAML folded block scalar (`description: >`) so it must be
parsed by a real YAML parser, not counted line-by-line as a regex would.

Usage:
  python scripts/build_skill_variant.py [--base PATH] [--out DIR] \
      --variant-name NAME --mode {edit,remove,add} --anchor TEXT \
      [--replace TEXT | --insert TEXT] [--regex] [--cap 1024]
"""
import argparse
import difflib
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASE = REPO / "skills" / "home-assistant-best-practices" / "SKILL.md"

# Public Agent Skills spec caps the frontmatter `description` at 1024 chars to bound
# context-window use; every compliant runtime (Claude Code plugin, Codex, ...) enforces
# it, so an over-budget skill would fail to load regardless of content quality. This is
# the default for the --cap option below: if the spec ever moves, change this one number.
DESCRIPTION_CAP = 1024


def anchor_lines(lines, text, as_regex):
    """Indices of lines whose content contains `text` (or matches it as regex)."""
    hit = []
    for i, line in enumerate(lines):
        body = re.sub(r"[\r\n]+$", "", line)
        if (re.search(text, body) if as_regex else text in body):
            hit.append(i)
    return hit


def apply_edit(base_text, mode, anchor, replacement, as_regex):
    """Return the variant text: one add/edit/remove anchored on content."""
    lines = base_text.splitlines(keepends=True)
    hit = anchor_lines(lines, anchor, as_regex)
    if not hit:
        sys.exit(f"anchor {anchor!r} matched no line in the base file")
    if len(hit) > 1:
        shown = ", ".join(str(i + 1) for i in hit)
        sys.exit(f"anchor {anchor!r} matched {len(hit)} lines ({shown}); make it "
                 "more specific or drop --regex")
    i = hit[0]
    if mode == "remove":
        return "".join(line for j, line in enumerate(lines) if j != i)
    if mode == "edit":
        if not replacement:
            sys.exit("--replace text is required for --mode edit")
        lines[i] = replacement + lines[i][len(lines[i].rstrip("\r\n")):]
        return "".join(lines)
    if mode == "add":
        if not replacement:
            sys.exit("--insert text is required for --mode add")
        term = lines[i][len(lines[i].rstrip("\r\n")):]
        return "".join(lines[:i + 1] + [replacement + term] + lines[i + 1:])
    sys.exit(f"unknown mode {mode!r} (use edit, remove, or add)")


def one_hunk_gate(base_text, variant_text):
    """True iff the unified diff is exactly one hunk that changes at most one line.

    One contiguous edit — insertion (+), deletion (-), or replacement (-/+) — is a
    single @@ hunk with one changed content line (two for a replacement). A
    confound where two non-adjacent lines changed produces two hunks and fails here.
    """
    diff = list(difflib.unified_diff(
        base_text.splitlines(keepends=True),
        variant_text.splitlines(keepends=True),
        fromfile="base", tofile="variant", lineterm=""))
    changed = [d for d in diff
               if d[:1] in ("+", "-") and not d.startswith(("+++", "---"))]
    removed = sum(d.startswith("-") for d in changed)
    added = sum(d.startswith("+") for d in changed)
    hunks = sum(1 for d in diff if d.startswith("@@"))
    ok = hunks == 1 and (removed + added) >= 1 and removed <= 1 and added <= 1
    return ok, diff, removed, added


def description_length(skill_text):
    """Length of the parsed frontmatter `description` folded block scalar."""
    parts = skill_text.split("---", 2)
    if len(parts) < 3:
        return None
    fm = yaml.safe_load(parts[1]) or {}
    desc = fm.get("description")
    return len(desc.strip()) if isinstance(desc, str) else None


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base", type=Path, default=DEFAULT_BASE,
                   help="base SKILL.md (default: the repo's home-assistant skill)")
    p.add_argument("--out", type=Path, default=REPO / "build-variants",
                   help="output directory for <variant-name>/SKILL.md")
    p.add_argument("--variant-name", required=True, help="dir name under --out")
    p.add_argument("--mode", required=True, choices=("edit", "remove", "add"))
    p.add_argument("--anchor", required=True,
                   help="substring (or regex with --regex) the edit is anchored on")
    p.add_argument("--replace", help="new line content for --mode edit")
    p.add_argument("--insert", help="line to insert after the anchor for --mode add")
    p.add_argument("--regex", action="store_true",
                   help="treat --anchor as a regular expression, not a substring")
    p.add_argument("--cap", type=int, default=DESCRIPTION_CAP,
                   help="description frontmatter cap in chars (default: 1024)")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    if not a.base.is_file():
        sys.exit(f"base file not found: {a.base}")
    base_text = a.base.read_text(encoding="utf-8")
    variant_text = apply_edit(base_text, a.mode, a.anchor,
                              a.replace if a.mode == "edit" else a.insert, a.regex)

    ok, diff, removed, added = one_hunk_gate(base_text, variant_text)
    print("=== unified diff (base -> variant) ===")
    sys.stdout.write("".join(diff) + "\n")
    if not ok:
        sys.exit(f"gate FAILED: the variant is not a single isolated edit "
                  f"(hunks={sum(1 for d in diff if d.startswith('@@'))}, "
                  f"removed={removed}, added={added}); aborting before any run")

    cap = description_length(variant_text)
    if cap is None:
        print("budget: no parseable frontmatter `description`; skipped")
    elif cap <= a.cap:
        print(f"budget: description = {cap} chars (cap {a.cap}) [OK]")
    else:
        sys.exit(f"gate FAILED: description = {cap} chars exceeds the "
                 f"{a.cap}-char spec cap; aborting before writing so no over-budget variant is produced")

    out_dir = a.out / a.variant_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SKILL.md").write_text(variant_text, encoding="utf-8")

    print(f"gate OK: exactly one hunk, {removed + added} content line(s) changed -> "
          f"{out_dir.as_posix()}/SKILL.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())

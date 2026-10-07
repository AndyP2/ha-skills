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
      [--replace TEXT | --insert TEXT]
"""
import argparse
import difflib
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASE = REPO / "skills" / "home-assistant-best-practices" / "SKILL.md"

# Public Agent Skills spec caps the frontmatter `description` at 1024 chars to bound
# context-window use; every compliant runtime (Claude Code plugin, Codex, ...) enforces
# it, so an over-budget skill would fail to load regardless of content quality. If the
# spec ever changes, update this single constant.
DESCRIPTION_CAP = 1024


def anchor_lines(lines, text):
    """Indices of lines whose content contains `text`."""
    hit = []
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if text in body:
            hit.append(index)
    return hit


def apply_edit(base_text, mode, anchor, replacement):
    """Return the variant text: one add/edit/remove anchored on content."""
    lines = base_text.splitlines(keepends=True)
    hit = anchor_lines(lines, anchor)
    if not hit:
        sys.exit(f"anchor {anchor!r} matched no line in the base file")
    if len(hit) > 1:
        shown = ", ".join(str(index + 1) for index in hit)
        sys.exit(f"anchor {anchor!r} matched {len(hit)} lines ({shown}); make it "
                 "more specific")
    index = hit[0]
    if mode == "remove":
        return "".join(line for other_index, line in enumerate(lines) if other_index != index)
    if mode == "edit":
        if not replacement:
            sys.exit("--replace text is required for --mode edit")
        lines[index] = replacement + lines[index][len(lines[index].rstrip("\r\n")):]
        return "".join(lines)
    if mode == "add":
        if not replacement:
            sys.exit("--insert text is required for --mode add")
        anchor_line = lines[index]
        anchor_ending = anchor_line[len(anchor_line.rstrip("\r\n")):]
        if index == len(lines) - 1 and not anchor_ending:
            sys.exit(f"anchor {anchor!r} is the last line and has no line ending; "
                     "add cannot insert cleanly")
        return "".join(lines[:index + 1] + [replacement + anchor_ending] + lines[index + 1:])
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
    changed = [diff_line for diff_line in diff
               if diff_line[:1] in ("+", "-") and not diff_line.startswith(("+++", "---"))]
    removed = sum(diff_line.startswith("-") for diff_line in changed)
    added = sum(diff_line.startswith("+") for diff_line in changed)
    hunks = sum(1 for diff_line in diff if diff_line.startswith("@@"))
    ok = hunks == 1 and (removed + added) >= 1 and removed <= 1 and added <= 1
    return ok, diff, removed, added


def description_length(skill_text):
    """Length of the parsed frontmatter `description` folded block scalar.

    Frontmatter is delimited by whole lines whose content is exactly `---`. A
    naive substring split on `---` would treat an indented `---` inside a folded
    `description` as a delimiter and measure only a prefix, so scan for the
    closing delimiter line and parse the block between the two delimiter lines.
    """
    lines = skill_text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    closing = next(
        (index for index, line in enumerate(lines[1:], 1)
         if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        return None
    try:
        frontmatter = yaml.safe_load("".join(lines[1:closing])) or {}
    except yaml.YAMLError:
        return None
    description = frontmatter.get("description")
    return len(description.strip()) if isinstance(description, str) else None


def parse_args():
    """Parse the command-line arguments into a namespace."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE,
                        help="base SKILL.md (default: the repo's home-assistant skill)")
    parser.add_argument("--out", type=Path, default=REPO / "build-variants",
                        help="output directory for <variant-name>/SKILL.md")
    parser.add_argument("--variant-name", required=True, help="dir name under --out")
    parser.add_argument("--mode", required=True, choices=("edit", "remove", "add"))
    parser.add_argument("--anchor", required=True,
                        help="the substring the edit is anchored on")
    parser.add_argument("--replace", help="new line content for --mode edit")
    parser.add_argument("--insert", help="line to insert after the anchor for --mode add")
    return parser.parse_args()


def main() -> int:
    """Apply the requested edit to the base SKILL.md and verify it before writing."""
    args = parse_args()
    if not args.base.is_file():
        sys.exit(f"base file not found: {args.base}")
    base_text = args.base.read_text(encoding="utf-8", newline="")
    variant_text = apply_edit(base_text, args.mode, args.anchor,
                              args.replace if args.mode == "edit" else args.insert)

    ok, diff, removed, added = one_hunk_gate(base_text, variant_text)
    print("=== unified diff (base -> variant) ===")
    sys.stdout.write("".join(diff) + "\n")
    if not ok:
        sys.exit(f"gate FAILED: the variant is not a single isolated edit "
                 f"(hunks={sum(1 for diff_line in diff if diff_line.startswith('@@'))}, "
                 f"removed={removed}, added={added}); aborting before any run")

    desc_chars = description_length(variant_text)
    if desc_chars is None:
        sys.exit("gate FAILED: no parseable frontmatter `description`; aborting "
                 "before writing so an unreadable variant is not produced")
    elif desc_chars <= DESCRIPTION_CAP:
        print(f"budget: description = {desc_chars} chars (cap {DESCRIPTION_CAP}) [OK]")
    else:
        sys.exit(f"gate FAILED: description = {desc_chars} chars exceeds the "
                 f"{DESCRIPTION_CAP}-char spec cap; aborting before writing so no over-budget variant is produced")

    variant_path = Path(args.variant_name)
    if (variant_path.is_absolute() or any(part in ("..", ".") for part in variant_path.parts)
            or len(variant_path.parts) != 1):
        sys.exit(f"--variant-name {args.variant_name!r} must be a single relative "
                 "directory component; no absolute paths, '..' or path separators")
    out_dir = args.out / variant_path
    output_path = out_dir / "SKILL.md"
    if output_path.resolve() == args.base.resolve():
        sys.exit("refusing to overwrite --base with the variant output")
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(variant_text, encoding="utf-8", newline="")

    print(f"gate OK: exactly one hunk, {removed + added} content line(s) changed -> "
          f"{out_dir.as_posix()}/SKILL.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())

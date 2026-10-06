#!/usr/bin/env python3
"""Summarise local_model_eval runs by label: ingestion (`reads`) rate and mean
score / per-grader pass (the "does the change matter" view).

`local_model_eval.py` writes one <case>-<label>-r<n>.json per run, each carrying a
`label`, a `model`, a `score`, a `graders` map of boolean grader verdicts, and a
`reads` list of files the model loaded. This groups those runs by their `label`
field and prints, for each label: how many runs there were, the reads rate (what
fraction actually loaded the skill — a change can fix the answer while silently
breaking ingestion), the mean score with per-run scores, per-grader pass counts,
and any distinct error values. Run one invocation with `--label baseline,post` to
print both arms together and read the delta across BOTH dimensions; there is no
separate compare tool because the delta is meant to be judged by a human on both.

Grader verdicts are counted as stored: `local_model_eval.grade()` already returns
the correct truth value for both `contains` and `not_contains` graders, so nothing
is flipped.

Usage: python scripts/summarise_runs.py <case> [--out evals/results] \
    [--label baseline,post] [--model <substr>]
"""
import glob
import json
import os
import sys
from collections import defaultdict


def summarise(case, out, labels=None, model_sub=None):
    """Group the case's runs by label and print per-label ingestion/score stats."""
    found = {}
    for f in sorted(glob.glob(os.path.join(out, f"{case}-*-r*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  skip {os.path.basename(f)}: {e}")
            continue
        lab = d.get("label")
        if not lab or (labels and lab not in labels):
            continue
        model = d.get("model") or ""
        if model_sub and not model.startswith(model_sub):
            continue
        found.setdefault(lab, []).append(d)

    if not found:
        print(f"no runs for case {case!r} in {out}")
        return

    for lab in sorted(found):
        rs = found[lab]
        n = len(rs)
        loaded = sum(1 for r in rs if r.get("reads"))
        rr = loaded / n if n else float("nan")
        mean = sum(r.get("score", 0) for r in rs) / n if n else float("nan")
        grades = defaultdict(lambda: [0, 0])
        for r in rs:
            for k, v in (r.get("graders") or {}).items():
                grades[k][0] += 1 if v else 0
                grades[k][1] += 1
        errs = sorted({r.get("error") for r in rs if r.get("error")})
        model_seen = next((r.get("model") for r in rs if r.get("model")), "?")
        print(f"=== {lab}   runs={n}   model={model_seen}")
        print(f"   reads_rate(SKILL loaded) = {loaded}/{n} = {rr:.3f}" if n else "   (no runs)")
        print(f"   mean score = {mean:.3f}   scores={[round(r.get('score', 0), 2) for r in rs]}" if n else "")
        for k, (p, t) in sorted(grades.items()):
            print(f"     {k}: {p}/{t} pass = {(p / t if t else 0):.3f}")
        if errs:
            print("   ERRORS:", set(errs))


def main() -> int:
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: python scripts/summarise_runs.py <case> [--out DIR] "
                 "[--label baseline,post] [--model SUBSTR]")
    case = args[0]
    out = os.environ.get("LOCAL_MODEL_OUT", "evals/results")
    labels = None
    model_sub = None
    i = 1
    while i < len(args):
        arg = args[i]
        if "=" in arg:
            key, val = arg.split("=", 1)
            i += 1
        elif arg in ("--out", "--label", "--model"):
            if i + 1 >= len(args):
                sys.exit(f"{arg} requires a value")
            key, val = arg, args[i + 1]
            i += 2
        else:
            i += 1
            continue
        if key == "--out":
            out = val
        elif key == "--label":
            labels = (labels or []) + [l for l in val.split(",") if l]
        elif key == "--model":
            model_sub = val
    summarise(case, out, labels, model_sub)
    return 0


if __name__ == "__main__":
    sys.exit(main())

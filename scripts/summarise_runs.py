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


def summarise(case, out, labels=None, model_filter=None):
    """Group the case's runs by label and print per-label ingestion/score stats."""
    found = {}
    for file_path in sorted(glob.glob(os.path.join(out, f"{case}-*-r*.json"))):
        try:
            record = json.load(open(file_path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"  skip {os.path.basename(file_path)}: {error}")
            continue
        run_label = record.get("label")
        if not run_label or (labels and run_label not in labels):
            continue
        if record.get("case") != case:
            continue
        model = record.get("model") or ""
        if model_filter and model_filter not in model:
            continue
        found.setdefault(run_label, []).append(record)

    if not found:
        print(f"no runs for case {case!r} in {out}")
        return

    for run_label in sorted(found):
        runs = found[run_label]
        run_count = len(runs)
        loaded = sum(1 for run in runs if "SKILL.md" in (run.get("reads") or []))
        reads_rate = loaded / run_count if run_count else float("nan")
        mean_score = sum(run.get("score", 0) for run in runs) / run_count if run_count else float("nan")
        grades = defaultdict(lambda: [0, 0])
        for run in runs:
            for grader_name, verdict in (run.get("graders") or {}).items():
                grades[grader_name][0] += 1 if verdict else 0
                grades[grader_name][1] += 1
        errors = sorted({run.get("error") for run in runs if run.get("error")})
        model_seen = next((run.get("model") for run in runs if run.get("model")), "?")
        print(f"=== {run_label}   runs={run_count}   model={model_seen}")
        print(f"   reads_rate(SKILL loaded) = {loaded}/{run_count} = {reads_rate:.3f}" if run_count else "   (no runs)")
        print(f"   mean score = {mean_score:.3f}   scores={[round(run.get('score', 0), 2) for run in runs]}" if run_count else "")
        for grader_name, (pass_count, total_count) in sorted(grades.items()):
            print(f"     {grader_name}: {pass_count}/{total_count} pass = {(pass_count / total_count if total_count else 0):.3f}")
        if errors:
            print("   ERRORS:", set(errors))


def main() -> int:
    """Parse argv into <case> + optional filters and run the summarisation."""
    arguments = sys.argv[1:]
    if not arguments:
        sys.exit("usage: python scripts/summarise_runs.py <case> [--out DIR] "
                 "[--label baseline,post] [--model SUBSTR]")
    case = arguments[0]
    results_dir = os.environ.get("LOCAL_MODEL_OUT", "evals/results")
    labels = None
    model_filter = None
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if "=" in argument:
            key, value = argument.split("=", 1)
            index += 1
        elif argument in ("--out", "--label", "--model"):
            if index + 1 >= len(arguments):
                sys.exit(f"{argument} requires a value")
            key, value = argument, arguments[index + 1]
            index += 2
        else:
            sys.exit(f"unknown argument {argument!r}; "
                     f"usage: python scripts/summarise_runs.py <case> [--out DIR] "
                     f"[--label baseline,post] [--model SUBSTR]")
        if key == "--out":
            results_dir = value
        elif key == "--label":
            labels = (labels or []) + [run_label for run_label in value.split(",") if run_label]
        elif key == "--model":
            model_filter = value
    summarise(case, results_dir, labels, model_filter)
    return 0


if __name__ == "__main__":
    sys.exit(main())

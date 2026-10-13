"""Single accuracy tracker for all GLiNER/Laya gate evaluations.

Workbook: approval_accuracy_tracker.xlsx
  sheet `runs`     - one row per run: model, context variant, score, error split, rule
  sheet `failures` - every failed row with its scores (keyed by run_id)

Usage:
  python tracker.py                     # (re)build from existing results CSVs
  from tracker import append_run        # called by gliner_large_test.py after each run
"""
import os, sys, csv, glob, datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import openpyxl
from openpyxl.styles import Font

TRACKER = "approval_accuracy_tracker.xlsx"
RUNS_COLS = ["run_id", "timestamp", "model", "variant", "dataset_file", "results_file",
             "total", "correct", "accuracy_pct", "false_AUTO", "false_HUMAN",
             "verdict_rule", "notes"]
FAIL_COLS = ["run_id", "example_id", "domain", "tool_name", "ground_truth", "predicted",
             "needs_human", "nh_conf", "risk", "risk_conf", "label_reason"]
RULE = "HUMAN if needs_human==yes OR risk==dangerous else AUTO"


def _score(rows):
    "Return (correct, false_AUTO, false_HUMAN) for result rows using their stored verdict."
    correct = sum(1 for r in rows if r["correct"] == "Y")
    false_auto = sum(1 for r in rows if r["ground_truth"] == "HUMAN" and r["predicted"] == "AUTO")
    false_human = sum(1 for r in rows if r["ground_truth"] == "AUTO" and r["predicted"] == "HUMAN")
    return correct, false_auto, false_human


def append_run(model, variant, results_file, dataset_file="", notes="", verdict_rule=RULE,
               timestamp=None):
    "Append one run from a results CSV (gliner-style schema). Returns run_id."
    rows = list(csv.DictReader(open(results_file, encoding="utf-8-sig")))
    correct, false_auto, false_human = _score(rows)
    failures = [{c: r.get(c) for c in FAIL_COLS} for r in rows if r["correct"] == "N"]
    return append_run_summary(model, variant, len(rows), correct, false_auto, false_human,
                              dataset_file=dataset_file, results_file=results_file,
                              notes=notes, failures=failures, verdict_rule=verdict_rule,
                              timestamp=timestamp)


def append_run_summary(model, variant, total, correct, false_auto=None, false_human=None,
                       dataset_file="", results_file="", notes="", failures=(),
                       verdict_rule=RULE, timestamp=None):
    "Append one run (summary + prebuilt failure dicts) directly - for imported/legacy results."
    wb = openpyxl.load_workbook(TRACKER) if os.path.exists(TRACKER) else openpyxl.Workbook()
    runs = wb["runs"] if "runs" in wb.sheetnames else None
    if runs is None:
        runs = wb.create_sheet("runs", 0)
        runs.append(RUNS_COLS)
    fails = wb["failures"] if "failures" in wb.sheetnames else wb.create_sheet("failures")
    if fails.max_row == 1 and fails["A1"].value is None:
        fails.delete_rows(1)  # openpyxl leaves a phantom row on fresh sheets
    if fails["A1"].value is None:
        fails.append(FAIL_COLS)
    # run_id = model-short + variant + date, unique across repeats
    short = model.split("/")[-1]
    stamp = timestamp or datetime.datetime.now()
    date_str = stamp[:10] if isinstance(stamp, str) else stamp.date().isoformat()
    run_id = f"{short}:{variant}:{date_str}"
    n = sum(1 for r in runs.iter_rows(min_row=2, values_only=True) if r and str(r[0]).startswith(run_id))
    if n:
        run_id = f"{run_id}#{n + 1}"
    runs.append([run_id, stamp if isinstance(stamp, str) else stamp.strftime("%Y-%m-%d %H:%M"),
                 model, variant, dataset_file, results_file, total, correct,
                 round(correct / total, 4) if total else None, false_auto, false_human,
                 verdict_rule, notes])
    for r in failures:
        fails.append([run_id] + [r.get(c) for c in FAIL_COLS if c != "run_id"])
    for ws in (runs, fails):
        ws.freeze_panes = "A2"
        for c in ws[1]:
            c.font = Font(bold=True)
        for col in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 46)
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    wb.save(TRACKER)
    acc = f"{correct / total:.0%}" if total else "n/a"
    print(f"  tracker: +run {run_id} -> {correct}/{total} ({acc}), "
          f"false_AUTO={false_auto} false_HUMAN={false_human} failures={len(list(failures))}")
    return run_id


if __name__ == "__main__":
    "Rebuild summary from any gliner_results_*.csv present in the folder."
    seen = {}
    for f in sorted(glob.glob("gliner_results_*.csv")):
        variant = "with_context" if "with_context" in f else "no_context"
        seen[(variant, f)] = True
    if not seen:
        print("  no gliner_results_*.csv found")
    for (variant, f) in seen:
        append_run("fastino/GLiNER2.5-Decide", variant, f,
                   dataset_file="approval_dataset_with_context.csv" if variant == "with_context"
                   else "approval_dataset_no_context.csv",
                   notes="seeded from results CSV; labels = independent review")

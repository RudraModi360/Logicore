"""One-off: three-model comparison + Kev failure breakdown from the results CSVs."""
import csv
from collections import Counter

def load(p):
    return {r["example_id"]: r for r in csv.DictReader(open(p, encoding="utf-8-sig"))}

g_n, g_c = load("experiments/gliner_results_no_context.csv"), load("experiments/gliner_results_with_context.csv")
k_n, k_c = load("experiments/results_kev_no_context.csv"), load("experiments/results_kev_with_context.csv")
l_n, l_c = load("experiments/results_laya_no_context.csv"), load("experiments/results_laya_with_context.csv")

print("=== verdict table (correct / false_AUTO / false_HUMAN) ===")
for name, n, c in [("GLiNER2.5", g_n, g_c), ("Kev-0.8B", k_n, k_c), ("Laya-typed", l_n, l_c)]:
    for tag, d in [("no_ctx", n), ("with_ctx", c)]:
        ok = sum(1 for r in d.values() if r["correct"] == "Y")
        fa = sum(1 for r in d.values() if r["ground_truth"] == "HUMAN" and r["predicted"] == "AUTO")
        fh = sum(1 for r in d.values() if r["ground_truth"] == "AUTO" and r["predicted"] == "HUMAN")
        print(f"  {name:11} {tag:9} {ok:3}/151 ({ok/151:.0%})  false_AUTO={fa:2}  false_HUMAN={fh:2}")

print()
print("=== Kev WITH-context failures by domain ===")
fails = [r for r in k_c.values() if r["correct"] == "N"]
for dom, rs in sorted(Counter(r["domain"] for r in fails).items()):
    print(f"  {dom}: {rs}")
print("  missed-HUMAN rows (false_AUTO):")
for r in sorted((r for r in fails if r["ground_truth"] == "HUMAN"), key=lambda x: x["example_id"]):
    print(f"    {r['example_id']} {r['tool_name']:<24} nh={r['needs_human']}({r['nh_conf']}) risk={r['risk']}({r['risk_conf']})")
print("  false-HUMAN rows:")
for r in sorted((r for r in fails if r["ground_truth"] == "AUTO"), key=lambda x: x["example_id"]):
    print(f"    {r['example_id']} {r['tool_name']:<24} {r['label_reason']} nh={r['needs_human']}({r['nh_conf']}) risk={r['risk']}({r['risk_conf']})")

print()
print("=== head-to-head, no_context ===")
both = sum(1 for i in g_n if g_n[i]["correct"] == "Y" and k_n[i]["correct"] == "Y")
g_only = sum(1 for i in g_n if g_n[i]["correct"] == "Y" and k_n[i]["correct"] == "N")
k_only = sum(1 for i in g_n if g_n[i]["correct"] == "N" and k_n[i]["correct"] == "Y")
neither = 151 - both - g_only - k_only
print(f"  both right {both} | GLiNER only {g_only} | Kev only {k_only} | neither {neither}")
print("  GLiNER-only rows:", sorted(i for i in g_n if g_n[i]["correct"] == "Y" and k_n[i]["correct"] == "N"))
print("  Kev-only rows:   ", sorted(i for i in g_n if g_n[i]["correct"] == "N" and k_n[i]["correct"] == "Y"))

"""Headline comparison: reference alone vs baseline + all judge/refine rounds.

    python harness/headline.py out-final-pelican out-final-monkey ...

Per run: what asking the reference model directly costs, what the loop costs
(Haiku drawings + Opus judgments), and the quality reached (judge totals).
Reads only summary.json and usage.csv. Writes eval/headline.md and eval/headline.json.
"""
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL = REPO / "eval"


def costs(run):
    by_role = {}
    with (run / "usage.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            by_role[r["role"]] = by_role.get(r["role"], 0.0) + float(r["cost_usd"])
    return by_role


def row(run):
    run = Path(run)
    s = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    c = costs(run)
    h = s["history"]
    final = next((x for x in h if x["round"] == s["final_round"]), h[-1])
    haiku = c.get("baseline", 0) + c.get("refine", 0)
    loop = haiku + c.get("judge", 0)
    return {
        "run": run.name,
        "prompt": s["prompt"],
        "stop_reason": s["stop_reason"],
        "rounds": s["rounds"],
        "judgments": len(h) + sum(1 for x in h if x.get("confirmation")),
        "confirmations_rejected": sum(1 for x in h if x.get("confirmation")
                                      and not (x["confirmation"]["parity"]
                                               or x["confirmation"]["attempt_at_least_reference"])),
        "haiku_total_start": h[0]["attempt_total"],
        "haiku_total_final": final["attempt_total"],
        "reference_total_final": final["reference_total"],
        "cost_reference_usd": round(c.get("reference", 0), 4),
        "cost_haiku_usd": round(haiku, 4),
        "cost_judge_usd": round(c.get("judge", 0), 4),
        "cost_loop_usd": round(loop, 4),
        "loop_vs_reference": round(loop / c["reference"], 2) if c.get("reference") else None,
        "reserved_unmeasured_usd": s.get("reserved_unmeasured_usd", 0),
    }


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    rows = [row(r) for r in argv]
    EVAL.mkdir(exist_ok=True)
    (EVAL / "headline.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    md = ["# Headline: reference alone vs baseline + judge/refine loop\n\n",
          "Totals are the judge's sum of 5 criteria (max 25) in the final round's judgment. "
          "Loop = Haiku drawings (baseline + refines) + all Opus judgments.\n\n",
          "| run | stop | rounds | Haiku total start → final | reference total | "
          "Fable alone $ | Haiku $ | judge $ | loop $ | loop / Fable |\n",
          "|---|---|---|---|---|---|---|---|---|---|\n"]
    for r in rows:
        md.append(f"| {r['run']} | {r['stop_reason']} | {r['rounds']} | "
                  f"{r['haiku_total_start']} → {r['haiku_total_final']} | {r['reference_total_final']} | "
                  f"{r['cost_reference_usd']:.4f} | {r['cost_haiku_usd']:.4f} | {r['cost_judge_usd']:.4f} | "
                  f"{r['cost_loop_usd']:.4f} | {r['loop_vs_reference']}× |\n")
    n = len(rows)
    md.append(f"\nAverage over {n} runs: Fable alone ${sum(r['cost_reference_usd'] for r in rows) / n:.4f}, "
              f"Haiku drawings ${sum(r['cost_haiku_usd'] for r in rows) / n:.4f}, "
              f"judge ${sum(r['cost_judge_usd'] for r in rows) / n:.4f}, "
              f"loop ${sum(r['cost_loop_usd'] for r in rows) / n:.4f}.\n")
    (EVAL / "headline.md").write_text("".join(md), encoding="utf-8")
    print("-> eval/headline.md, eval/headline.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

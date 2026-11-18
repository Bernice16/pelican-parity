"""Judge calibration: judge one fixed pair of PNGs N times with the real judge.

    python harness/calibrate.py --name self --prompt prompts/pelican.txt \
        --a out-m1-test/rounds/00/reference.png --b out-m1-test/rounds/00/reference.png \
        --times 2 --budget-usd 1

Same judge model, prompt, isolation and verdict validation as a real run. The
A/B order is fixed (no shuffle) so that repeated judgments are comparable.
Every session goes through the same ledgers as a run (role=calibration), and
is resumable: rerunning the same command pays only for missing judgments.

Working files (raw JSON, session dirs) go to out-calib-<name>/ (not committed);
the results go to eval/calibration-<name>.json (committed).
"""
import argparse
import json
import shutil
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import judge  # noqa: E402
import main as harness  # noqa: E402
from runner import SessionError  # noqa: E402

REPO_ROOT = harness.REPO_ROOT


def prepare(workdir, a_png, b_png):
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    shutil.copyfile(a_png, workdir / "A.png")
    shutil.copyfile(b_png, workdir / "B.png")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="calibrate")
    ap.add_argument("--name", required=True, help="short name: out-calib-NAME/, eval/calibration-NAME.json")
    ap.add_argument("--prompt", required=True, type=Path)
    ap.add_argument("--a", required=True, type=Path, help="PNG shown as A")
    ap.add_argument("--b", required=True, type=Path, help="PNG shown as B")
    ap.add_argument("--times", type=int, default=3)
    ap.add_argument("--budget-usd", required=True, type=float)
    args = ap.parse_args(argv)

    cfg = harness.load_config()
    # Same model, effort, tools and step cap as the judge; its own ledger role.
    cfg["roles"]["calibration"] = dict(cfg["roles"]["judge"])
    cfg["max_step_usd"]["calibration"] = cfg["max_step_usd"]["judge"]
    drawing_prompt = args.prompt.read_text(encoding="utf-8").rstrip("\n")
    judge_prompt = judge.build_prompt(REPO_ROOT / cfg["judge_prompt"], drawing_prompt)
    out_dir = REPO_ROOT / f"out-calib-{args.name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    judgments = []
    try:
        for i in range(args.times):
            data = harness.paid_session(
                cfg, "calibration", judge_prompt, cfg["judge_instructions"], out_dir, i,
                args.budget_usd, "calib", prepare=lambda wd: prepare(wd, args.a, args.b))
            v, _ = judge.parse_verdict(data.get("result", ""), cfg["criteria"])
            a, b = v["images"]["A"], v["images"]["B"]
            judgments.append({
                "session_id": data.get("session_id"),
                "A_scores": a["scores"], "B_scores": b["scores"],
                "A_total": sum(a["scores"].values()), "B_total": sum(b["scores"].values()),
                "A_defects": len(a["defects"]), "B_defects": len(b["defects"]),
                "parity": v["parity"], "rule_parity": judge.rule_parity(v, cfg["parity_max_total_gap"]),
                "parity_reason": v["parity_reason"],
            })
            j = judgments[-1]
            print(f"[calib {i:02d}] A={j['A_total']} B={j['B_total']} parity={j['parity']}", flush=True)
    except (harness.BudgetRefused, SessionError, judge.VerdictError) as e:
        print(f"STOP: {e}  (rerun the same command to resume)", file=sys.stderr)

    def spread(key):
        vals = [j[key] for j in judgments]
        return {"values": vals, "min": min(vals), "max": max(vals),
                "mean": round(statistics.mean(vals), 2)} if vals else None

    result = {
        "name": args.name,
        "prompt": drawing_prompt,
        "A": Path(args.a).as_posix(),
        "B": Path(args.b).as_posix(),
        "same_image": Path(args.a).resolve() == Path(args.b).resolve(),
        "judge": f"{cfg['roles']['judge']['model']} ({cfg['roles']['judge']['effort']})",
        "judge_prompt": cfg["judge_prompt"],
        "judgments": judgments,
        "A_total": spread("A_total"),
        "B_total": spread("B_total"),
        "parity_count": f"{sum(j['parity'] for j in judgments)}/{len(judgments)}",
        "cost_usd": round(harness.ledger.total_cost(out_dir / "usage.csv"), 7),
    }
    eval_dir = REPO_ROOT / "eval"
    eval_dir.mkdir(exist_ok=True)
    path = eval_dir / f"calibration-{args.name}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"parity {result['parity_count']}  A totals {result['A_total'] and result['A_total']['values']}  "
          f"B totals {result['B_total'] and result['B_total']['values']}  "
          f"cost ${result['cost_usd']:.4f}  -> {path.relative_to(REPO_ROOT).as_posix()}")
    return 0 if len(judgments) == args.times else 1


if __name__ == "__main__":
    sys.exit(main())

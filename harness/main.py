"""pelican-parity harness, milestone M1 (walking skeleton).

./run --prompt FILE --budget-usd N --out DIR

M1 scope: reference (round 00) + baseline (round 00), end to end:
budget check -> claude -p session -> usage row -> SVG extract/validate -> PNG.
The judge/refine loop comes in M2.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger  # noqa: E402
from runner import SessionError, check_session_ok, run_session, usage_row  # noqa: E402
from svgtools import (  # noqa: E402
    RenderError, SvgError, extract_svg, render_png, renderer_version, validate_svg,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_LEDGER = Path(os.environ.get("HARNESS_LEDGER", REPO_ROOT / "ledger" / "usage.csv"))


class BudgetRefused(RuntimeError):
    pass


def load_config() -> dict:
    path = Path(os.environ.get("HARNESS_CONFIG", REPO_ROOT / "config.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def live(role, round_no, row, spent, budget):
    tokens = (f"in={row['input_tokens']:,} out={row['output_tokens']:,} "
              f"cache_r={row['cache_read_tokens']:,} cache_w={row['cache_write_tokens']:,}")
    print(f"[round {round_no:02d}] {role:<9} {row['model']:<18} {tokens}  "
          f"step=${row['cost_usd']:.4f}  total=${spent:.4f}  left=${budget - spent:.4f}",
          flush=True)


def log_incident(out_dir, role, round_no, message, detail=""):
    """Append to DIR/incidents.log: anything that makes the numbers need explaining."""
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with (out_dir / "incidents.log").open("a", encoding="utf-8") as f:
        f.write(f"{stamp} round={round_no:02d} role={role} {message}\n")
        if detail:
            f.write("    " + detail.replace("\n", "\n    ") + "\n")
    print(f"[round {round_no:02d}] {role:<9} INCIDENT: {message}", file=sys.stderr, flush=True)


def record(out_dir, row):
    """Write one paid session to both ledgers (idempotent on session_id)."""
    ledger.append_row(out_dir / "usage.csv", row)
    ledger.append_row(PROJECT_LEDGER, row)


def reusable_session(raw_path, role, model):
    """A successful, already paid session saved on disk, or None."""
    if not raw_path.exists():
        return None
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    try:
        check_session_ok(data, role, model)
    except SessionError:
        return None
    return data


def archive_raw(raw_path, data, reason):
    """Move a raw output aside so the next attempt starts fresh. Its cost stays in the ledger."""
    sid = (data.get("session_id") or "unknown")[:8]
    raw_path.rename(raw_path.with_name(f"{raw_path.stem}.{reason}-{sid}.json"))


def draw_step(cfg, role, prompt, out_dir, round_no, budget, stem):
    """One drawing step. Resume rules (a paid, successful session is never re-run):
    - .done.json present            -> skip entirely
    - raw JSON of a successful run  -> reuse it, only redo extract/render
    - raw JSON of a failed run      -> archive it (cost already counted), pay a new session
    """
    round_dir = out_dir / "rounds" / f"{round_no:02d}"
    done_marker = round_dir / f"{stem}.done.json"
    svg_path, png_path = round_dir / f"{stem}.svg", round_dir / f"{stem}.png"
    raw_path = out_dir / "raw" / f"{round_no:02d}_{stem}.json"
    run_csv = out_dir / "usage.csv"
    role_cfg = cfg["roles"][role]

    if done_marker.exists() and svg_path.exists() and png_path.exists():
        done = json.loads(done_marker.read_text(encoding="utf-8"))
        print(f"[round {round_no:02d}] {role:<9} already done (session {done['session_id']}), "
              f"not re-run, cost ${done['cost_usd']:.4f} already counted", flush=True)
        return

    data = reusable_session(raw_path, role, role_cfg["model"])
    if data is not None:
        row, _ = usage_row(data, role, role_cfg["model"], role_cfg["effort"], round_no)
        record(out_dir, row)  # no-op if already there; fixes a crash between raw write and ledger
        print(f"[round {round_no:02d}] {role:<9} reusing paid session {row['session_id']} "
              f"(${row['cost_usd']:.4f} already counted), not re-run", flush=True)
    else:
        if raw_path.exists():
            archive_raw(raw_path, json.loads(raw_path.read_text(encoding="utf-8")), "failed")

        spent = ledger.total_cost(run_csv)
        remaining = budget - spent
        worst_case = cfg["max_step_usd"][role]
        if remaining < worst_case:
            raise BudgetRefused(
                f"{role}: refused, worst-case step cost ${worst_case:.2f} > "
                f"remaining budget ${remaining:.4f}"
            )

        print(f"[round {round_no:02d}] {role:<9} {role_cfg['model']} ({role_cfg['effort']}) "
              f"started, step cap ${worst_case:.2f}", flush=True)
        try:
            data = run_session(
                cfg, role, prompt, cfg["draw_instructions"],
                workdir=out_dir / ".sessions" / f"{round_no:02d}_{stem}",
                raw_path=raw_path,
                # The step may not spend more than its own worst case, so one
                # step can never eat the budget reserved for the next ones.
                max_budget_usd=min(worst_case, remaining),
            )
        except SessionError as e:
            log_incident(out_dir, role, round_no, str(e), e.detail)
            raise

        # Record the cost before anything else: a failed session is still paid for.
        row, warnings = usage_row(data, role, role_cfg["model"], role_cfg["effort"], round_no)
        record(out_dir, row)
        live(role, round_no, row, ledger.total_cost(run_csv), budget)
        for w in warnings:
            log_incident(out_dir, role, round_no, f"session {row['session_id']}: {w}")
        try:
            check_session_ok(data, role, role_cfg["model"])
        except SessionError as e:
            log_incident(out_dir, role, round_no, str(e))
            raise

    # From here on the session is paid and successful.
    round_dir.mkdir(parents=True, exist_ok=True)
    if not svg_path.exists():
        try:
            svg = extract_svg(data.get("result", ""))
            validate_svg(svg)
        except SvgError as e:
            # The answer itself is unusable: only a new session can fix it.
            archive_raw(raw_path, data, "invalid")
            log_incident(out_dir, role, round_no, f"{e} (session cost counted, rerun pays a new one)")
            raise
        svg_path.write_text(svg, encoding="utf-8")
    # A render failure is local (renderer missing, crash): the rerun only re-renders.
    render_png(cfg, svg_path, png_path)
    done_marker.write_text(json.dumps(
        {"session_id": data.get("session_id"),
         "cost_usd": usage_row(data, role, role_cfg["model"], role_cfg["effort"], round_no)[0]["cost_usd"]}),
        encoding="utf-8")


def write_summary(out_dir, prompt, stop_reason, final_svg):
    rows = ledger.read_rows(out_dir / "usage.csv")
    per_role = {r: 0.0 for r in ("reference", "baseline", "judge", "refine")}
    for r in rows:
        per_role[r["role"]] = per_role.get(r["role"], 0.0) + float(r["cost_usd"])
    summary = {
        "prompt": prompt,
        "parity": False,
        "rounds": 0,
        "stop_reason": stop_reason,
        "cost_usd": {k: round(v, 7) for k, v in per_role.items()},
        "total_cost_usd": round(sum(float(r["cost_usd"]) for r in rows), 7),
        "final_svg": final_svg,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="run")
    ap.add_argument("--prompt", required=True, type=Path, help="text file with the drawing prompt")
    ap.add_argument("--budget-usd", required=True, type=float, help="hard cap for the whole run")
    ap.add_argument("--out", required=True, type=Path, help="output directory")
    args = ap.parse_args(argv)

    if args.budget_usd <= 0:
        ap.error("--budget-usd must be positive")
    prompt = args.prompt.read_text(encoding="utf-8").rstrip("\n")
    if not prompt.strip():
        ap.error("prompt file is empty")
    cfg = load_config()
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"prompt: {prompt}")
    # Check the renderer before paying for any session.
    try:
        version = renderer_version(cfg)
    except RenderError as e:
        print(f"ERROR: {e}. Install it first (see RUNBOOK.md); nothing was spent.", file=sys.stderr)
        return 1
    print(f"budget: ${args.budget_usd:.2f}   renderer: {version}")

    stop_reason, final_svg, exit_code = "m1_skeleton_done", None, 0
    try:
        draw_step(cfg, "reference", prompt, out_dir, 0, args.budget_usd, "reference")
        draw_step(cfg, "baseline", prompt, out_dir, 0, args.budget_usd, "attempt")
        # Forward slashes on every OS, so summary.json reads the same everywhere.
        final_svg = (out_dir / "rounds" / "00" / "attempt.svg").as_posix()
    except BudgetRefused as e:
        stop_reason, exit_code = "budget", 2
        print(f"STOP: {e}", file=sys.stderr)
    except (SessionError, SvgError, RenderError) as e:
        stop_reason, exit_code = "error", 1
        print(f"ERROR: {e}  (rerun the same command to resume)", file=sys.stderr)

    s = write_summary(out_dir, prompt, stop_reason, final_svg)
    print(f"stop_reason={s['stop_reason']}  total=${s['total_cost_usd']:.4f}  -> {(out_dir / 'summary.json').as_posix()}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

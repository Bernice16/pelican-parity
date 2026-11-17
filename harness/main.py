"""pelican-parity harness.

./run --prompt FILE --budget-usd N --out DIR

Each step: budget check -> claude -p session -> usage row -> step output.
Round 00: reference + baseline drawings, then the blind judge.
Round NN (1..round_cap): the firewall turns a verdict into feedback, Haiku
refines that round's SVG, the judge compares it again. The source is the best
round, or the round just judged once the best round's verdict has been used:
every refine carries feedback never sent before (no Ralph loop).
A parity (or better) verdict is only accepted if a second judgment (same judge
model and prompt, new session, A and B swapped) agrees.
Stops on: parity, attempt_exceeds_reference, round_cap, stagnation, budget,
no_svg, no_new_feedback; or step_cap / error / interrupted (rerun the same command to resume).
Every step is resumable: rerunning the same command never pays twice. A session
killed before reporting its usage has its step cap reserved (reserved.csv).
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import firewall  # noqa: E402
import judge  # noqa: E402
import ledger  # noqa: E402
from runner import (  # noqa: E402
    SessionError, SessionLost, StepCapHit, check_session_ok, run_session, usage_row,
)
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


def live(role, round_no, row, out_dir, budget):
    tokens = (f"in={row['input_tokens']:,} out={row['output_tokens']:,} "
              f"cache_r={row['cache_read_tokens']:,} cache_w={row['cache_write_tokens']:,}")
    measured, held = ledger.total_cost(out_dir / "usage.csv"), reserved_total(out_dir)
    extra = f" (+${held:.2f} reserved)" if held else ""
    print(f"[round {round_no:02d}] {role:<9} {row['model']:<18} {tokens}  "
          f"step=${row['cost_usd']:.4f}  total=${measured:.4f}{extra}  "
          f"left=${budget - measured - held:.4f}", flush=True)


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


RESERVED_COLUMNS = ["role", "round", "reserved_usd", "reason"]


def reserve(out_dir, role, round_no, amount, reason):
    """A session died before reporting its usage: hold its worst case against the budget.

    Kept apart from usage.csv, which holds measured usage only.
    """
    path = out_dir / "reserved.csv"
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        if new:
            f.write(",".join(RESERVED_COLUMNS) + "\n")
        f.write(f"{role},{round_no},{amount:.4f},{reason}\n")


def reserved_total(out_dir):
    path = out_dir / "reserved.csv"
    if not path.exists():
        return 0.0
    lines = path.read_text(encoding="utf-8").splitlines()[1:]
    return round(sum(float(line.split(",")[2]) for line in lines if line.strip()), 4)


def spent(out_dir):
    """What the budget has to cover: measured cost + worst case of sessions lost mid-way."""
    return ledger.total_cost(out_dir / "usage.csv") + reserved_total(out_dir)


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


def paid_session(cfg, role, prompt, system_append, out_dir, round_no, budget, stem, prepare=None):
    """Return the JSON of a successful session for this step, paying for one only if needed.

    Resume rules (a paid, successful session is never re-run):
    - raw JSON of a successful run  -> reuse it, pay nothing
    - raw JSON of a failed run      -> archive it (cost already counted), pay a new session
    `prepare(workdir)` fills the session's working dir just before a new session starts.
    """
    raw_path = out_dir / "raw" / f"{round_no:02d}_{stem}.json"
    role_cfg = cfg["roles"][role]

    data = reusable_session(raw_path, role, role_cfg["model"])
    if data is not None:
        row, _ = usage_row(data, role, role_cfg["model"], role_cfg["effort"], round_no)
        record(out_dir, row)  # no-op if already there; fixes a crash between raw write and ledger
        print(f"[round {round_no:02d}] {role:<9} reusing paid session {row['session_id']} "
              f"(${row['cost_usd']:.4f} already counted), not re-run", flush=True)
        return data

    if raw_path.exists():
        archive_raw(raw_path, json.loads(raw_path.read_text(encoding="utf-8")), "failed")

    remaining = budget - spent(out_dir)
    worst_case = cfg["max_step_usd"][role]
    if remaining < worst_case:
        raise BudgetRefused(
            f"{role}: refused, worst-case step cost ${worst_case:.2f} > "
            f"remaining budget ${remaining:.4f}"
        )

    workdir = out_dir / ".sessions" / f"{round_no:02d}_{stem}"
    if prepare is not None:
        prepare(workdir)
    print(f"[round {round_no:02d}] {role:<9} {role_cfg['model']} ({role_cfg['effort']}) "
          f"started, step cap ${worst_case:.2f}", flush=True)
    cap = min(worst_case, remaining)
    try:
        data = run_session(
            cfg, role, prompt, system_append, workdir=workdir, raw_path=raw_path,
            # The step may not spend more than its own worst case, so one
            # step can never eat the budget reserved for the next ones.
            max_budget_usd=cap,
        )
    except SessionLost as e:
        reserve(out_dir, role, round_no, cap, "interrupted" if e.interrupted else "lost")
        log_incident(out_dir, role, round_no, f"{e}; reserved ${cap:.2f}", e.detail)
        raise
    except SessionError as e:
        log_incident(out_dir, role, round_no, str(e), e.detail)
        raise

    # Record the cost before anything else: a failed session is still paid for.
    row, warnings = usage_row(data, role, role_cfg["model"], role_cfg["effort"], round_no)
    record(out_dir, row)
    live(role, round_no, row, out_dir, budget)
    for w in warnings:
        log_incident(out_dir, role, round_no, f"session {row['session_id']}: {w}")
    if row["cost_usd"] > cap:
        # Claude Code checks --max-budget-usd between turns only, so a single
        # long turn can go past it. Measured cost is what counts.
        log_incident(out_dir, role, round_no,
                     f"session {row['session_id']} cost ${row['cost_usd']:.4f}, above its cap ${cap:.2f}")
    if spent(out_dir) > budget:
        log_incident(out_dir, role, round_no,
                     f"run budget exceeded: ${spent(out_dir):.4f} spent of ${budget:.2f}")
    try:
        check_session_ok(data, role, role_cfg["model"])
    except SessionError as e:
        log_incident(out_dir, role, round_no, str(e))
        raise
    return data


def draw_step(cfg, role, prompt, out_dir, round_no, budget, stem):
    """One drawing step: paid session -> SVG -> PNG. Skipped if .done.json is present."""
    round_dir = out_dir / "rounds" / f"{round_no:02d}"
    done_marker = round_dir / f"{stem}.done.json"
    svg_path, png_path = round_dir / f"{stem}.svg", round_dir / f"{stem}.png"
    raw_path = out_dir / "raw" / f"{round_no:02d}_{stem}.json"
    role_cfg = cfg["roles"][role]

    if done_marker.exists() and svg_path.exists() and png_path.exists():
        done = json.loads(done_marker.read_text(encoding="utf-8"))
        print(f"[round {round_no:02d}] {role:<9} already done (session {done['session_id']}), "
              f"not re-run, cost ${done['cost_usd']:.4f} already counted", flush=True)
        return

    data = paid_session(cfg, role, prompt, cfg["draw_instructions"], out_dir, round_no, budget, stem)

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


def judge_step(cfg, prompt, out_dir, round_no, budget, confirm=False):
    """Blind judge of rounds/NN/attempt.png against the reference. Returns the saved verdict.

    confirm=True is the second opinion asked before accepting parity: a new
    judge session with A and B swapped (verdict_confirm.json).
    Skipped if the verdict file exists. An invalid verdict is archived
    (its cost stays counted) and the judge is asked once more; a second invalid
    verdict stops the run.
    """
    role, role_cfg = "judge", cfg["roles"]["judge"]
    round_dir = out_dir / "rounds" / f"{round_no:02d}"
    stem = "judge_confirm" if confirm else "judge"
    label = "judge*" if confirm else "judge"
    verdict_path = round_dir / ("verdict_confirm.json" if confirm else "verdict.json")
    if verdict_path.exists():
        saved = json.loads(verdict_path.read_text(encoding="utf-8"))
        print(f"[round {round_no:02d}] {label:<9} already judged (session {saved['session_id']}), "
              f"not re-run, parity={saved['parity']}", flush=True)
        return saved

    mapping = judge.blind_mapping(round_dir / "blind.json")
    if confirm:
        mapping = {"A": mapping["B"], "B": mapping["A"]}
    pngs = {"reference": out_dir / "rounds" / "00" / "reference.png",
            "attempt": round_dir / "attempt.png"}
    judge_prompt = judge.build_prompt(REPO_ROOT / cfg["judge_prompt"], prompt)
    raw_path = out_dir / "raw" / f"{round_no:02d}_{stem}.json"

    for try_no in (1, 2):
        data = paid_session(cfg, role, judge_prompt, cfg["judge_instructions"], out_dir, round_no,
                            budget, stem, prepare=lambda wd: judge.prepare_workdir(wd, mapping, pngs))
        try:
            verdict, warnings = judge.parse_verdict(data.get("result", ""))
            break
        except judge.VerdictError as e:
            archive_raw(raw_path, data, "invalid")
            log_incident(out_dir, role, round_no, f"{e} (try {try_no}/2, session cost counted)")
            if try_no == 2:
                raise
    for w in warnings:
        log_incident(out_dir, role, round_no, w)

    by_role = judge.unblind(verdict, mapping)
    rule = judge.rule_parity(verdict)
    if rule != verdict["parity"]:
        log_incident(out_dir, role, round_no,
                     f"judge said parity={verdict['parity']} but its own scores give {rule} "
                     f"under the parity rule; the judge's answer is applied")
    saved = {
        "session_id": data.get("session_id"),
        "judge_prompt": cfg["judge_prompt"],
        "blind": mapping,
        "parity": verdict["parity"],
        "parity_reason": verdict["parity_reason"],
        "rule_parity": rule,
        "attempt_at_least_reference": judge.attempt_at_least_reference(by_role),
        "reference": by_role["reference"],
        "attempt": by_role["attempt"],
        "not_applicable": verdict["not_applicable"],
    }
    verdict_path.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    att, ref = by_role["attempt"], by_role["reference"]
    scores = " ".join(f"{c[:4]}={att['scores'][c]}/{ref['scores'][c]}" for c in judge.CRITERIA)
    print(f"[round {round_no:02d}] {label:<9} parity={'yes' if saved['parity'] else 'no'}  "
          f"attempt/ref {scores}  totals={totals(att)}/{totals(ref)}  "
          f"attempt defects={len(att['defects'])}" + ("  (A/B swapped)" if confirm else ""),
          flush=True)
    return saved


def format_problems(kept):
    if not kept:
        return ("No specific problem was listed. Improve the overall quality: fidelity to the "
                "prompt, coherence, readability, composition and craft.")
    return "\n".join(
        f"{i}. [{d['severity']}, {d['criterion']}] {d['what']}\n"
        f"   Where: {d['where']}\n   Expected: {d['expected']}"
        for i, d in enumerate(kept, 1))


def refine_prompt_step(cfg, prompt, out_dir, round_no, source_round):
    """Build (and save) what Haiku receives for round `round_no`: its own SVG from
    `source_round` and that round's firewalled feedback. Nothing else: from refine_v2
    on, not even the drawing prompt (refine_v1 carried it in {{DRAWING_PROMPT}})."""
    round_dir = out_dir / "rounds" / f"{round_no:02d}"
    round_dir.mkdir(parents=True, exist_ok=True)
    source_dir = out_dir / "rounds" / f"{source_round:02d}"
    verdict = json.loads((source_dir / "verdict.json").read_text(encoding="utf-8"))
    kept, dropped = firewall.filter_feedback(verdict)

    feedback_path = round_dir / "feedback.json"
    if not feedback_path.exists():  # log drops once, not on every resume
        for d, found in dropped:
            log_incident(out_dir, "firewall", round_no,
                         f"dropped a round {source_round:02d} defect that leaks ({', '.join(found)})",
                         json.dumps(d))
    feedback_path.write_text(json.dumps({
        "source_round": source_round,
        "kept": kept,
        "dropped": [{"defect": d, "leaks": found} for d, found in dropped],
    }, indent=2), encoding="utf-8")

    template = (REPO_ROOT / cfg["refine_prompt"]).read_text(encoding="utf-8")
    text = (template
            .replace("{{DRAWING_PROMPT}}", prompt)
            .replace("{{CURRENT_SVG}}", (source_dir / "attempt.svg").read_text(encoding="utf-8").strip())
            .replace("{{PROBLEMS}}", format_problems(kept)))
    reference_svg = (out_dir / "rounds" / "00" / "reference.svg").read_text(encoding="utf-8")
    firewall.check_refine_prompt(text, reference_svg)
    (round_dir / "refine_prompt.txt").write_text(text, encoding="utf-8")
    print(f"[round {round_no:02d}] firewall  refining round {source_round:02d}: "
          f"{len(kept)} defect(s) passed, {len(dropped)} dropped", flush=True)
    return text


def totals(entry):
    return sum(entry["scores"].values())


def rank(verdict):
    """Order attempts: higher total first, then fewer critical, then fewer major defects."""
    att = verdict["attempt"]
    sev = [d["severity"] for d in att["defects"]]
    return (totals(att), -sev.count("critical"), -sev.count("major"))


def history_entry(round_no, v):
    att, ref = v["attempt"], v["reference"]
    sev = [d["severity"] for d in att["defects"]]
    return {
        "round": round_no,
        "parity": v["parity"],
        "rule_parity": v["rule_parity"],
        "attempt_total": totals(att),
        "reference_total": totals(ref),
        "attempt_scores": att["scores"],
        "attempt_defects": {s: sev.count(s) for s in judge.SEVERITIES},
        "confirmation": None,
    }


def success(v):
    """The attempt is at least as good as the reference in this verdict."""
    return v["parity"] or v["attempt_at_least_reference"]


def round_cost_needed(cfg):
    """Worst case of one more round: refine (+1 retry if no SVG) + judge (+ confirmation)."""
    caps = cfg["max_step_usd"]
    return 2 * caps["refine"] + 2 * caps["judge"]


def refine_with_retry(cfg, refine_text, out_dir, round_no, budget):
    """Haiku's refine answer must hold a valid SVG; one new session is allowed if it does not.
    Returns False (and leaves a marker, so a resume does not pay again) after two failures."""
    marker = out_dir / "rounds" / f"{round_no:02d}" / "no_svg.json"
    if marker.exists():
        print(f"[round {round_no:02d}] refine    no usable SVG twice before, not re-run", flush=True)
        return False
    for try_no in (1, 2):
        try:
            draw_step(cfg, "refine", refine_text, out_dir, round_no, budget, "attempt")
            return True
        except SvgError as e:
            if try_no == 2:
                marker.write_text(json.dumps({"error": str(e)}), encoding="utf-8")
                return False
            print(f"[round {round_no:02d}] refine    no usable SVG, asking once more", flush=True)


def run_loop(cfg, prompt, out_dir, budget, state):
    """Rounds 00..round_cap. Fills `state` as it goes, so the summary is right even on an error."""
    draw_step(cfg, "reference", prompt, out_dir, 0, budget, "reference")
    draw_step(cfg, "baseline", prompt, out_dir, 0, budget, "attempt")
    state["final_round"] = 0
    round_cap, patience = cfg["round_cap"], cfg["stagnation_rounds"]
    best_round, best_rank, round_no = None, None, 0
    used_sources = set()  # rounds whose verdict already fed a refine

    while True:
        verdict = judge_step(cfg, prompt, out_dir, round_no, budget)
        entry = history_entry(round_no, verdict)
        state["history"].append(entry)
        state["rounds"] = round_no
        if best_round is None or rank(verdict) > best_rank:
            best_round, best_rank = round_no, rank(verdict)
        state["best_round"] = state["final_round"] = best_round

        if success(verdict):
            # A single judgment near the threshold is noisy, and a judge may
            # favour one position: accept only if a second judge, with A and B
            # swapped, agrees.
            conf = judge_step(cfg, prompt, out_dir, round_no, budget, confirm=True)
            entry["confirmation"] = {
                "parity": conf["parity"],
                "attempt_at_least_reference": conf["attempt_at_least_reference"],
                "attempt_total": totals(conf["attempt"]),
                "reference_total": totals(conf["reference"]),
            }
            if success(conf):
                state["final_round"] = round_no
                if verdict["parity"] and conf["parity"]:
                    state["parity"] = True
                    return "parity"
                return "attempt_exceeds_reference"
            print(f"[round {round_no:02d}] judge     not confirmed with A/B swapped, continuing",
                  flush=True)

        if round_no >= round_cap:
            return "round_cap"
        if round_no - best_round >= patience:
            return "stagnation"

        nxt = round_no + 1
        nxt_dir = out_dir / "rounds" / f"{nxt:02d}"
        if not (nxt_dir / "attempt.done.json").exists():
            remaining = budget - spent(out_dir)
            if remaining < round_cost_needed(cfg):
                print(f"STOP: a new round needs up to ${round_cost_needed(cfg):.2f} "
                      f"(refine + judge, with retry and confirmation), ${remaining:.4f} left",
                      flush=True)
                return "budget"
        # No Ralph loop: every refine must carry judge feedback not used before.
        # Start from the best round while its verdict is unused; once it has fed a
        # refine, start from the round just judged, whose verdict is always new.
        source = best_round if best_round not in used_sources else round_no
        used_sources.add(source)
        refine_text = refine_prompt_step(cfg, prompt, out_dir, nxt, source)
        for earlier in range(1, nxt):
            earlier_prompt = out_dir / "rounds" / f"{earlier:02d}" / "refine_prompt.txt"
            if earlier_prompt.exists() and earlier_prompt.read_text(encoding="utf-8") == refine_text:
                # Safety net (the source choice above should make this unreachable).
                print(f"STOP: round {nxt:02d} refine prompt is identical to round {earlier:02d}'s; "
                      "re-running an unchanged prompt is not allowed", flush=True)
                return "no_new_feedback"
        if not refine_with_retry(cfg, refine_text, out_dir, nxt, budget):
            return "no_svg"
        round_no = nxt


def write_summary(cfg, out_dir, prompt, budget, stop_reason, state):
    rows = ledger.read_rows(out_dir / "usage.csv")
    per_role = {r: 0.0 for r in ("reference", "baseline", "judge", "refine")}
    for r in rows:
        per_role[r["role"]] = per_role.get(r["role"], 0.0) + float(r["cost_usd"])
    final_round = state.get("final_round")
    final = None
    if final_round is not None:
        # Forward slashes on every OS, so summary.json reads the same everywhere.
        final = (out_dir / "rounds" / f"{final_round:02d}").as_posix()
    summary = {
        "prompt": prompt,
        "parity": state.get("parity", False),
        "stop_reason": stop_reason,
        "rounds": state.get("rounds", 0),
        "final_round": final_round,
        "final_svg": f"{final}/attempt.svg" if final else None,
        "final_png": f"{final}/attempt.png" if final else None,
        "reference_svg": (out_dir / "rounds" / "00" / "reference.svg").as_posix(),
        "history": state.get("history", []),
        "budget_usd": budget,
        "cost_usd": {k: round(v, 7) for k, v in per_role.items()},
        "total_cost_usd": round(sum(float(r["cost_usd"]) for r in rows), 7),
        # Sessions killed before reporting usage (timeout, Ctrl+C): their worst
        # case, held against the budget but not in usage.csv (not measured).
        "reserved_unmeasured_usd": reserved_total(out_dir),
        "models": {role: f"{c['model']} ({c['effort']})" for role, c in cfg["roles"].items()},
        "judge_prompt": cfg["judge_prompt"],
        "refine_prompt": cfg["refine_prompt"],
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

    state = {"history": []}
    exit_code = 0
    try:
        stop_reason = run_loop(cfg, prompt, out_dir, args.budget_usd, state)
    except BudgetRefused as e:
        # A single step did not fit (normally only reference/baseline: the loop
        # checks a whole round's worst case before starting it).
        stop_reason, exit_code = "budget", 2
        print(f"STOP: {e}", file=sys.stderr)
    except SessionLost as e:
        stop_reason, exit_code = ("interrupted", 130) if e.interrupted else ("error", 1)
        print(f"STOP: {e}  (rerun the same command to resume)", file=sys.stderr)
    except KeyboardInterrupt:
        # Ctrl+C outside a model session (rendering, file writes): nothing is lost.
        stop_reason, exit_code = "interrupted", 130
        print("STOP: interrupted (Ctrl+C)  (rerun the same command to resume)", file=sys.stderr)
    except StepCapHit as e:
        stop_reason, exit_code = "step_cap", 1
        print(f"STOP: {e}", file=sys.stderr)
    except (SessionError, SvgError, RenderError, judge.VerdictError) as e:
        stop_reason, exit_code = "error", 1
        print(f"ERROR: {e}  (rerun the same command to resume)", file=sys.stderr)

    s = write_summary(cfg, out_dir, prompt, args.budget_usd, stop_reason, state)
    reserved = f"  reserved=${s['reserved_unmeasured_usd']:.2f}" if s["reserved_unmeasured_usd"] else ""
    print(f"stop_reason={s['stop_reason']}  parity={'yes' if s['parity'] else 'no'}  "
          f"rounds={s['rounds']}  final={s['final_svg']}  total=${s['total_cost_usd']:.4f}{reserved}  "
          f"-> {(out_dir / 'summary.json').as_posix()}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

"""Offline test suite: replays every scenario against a fake `claude` and a fake renderer.

    python tests/run_offline.py        (or: tests/run_offline.sh)

No model is called and nothing is spent. Each scenario runs the real harness
(harness/main.py) in a fresh temp dir with its own run and project ledgers,
then checks stop reason, resume behaviour, firewall, and that the money adds up:
usage.csv total == summary.json total, and every row is in the project ledger.
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
PY = sys.executable
RESULTS = []


class Run:
    def __init__(self, base, name):
        self.dir = base / name
        self.out = self.dir / "out"
        self.calls = self.dir / "calls.txt"
        self.dir.mkdir(parents=True)

    def call_count(self):
        return len([line for line in self.calls.read_text(encoding="utf-8").splitlines()
                    if not line.startswith(" ")]) if self.calls.exists() else 0

    def summary(self):
        return json.loads((self.out / "summary.json").read_text(encoding="utf-8"))

    def rows(self, path=None):
        path = path or self.out / "usage.csv"
        if not path.exists():
            return []
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def file(self, rel):
        return self.out / rel


def make_config(base, **overrides):
    cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
    cfg["claude_bin"] = [PY, str(TESTS / "fake_claude.py")]
    cfg["render"]["cmd"] = [PY, str(TESTS / "fake_render.py"), "{svg}", "{png}"]
    cfg["render"]["version_cmd"] = [PY, str(TESTS / "fake_render.py"), "--version"]
    cfg.update(overrides)
    path = base / f"cfg_{abs(hash(json.dumps(overrides, sort_keys=True)))}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def harness(run, budget, cfg_path, **fake_env):
    env = dict(os.environ)
    env.pop("CLAUDE_BIN", None)
    env.update(HARNESS_CONFIG=str(cfg_path), HARNESS_LEDGER=str(run.dir / "project_ledger.csv"),
               FAKE_CALLS=str(run.calls), PYTHONIOENCODING="utf-8")
    env.update({k: str(v) for k, v in fake_env.items()})
    proc = subprocess.run([PY, str(REPO / "harness" / "main.py"), "--prompt",
                           str(REPO / "prompts" / "pelican.txt"), "--budget-usd", str(budget),
                           "--out", str(run.out)],
                          env=env, capture_output=True, text=True, encoding="utf-8")
    return proc


def money_adds_up(run):
    rows = run.rows()
    total = round(sum(float(r["cost_usd"]) for r in rows), 6)
    project = {r["session_id"] for r in run.rows(run.dir / "project_ledger.csv")}
    s = run.summary()
    return (abs(total - s["total_cost_usd"]) < 1e-6
            and all(r["session_id"] in project for r in rows))


def check(name, conditions, proc=None):
    failed = [label for label, ok in conditions if not ok]
    RESULTS.append((name, not failed))
    print(f"{'PASS' if not failed else 'FAIL'}  {name}")
    for label in failed:
        print(f"      failed: {label}")
    if failed and proc is not None:
        print("      --- harness output ---")
        print("      " + (proc.stdout + proc.stderr).strip().replace("\n", "\n      "))


def refine_prompts(run):
    return [p.read_text(encoding="utf-8") for p in sorted(run.out.glob("rounds/*/refine_prompt.txt"))]


def main():
    base = Path(tempfile.mkdtemp(prefix="pelican-offline-"))
    cfg = make_config(base)
    try:
        # 1. Haiku improves round by round until parity (confirmed with A/B swapped).
        r = Run(base, "improve")
        p = harness(r, 20, cfg)
        s = r.summary()
        check("improving attempts reach confirmed parity", [
            ("exit 0", p.returncode == 0),
            ("stop_reason=parity", s["stop_reason"] == "parity" and s["parity"]),
            ("parity at round 3", s["rounds"] == 3 and s["final_round"] == 3),
            ("confirmation recorded", s["history"][-1]["confirmation"] is not None),
            ("money adds up", money_adds_up(r)),
        ], p)

        # 2. Rerunning a finished run pays nothing.
        before = r.call_count()
        p = harness(r, 20, cfg)
        check("rerun of a finished run makes 0 new calls", [
            ("0 new calls", r.call_count() == before),
            ("same stop_reason", r.summary()["stop_reason"] == "parity"),
            ("money adds up", money_adds_up(r)),
        ], p)

        # 3. Firewall: Haiku never sees the reference, its defects, or leaking defects.
        r = Run(base, "leak")
        p = harness(r, 20, cfg, FAKE_JUDGE="leak")
        prompts = refine_prompts(r)
        incidents = r.file("incidents.log").read_text(encoding="utf-8") if r.file("incidents.log").exists() else ""
        fb = json.loads(r.file("rounds/01/feedback.json").read_text(encoding="utf-8"))
        check("firewall drops leaks and never passes the reference", [
            ("refine prompts exist", len(prompts) >= 1),
            ("no reference SVG in refine prompts", not any("FABLE" in t for t in prompts)),
            ("no reference defect in refine prompts", not any("REFSECRET" in t for t in prompts)),
            ("no leaking defect in refine prompts",
             not any("unlike" in t or "88ccff" in t for t in prompts)),
            ("2 defects dropped in round 01", len(fb["dropped"]) == 2 and len(fb["kept"]) == 3),
            ("a defect whose fields join into 'image A' is kept",
             any("A balanced framing" in t for t in prompts)),
            ("a valid in-image comparison is kept",
             any("small compared with the tree" in t for t in prompts)),
            ("drops logged as incidents", incidents.count("leaks") >= 2),
            ("refine sessions have no tools and an empty dir",
             all("tools=''" in line and "cwd=[]" in line
                 for line in r.calls.read_text(encoding="utf-8").splitlines() if "haiku" in line)),
            ("judge sees only A.png and B.png",
             all("cwd=['A.png', 'B.png']" in line
                 for line in r.calls.read_text(encoding="utf-8").splitlines() if "opus" in line)),
        ], p)

        # 4. No progress: stagnation, and the final SVG is the best round, not the last.
        r = Run(base, "flat")
        p = harness(r, 20, cfg, FAKE_JUDGE="flat")
        s = r.summary()
        check("no progress stops on stagnation and keeps the best round", [
            ("stop_reason=stagnation", s["stop_reason"] == "stagnation"),
            ("final round is 00", s["final_round"] == 0),
            ("money adds up", money_adds_up(r)),
        ], p)

        # 5. Attempt better than the reference (confirmed).
        r = Run(base, "exceed")
        p = harness(r, 20, cfg, FAKE_JUDGE="exceed")
        s = r.summary()
        check("attempt above the reference stops the run", [
            ("stop_reason=attempt_exceeds_reference", s["stop_reason"] == "attempt_exceeds_reference"),
            ("round 0", s["rounds"] == 0),
        ], p)

        # 6. Position-biased judge: parity in one order only is not accepted.
        r = Run(base, "biased")
        for n in range(6):  # attempt always shown as B in the first judgment
            d = r.out / "rounds" / f"{n:02d}"
            d.mkdir(parents=True)
            (d / "blind.json").write_text(json.dumps({"A": "reference", "B": "attempt"}), encoding="utf-8")
        p = harness(r, 20, cfg, FAKE_JUDGE="biased")
        s = r.summary()
        unconfirmed = [h for h in s["history"] if h["parity"] and h["confirmation"]
                       and not h["confirmation"]["parity"]]
        check("parity seen in one A/B order only is rejected", [
            ("an unconfirmed parity exists", len(unconfirmed) == 1 and unconfirmed[0]["round"] == 2),
            ("run continued to a confirmed parity at round 3",
             s["stop_reason"] == "parity" and s["final_round"] == 3),
            ("money adds up", money_adds_up(r)),
        ], p)

        # 7. Tiny budget: refused before any spend, summary still written.
        r = Run(base, "tiny")
        p = harness(r, 0.5, cfg)
        check("tiny budget is refused before any spend", [
            ("exit 2", p.returncode == 2),
            ("stop_reason=budget", r.summary()["stop_reason"] == "budget"),
            ("0 calls", r.call_count() == 0),
            ("total 0", r.summary()["total_cost_usd"] == 0),
        ], p)

        # 8. Budget runs out mid-run: stops before a round it cannot finish.
        r = Run(base, "budget")
        p = harness(r, 1.2, cfg)
        s = r.summary()
        check("budget stop before a round that may not fit", [
            ("stop_reason=budget", s["stop_reason"] == "budget"),
            ("exit 0 (planned stop)", p.returncode == 0),
            ("never over budget", s["total_cost_usd"] <= 1.2),
            ("money adds up", money_adds_up(r)),
        ], p)

        # 9. Round cap.
        r = Run(base, "cap1")
        p = harness(r, 20, make_config(base, round_cap=1))
        check("round cap ends the run", [
            ("stop_reason=round_cap", r.summary()["stop_reason"] == "round_cap"),
            ("1 round", r.summary()["rounds"] == 1),
        ], p)

        # 10. Refine never returns an SVG: one retry, then no_svg, best attempt kept; rerun pays nothing.
        r = Run(base, "nosvg")
        p = harness(r, 20, cfg, FAKE_DRAW="nosvg")
        s = r.summary()
        before = r.call_count()
        p2 = harness(r, 20, cfg, FAKE_DRAW="nosvg")
        check("no usable SVG twice stops cleanly with no_svg", [
            ("stop_reason=no_svg", s["stop_reason"] == "no_svg"),
            ("final is round 00", s["final_round"] == 0),
            ("2 refine rows costed", sum(1 for x in r.rows() if x["role"] == "refine") == 2),
            ("rerun makes 0 new calls", r.call_count() == before),
            ("money adds up", money_adds_up(r)),
        ], p2)

        # 11. Refine misses the SVG once: retried, the run goes on.
        r = Run(base, "nosvgonce")
        p = harness(r, 20, cfg, FAKE_DRAW="nosvgonce")
        check("one missing SVG is retried and the run goes on", [
            ("stop_reason=parity", r.summary()["stop_reason"] == "parity"),
            ("invalid answer archived", any(r.out.glob("raw/01_attempt.invalid-*.json"))),
        ], p)

        # 12. Malformed judge JSON once: retried, both sessions costed.
        r = Run(base, "badonce")
        p = harness(r, 20, cfg, FAKE_JUDGE="badonce")
        check("malformed judge answer is retried once and costed", [
            ("stop_reason=parity", r.summary()["stop_reason"] == "parity"),
            ("invalid verdict archived", any(r.out.glob("raw/00_judge.invalid-*.json"))),
            ("money adds up", money_adds_up(r)),
        ], p)

        # 13. Malformed judge JSON twice: run stops with an error, both costed.
        r = Run(base, "bad")
        p = harness(r, 20, cfg, FAKE_JUDGE="bad")
        check("malformed judge answer twice stops the run", [
            ("exit 1", p.returncode == 1),
            ("stop_reason=error", r.summary()["stop_reason"] == "error"),
            ("2 judge rows costed", sum(1 for x in r.rows() if x["role"] == "judge") == 2),
        ], p)

        # 14. Session timeout: killed, its cap reserved, resume finishes the run.
        r = Run(base, "timeout")
        tcfg = make_config(base, session_timeout_s=3)
        p = harness(r, 20, tcfg, FAKE_SLEEP="opus:30")
        s = r.summary()
        killed = not Path(f"{r.calls}.slept-to-the-end").exists()
        p2 = harness(r, 20, tcfg)
        s2 = r.summary()
        check("timed-out session is killed, its cap reserved, then resumed", [
            ("stop_reason=error", s["stop_reason"] == "error"),
            ("session process killed", killed),
            ("judge cap reserved", abs(s["reserved_unmeasured_usd"] - 0.30) < 1e-9),
            ("resume finishes", s2["stop_reason"] == "parity"),
            ("reserve kept after resume", abs(s2["reserved_unmeasured_usd"] - 0.30) < 1e-9),
            ("money adds up", money_adds_up(r)),
        ], p2)

        # 15. Step cap hit: clear stop reason, cost recorded.
        r = Run(base, "stepcap")
        p = harness(r, 20, cfg, FAKE_CAP="haiku")
        check("a session stopped at its step cap is reported as step_cap", [
            ("exit 1", p.returncode == 1),
            ("stop_reason=step_cap", r.summary()["stop_reason"] == "step_cap"),
            ("its cost is recorded", any(x["role"] == "refine" for x in r.rows())),
            ("money adds up", money_adds_up(r)),
        ], p)

        # 16. Rendering is local: a missing renderer stops before any spend.
        r = Run(base, "norender")
        bad = make_config(base, render={"width": 800, "cmd": ["no-such-renderer", "{svg}", "{png}"],
                                        "version_cmd": ["no-such-renderer", "--version"]})
        p = harness(r, 20, bad)
        check("missing renderer is detected before any spend", [
            ("exit 1", p.returncode == 1),
            ("0 calls", r.call_count() == 0),
        ], p)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    passed = sum(ok for _, ok in RESULTS)
    print(f"\n{passed}/{len(RESULTS)} scenarios passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

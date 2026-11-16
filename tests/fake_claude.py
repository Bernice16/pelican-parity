"""Fake `claude -p` for offline tests: same CLI and JSON output, no model call, $0.

Drawings are tiny SVGs carrying a marker the fake judge can read back from the
"PNG" (the fake renderer just copies the SVG):
  - the reference holds FABLE
  - Haiku's drawings hold "level N"; each refine answers level N+1
The judge scores the attempt from its level, so a run "improves" round by round.

Behaviour is set with environment variables (see tests/run_offline.py):
  FAKE_CALLS   file where every call is logged (model, tools, working dir)
  FAKE_JUDGE   improve (default) | flat | leak | exceed | biased | bad | badonce
  FAKE_DRAW    nosvg (refine never returns an SVG) | nosvgonce (first refine only)
  FAKE_SLEEP   "<model substring>:<seconds>" sleep before answering (timeout test)
  FAKE_CAP     "<model substring>" answer as if the step cap was hit
"""
import json
import os
import re
import sys
import time
import uuid

args = sys.argv
prompt = sys.stdin.read()
model = args[args.index("--model") + 1]
tools = args[args.index("--tools") + 1]
calls = os.environ["FAKE_CALLS"]
refining = "<current_svg>" in prompt

with open(calls, "a", encoding="utf-8") as f:
    f.write(f"{model} tools={tools!r} cwd={sorted(os.listdir('.'))}\n")


def once(name):
    """True the first time `name` is asked for in this test, False afterwards."""
    flag = f"{calls}.{name}"
    if os.path.exists(flag):
        return False
    open(flag, "w").close()
    return True


sleep = os.environ.get("FAKE_SLEEP", "")
if sleep and sleep.split(":")[0] in model:
    time.sleep(float(sleep.split(":")[1]))
    open(f"{calls}.slept-to-the-end", "w").close()  # must never appear: the harness kills us

CRITERIA = ("fidelity", "coherence", "readability", "composition", "craft")


def scores(v):
    return {c: v for c in CRITERIA}


def level_of(text):
    m = re.search(r"level (\d+)", text)
    return int(m.group(1)) if m else 0


def judge_answer():
    mode = os.environ.get("FAKE_JUDGE", "improve")
    if mode == "badonce":
        mode = "bad" if once("bad") else "improve"
    if mode == "bad":
        return "not json at all"
    imgs = {lab: open(f"{lab}.png", encoding="utf-8").read() for lab in ("A", "B")}
    verdict = {"images": {}, "not_applicable": []}
    for lab, txt in imgs.items():
        if "FABLE" in txt:
            verdict["images"][lab] = {
                "summary": "reference", "scores": scores(3 if mode == "exceed" else 5),
                # Text that must never reach Haiku: it describes the reference.
                "defects": [{"criterion": "craft", "severity": "minor", "what": "REFSECRET detail",
                             "where": "somewhere", "expected": "something"}]}
            continue
        lvl = level_of(txt)
        s = {"flat": 3, "exceed": 5}.get(mode, min(5, 2 + lvl))
        if mode == "biased" and lab == "B":
            s = min(5, s + 1)  # a judge that favours whatever is shown second
        defects = []
        if s < 5:
            defects.append({"criterion": "coherence", "severity": "major", "what": "the body floats",
                            "where": "middle", "expected": "the body rests on the seat"})
        if mode == "leak":
            # Valid: compares parts of the same drawing (dropped by mistake in a real run).
            defects.append({"criterion": "composition", "severity": "major",
                            "what": "the monkey is small compared with the tree",
                            "where": "centre", "expected": "the monkey is the clear focal point"})
            defects.append({"criterion": "craft", "severity": "minor", "what": "unlike image A, no spokes",
                            "where": "wheels", "expected": "spokes"})
            defects.append({"criterion": "craft", "severity": "minor", "what": "sky colour",
                            "where": "sky", "expected": "use #88ccff"})
        verdict["images"][lab] = {"summary": "attempt", "scores": scores(s), "defects": defects}
    a, b = (verdict["images"][lab]["scores"] for lab in ("A", "B"))
    verdict["parity"] = (all(abs(a[c] - b[c]) <= 1 for c in CRITERIA)
                         and abs(sum(a.values()) - sum(b.values())) <= 1)
    verdict["parity_reason"] = "fake"
    return "```json\n" + json.dumps(verdict) + "\n```"


def draw_answer():
    if "fable" in model:
        return "```svg\n<svg xmlns='http://www.w3.org/2000/svg'><desc>FABLE</desc><circle r='5'/></svg>\n```"
    lvl = level_of(prompt) + 1 if refining else 0
    draw = os.environ.get("FAKE_DRAW", "")
    if refining and (draw == "nosvg" or (draw == "nosvgonce" and once("nosvg"))):
        return "Sorry, I cannot draw that."
    return f"```svg\n<svg xmlns='http://www.w3.org/2000/svg'><desc>level {lvl}</desc><circle r='5'/></svg>\n```"


result = judge_answer() if "opus" in model else draw_answer()
cost = 0.1
out = {"type": "result", "subtype": "success", "is_error": False, "session_id": str(uuid.uuid4()),
       "result": result, "total_cost_usd": cost,
       "modelUsage": {model: {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": 3,
                              "cacheCreationInputTokens": 4, "costUSD": cost}}}
cap = os.environ.get("FAKE_CAP", "")
if cap and cap in model and refining:
    out.update(subtype="error_max_budget_usd", is_error=True, result="",
               errors=["Reached maximum budget ($0.05)"])
print(json.dumps(out))
sys.exit(1 if out["is_error"] else 0)

"""Model Runner: one headless `claude -p` session per step.

Each session runs in its own empty working directory, with an explicit tool
allow-list (no tools at all for drawing roles) and without the user's MCP
servers or settings, so runs do not depend on the machine they run on.
Usage is read from the JSON that Claude Code returns: measured, never estimated.
"""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


class SessionError(RuntimeError):
    """A session failed. The message says whether its cost reached the ledger."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class SessionLost(SessionError):
    """The session was killed (timeout or Ctrl+C) before it returned its usage.

    Its real cost is unknown: the caller must reserve the step's worst case.
    """

    def __init__(self, message: str, detail: str = "", interrupted: bool = False):
        super().__init__(message, detail)
        self.interrupted = interrupted


class StepCapHit(SessionError):
    """Claude Code stopped the session at its --max-budget-usd. Cost is recorded."""


def build_cmd(cfg: dict, role: str, system_append: str, max_budget_usd: float) -> list:
    """The prompt itself is NOT in the command line: it goes through stdin (see run_session)."""
    role_cfg = cfg["roles"][role]
    claude_bin = os.environ.get("CLAUDE_BIN") or cfg["claude_bin"]
    # A list lets the offline tests run `python tests/fake_claude.py` on any OS.
    prefix = list(claude_bin) if isinstance(claude_bin, list) else [claude_bin]
    return [
        *prefix, "-p",
        "--model", role_cfg["model"],
        "--effort", role_cfg["effort"],
        "--output-format", "json",
        "--append-system-prompt", system_append,
        # Allow-list, not deny-list: "" disables every built-in tool.
        "--tools", role_cfg.get("tools", ""),
        # Ignore the user's MCP servers and settings (see config.json).
        *cfg.get("isolation_flags", []),
        "--max-budget-usd", f"{max_budget_usd:.4f}",
    ]


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the session and everything it started (claude may run under a shell shim)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_session(cfg: dict, role: str, prompt: str, system_append: str,
                workdir: Path, raw_path: Path, max_budget_usd: float) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = build_cmd(cfg, role, system_append, max_budget_usd)
    # Own process group: on Ctrl+C the harness (not the console) decides how the
    # session ends, so it can log the lost cost before exiting.
    extra = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
             else {"start_new_session": True})
    proc = subprocess.Popen(
        cmd, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", **extra,
    )
    try:
        # Prompt via stdin: long multi-line prompts (the judge's) are not subject to
        # the OS's command-line quoting rules or length limit.
        stdout, stderr = proc.communicate(input=prompt, timeout=cfg["session_timeout_s"])
    except subprocess.TimeoutExpired as e:
        _kill_tree(proc)
        raise SessionLost(
            f"{role}: session killed after {e.timeout}s timeout, real cost unknown "
            f"(its step cap is reserved against the budget)",
        ) from e
    except KeyboardInterrupt as e:
        _kill_tree(proc)
        raise SessionLost(
            f"{role}: session interrupted (Ctrl+C), real cost unknown "
            f"(its step cap is reserved against the budget)", interrupted=True,
        ) from e

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        # No usage to read: treat like a lost session (never under-count).
        raise SessionLost(
            f"{role}: output is not JSON (exit {proc.returncode}), real cost unknown "
            f"(its step cap is reserved against the budget)",
            detail=(stderr or stdout)[-2000:],
        ) from e

    # Keep the raw JSON: every number can be traced back to it, and it is the
    # resume point (a successful session is never paid for twice).
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def usage_row(data: dict, role: str, model: str, effort: str, round_no: int):
    """Map Claude Code's JSON to one usage.csv row.

    Never raises: a paid session must always reach the ledger. Anything odd is
    returned as a warning for the incident log instead.
    Returns (row, warnings).
    """
    warnings = []
    model_usage = data.get("modelUsage") or {}
    total = data.get("total_cost_usd")

    if model_usage:
        vals = model_usage.values()
        row = {
            "model": "+".join(sorted(model_usage)),
            "input_tokens": sum(m.get("inputTokens", 0) for m in vals),
            "output_tokens": sum(m.get("outputTokens", 0) for m in vals),
            "cache_read_tokens": sum(m.get("cacheReadInputTokens", 0) for m in vals),
            "cache_write_tokens": sum(m.get("cacheCreationInputTokens", 0) for m in vals),
            "cost_usd": round(sum(m.get("costUSD", 0.0) for m in vals), 7),
        }
        if total is not None and abs(total - row["cost_usd"]) > 1e-6:
            # Keep the higher of the two: never under-count against the budget.
            warnings.append(f"per-model cost {row['cost_usd']} != total_cost_usd {total}; "
                            f"ledger keeps {max(total, row['cost_usd'])}")
            row["cost_usd"] = round(max(total, row["cost_usd"]), 7)
    else:
        usage = data.get("usage") or {}
        warnings.append("no modelUsage in session output; using top-level usage and total_cost_usd")
        row = {
            "model": model,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            "cost_usd": round(total or 0.0, 7),
        }
        if total is None:
            warnings.append("no total_cost_usd either: cost recorded as 0, real cost unknown")

    session_id = data.get("session_id")
    if not session_id:
        warnings.append("no session_id in session output")
        session_id = f"unknown-{role}-{round_no:02d}"
    row.update({"session_id": session_id, "role": role, "effort": effort, "round": round_no})
    return row, warnings


def check_session_ok(data: dict, role: str, expected_model: str) -> None:
    if data.get("subtype") == "error_max_budget_usd":
        raise StepCapHit(f"{role}: session stopped at its step cap ({'; '.join(data.get('errors') or [])}), "
                         f"cost recorded. Raise max_step_usd.{role} in config.json if this prompt needs more")
    if data.get("is_error") or data.get("subtype") != "success":
        errs = "; ".join(data.get("errors") or []) or str(data.get("result", ""))[:200]
        raise SessionError(f"{role}: session failed ({data.get('subtype')}: {errs}), cost recorded")
    models = set((data.get("modelUsage") or {}).keys())
    if models and expected_model not in models:
        raise SessionError(
            f"{role}: expected model {expected_model}, session used {sorted(models)}"
        )

"""Usage Ledger: single source of truth for costs.

One row per session, appended as soon as the session finishes, to the run's
usage.csv and to the project ledger (ledger/usage.csv). Rows are never
duplicated: a session_id already present is not written again (safe on resume).
"""
import csv
from pathlib import Path

COLUMNS = [
    "session_id", "role", "model", "effort", "round",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "cost_usd",
]


def _existing_ids(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {row["session_id"] for row in csv.DictReader(f)}


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if row["session_id"] in _existing_ids(path):
        return
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow({k: row[k] for k in COLUMNS})


def read_rows(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def total_cost(path: Path) -> float:
    return sum(float(r["cost_usd"]) for r in read_rows(path))

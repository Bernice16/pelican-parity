# pelican-parity

A budgeted, headless judge loop that brings a cheap model (Haiku 5.5) to the
quality of the best model (Fable 5.1) on any drawing prompt.
Submission for the Néo Carbone "Pelican on a Bicycle" engineering challenge.

```
./run --prompt prompts/pelican.txt --budget-usd 20 --out out-pelican
```

- **Rerun it:** [RUNBOOK.md](RUNBOOK.md) covers install, run, resume, tests and troubleshooting.
- **What happened and why:** [REPORT.md](REPORT.md) has the results, the headline comparison, the decisions, where the judge was wrong, and what comes next.
- **Release runs:** [`out-final-pelican/`](out-final-pelican) (pelican) and [`out-final-monkey/`](out-final-monkey) (own prompt).
- **Every paid session of the project:** [`ledger/usage.csv`](ledger/usage.csv).

## How it works

```
prompt ─┬─> 1 Reference  Fable 5.1 high, one shot ────────── reference.png ─┐
        └─> 2 Baseline   Haiku 5.5 low,  attempt 0 ──────── attempt.png ───┤
                                                                           v
                     3 Judge  Opus 5.5 high, blind A/B (random order) -> verdict.json
                                     │ parity (or better)?  yes -> 2nd judgment, A/B swapped
                                     │                              agrees -> STOP
                                     │ no
                                     v
                     FIREWALL: keeps only the attempt's own defects, drops any
                     defect that names the other image, a number, a colour value
                     or code
                                     v
                     4 Refine  Haiku 5.5 low: prompt + its own best SVG + defects
                                     └──> new attempt ──> 3 Judge ...
```

- Every role is a separate `claude -p --output-format json` session. Its prompt goes in on stdin. It runs with no MCP servers and no settings, in an empty working directory. Only the judge gets a tool (`Read`, for the two PNGs).
- Before each session, the runtime checks it can afford that step's worst case. It refuses the step if not, and passes `--max-budget-usd` as a second guard.
- One line per session shows tokens, step cost, run total and budget left.
- A rerun of the same command resumes the run and never pays twice for finished work.
- There is no prompt-specific logic. The judge prompt, refine prompt and stop rules are generic, and the prompt text is passed as data.

## Repository

| path | what |
|---|---|
| `run` | entry point (Bash), calls `harness/main.py` |
| `harness/main.py` | orchestration: budget, sessions, rounds, stop rules, resume, `summary.json` |
| `harness/runner.py` | one `claude -p` session: command, timeout, kill, usage row |
| `harness/judge.py` | judge prompt, verdict parsing and validation, blind A/B, parity rule check |
| `harness/firewall.py` | judge verdict → refiner feedback, leak checks |
| `harness/svgtools.py` | SVG extraction and validation, PNG render (resvg, pinned fonts) |
| `harness/ledger.py` | `usage.csv` writer (run and project ledgers) |
| `harness/calibrate.py`, `review.py`, `headline.py` | evaluation tools (judge calibration, review page, headline table) |
| `prompts/` | `pelican.txt`, `monkey.txt`, `judge_v1.txt`, `refine_v1.txt` |
| `config.json` | models, efforts, step caps, timeout, round cap, renderer command |
| `fonts/` | DejaVu fonts used for every render (reproducible PNGs) |
| `tests/` | offline suite: fake `claude` and fake renderer, 16 scenarios, $0 |
| `eval/` | calibration results, headline table, review of the verdicts |
| `ledger/usage.csv` | every session of the project (usage fields only) |

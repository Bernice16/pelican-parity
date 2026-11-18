# pelican-parity

A budgeted, headless judge loop that brings a cheap model (Haiku 5.5) to the
quality of the best model (Fable 5.1) on any drawing prompt.
Submission for the Néo Carbone "Pelican on a Bicycle" engineering challenge.

```
./run --prompt prompts/pelican.txt --budget-usd 20 --out out-pelican
```

- **Rerun it:** [RUNBOOK.md](RUNBOOK.md) covers install, run, resume, tests and troubleshooting.
- **What happened and why:** [REPORT.md](REPORT.md) has the results, the headline comparison, the decisions, where the judge was wrong, and what comes next.
- **Release runs** (final judge, `judge_v3.txt`): [`out-final-v3-pelican/`](out-final-v3-pelican) (the pelican), and three prompts of my own: [`out-final-v3-beaver/`](out-final-v3-beaver), [`out-final-v3-santa/`](out-final-v3-santa), [`out-final-v3-monkey/`](out-final-v3-monkey).
- **Evidence runs:** [`out-v3-santa/`](out-v3-santa) (total gap 2 let a weaker drawing pass), [`out-v4-santa/`](out-v4-santa) (judge_v4), [`out-v2-monkey/`](out-v2-monkey) (the Ralph loop before the fix). The v1 runs [`out-final-pelican/`](out-final-pelican) and [`out-final-monkey/`](out-final-monkey) are kept for history.
- **Every paid session of the project:** [`ledger/usage.csv`](ledger/usage.csv).

## How it works

```
prompt ─┬─> 1 Reference  Fable 5.1 high, one shot ────────── reference.png ─┐
        └─> 2 Baseline   Haiku 5.5 low,  attempt 0 ──────── attempt.png ───┤
                                                                           v
                     3 Judge  Opus 5.5 high, blind A/B (random order) -> verdict.json
                     (no drawing prompt: it works out the scene from the two images)
                                     │ parity (or better)?  yes -> 2nd judgment, A/B swapped
                                     │                              agrees -> STOP (success)
                                     │ no
                                     v
                     FIREWALL: keeps only the attempt's own defects, drops any
                     defect that names the other image, a number, a colour value
                     or code
                                     v
                     4 Refine  Haiku 5.5 low: its own SVG + its defects (no drawing prompt,
                               and never a verdict it has already used)
                                     └──> new attempt ──> 3 Judge ...
```

- Every role is a separate `claude -p --output-format json` session. Its prompt goes in on stdin. It runs with no MCP servers and no settings, in an empty working directory. Only the judge gets a tool (`Read`, for the two PNGs).
- Before each session, the runtime checks it can afford that step's worst case. It refuses the step if not, and passes `--max-budget-usd` as a second guard.
- One line per session shows tokens, step cost, run total and budget left.
- A rerun of the same command resumes the run and never pays twice for finished work.
- Only the reference and the baseline get the drawing prompt. The judge and the refiner never see it.
- There is no prompt-specific logic. The judge prompt, refine prompt and stop rules are generic, and the prompt text is passed as data. The judge's criteria are set in `config.json` and checked against the judge prompt before any spend.

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
| `prompts/` | drawing prompts (`pelican.txt`, `beaver.txt`, `santa.txt`, `monkey.txt`, `hippo.txt`), every judge version (`judge_v1.txt` to `judge_v4.txt`, v3 is the final one), `refine_v1.txt`, `refine_v2.txt` (final) |
| `config.json` | models, efforts, step caps, timeout, judge and refine prompts, criteria, parity gap, round cap, stagnation, renderer command |
| `fonts/` | DejaVu fonts used for every render (reproducible PNGs) |
| `tests/` | offline suite: fake `claude` and fake renderer, 23 scenarios, $0 |
| `eval/` | `verdict_final_review.md`: review of every judgment of the final runs (judge_v3). The other files (calibration, headline, `verdict_review.md`) date from the judge_v1 stage |
| `ledger/usage.csv` | every session of the project (usage fields only) |

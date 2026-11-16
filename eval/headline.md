# Headline: reference alone vs baseline + judge/refine loop

Totals are the judge's sum of 5 criteria (max 25) in the final round's judgment. Loop = Haiku drawings (baseline + refines) + all Opus judgments.

| run | stop | rounds | Haiku total start → final | reference total | Fable alone $ | Haiku $ | judge $ | loop $ | loop / Fable |
|---|---|---|---|---|---|---|---|---|---|
| out-final-pelican | parity | 1 | 18 → 22 | 23 | 0.2027 | 0.0039 | 0.1753 | 0.1792 | 0.88× |
| out-final-monkey | attempt_exceeds_reference | 3 | 19 → 22 | 19 | 0.2190 | 0.0106 | 0.2999 | 0.3105 | 1.42× |
| out-m3-second | parity | 4 | 18 → 21 | 22 | 0.1382 | 0.0119 | 0.4233 | 0.4352 | 3.15× |
| out-m3-crash | attempt_exceeds_reference | 1 | 21 → 23 | 22 | 0.1017 | 0.0041 | 0.2670 | 0.2711 | 2.67× |

Average over 4 runs: Fable alone $0.1654, Haiku drawings $0.0076, judge $0.2914, loop $0.2990.

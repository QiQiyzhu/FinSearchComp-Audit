# Match-3 Agent QA reproducibility report

This report is generated offline from `playable-seed-7`. The Agent chooses only
allow-listed skills; the deterministic rules engine owns legality, cascade, score,
and replay verification.

| Metric | Result |
|---|---:|
| Existing matched cells | 0 |
| Legal moves | 5 |
| Difficulty proxy (triage only) | 28.5 |
| Regression cascades | 3 |
| Regression cleared cells | 12 |
| Regression score | 2400 |

| Reproducibility check | Status |
|---|---|
| scenario_expectations | PASS |
| regression_move | PASS |
| deterministic_replay | PASS |
| no_simulation_failures | PASS |

The difficulty proxy is intentionally not presented as player difficulty. It is a
transparent mobility/balance heuristic for QA triage and must be calibrated with
telemetry or play-test data before product use.

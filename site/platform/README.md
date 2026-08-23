# FinAgent Audit Platform reproducibility report

| Metric | Result |
|---|---:|
| Evaluation cases | 3 |
| Answered / Abstained | 2 / 1 |
| Recovered timeout attempts | 2 |
| Classified failures | 1 |

| Check | Status |
|---|---|
| evaluation_completed | PASS |
| idempotency_deduplicated | PASS |
| timeout_recovered | PASS |
| failure_classified | PASS |
| replay_matched | PASS |

The demo is deterministic and uses an injected local answer service. A separate
integration test runs the real offline ATLAS-RAG pipeline through the same SQLite
job, trace, and replay layer.

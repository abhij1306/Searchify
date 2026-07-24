# LLM Analytics domain package (docs/roadmap/llm-analytics.md).
#
# Deterministic, PURE classification + sanitization primitives (no DB, no
# network, no LLM — invariants 6 + 9) plus the queued executors that run
# them: ``ingest`` (referral payload ingest), ``tasks`` (shared task-batch
# guards + the classify executor), ``snapshot`` (the analytics snapshot
# projection), and ``enqueue`` / ``service`` (task enqueue + read surface).

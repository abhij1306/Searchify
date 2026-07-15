# Audit worker entrypoint (placeholder).
#
# The real Postgres-queue worker lands in B5: it claims AuditTask rows via
# FOR UPDATE SKIP LOCKED, runs the answer-engine adapter, persists artifacts,
# heartbeats its lease, and drives audit state transitions (invariant 8).
# This placeholder keeps the `worker` compose service wired without a
# crash-loop until B5 replaces `main()` with the real claim/lease loop.
from __future__ import annotations

import logging
import time

from app.core.telemetry import configure_logging

logger = logging.getLogger("app.workers.audit_worker")


def main() -> None:
    configure_logging()
    logger.info("audit worker placeholder started; awaiting B5 implementation")
    while True:  # pragma: no cover - long-running process loop
        time.sleep(60)


if __name__ == "__main__":  # pragma: no cover
    main()

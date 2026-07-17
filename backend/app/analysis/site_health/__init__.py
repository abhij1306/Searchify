# Site Health deep analysis package (Task 5).
#
# Pure, deterministic building blocks the ``site_health_worker`` composes to
# turn one fetched page into durable analysis evidence:
#
#   - ``parser`` — bounded HTML/delivery fact extraction (lxml, hardened).
#   - ``structured_data`` — JSON-LD / microdata parse + required-property
#     validation against the config-owned schema map.
#   - ``rules`` — evaluate the config-owned rule catalog into pass / fail /
#     not_applicable / error outcomes with exact evidence + provenance.
#   - ``scoring`` — the deterministic dimension/overall scoring formula plus a
#     crawl-level aggregation that ignores missing/error URLs.
#
# Everything here is a PURE function (no I/O, no ORM): the worker owns all
# persistence, transactions, and lifecycle. Determinism is preserved so the
# same bytes always yield the same facts/evaluations/scores (invariant 9).
from __future__ import annotations

# Traffic domain package (docs/roadmap/traffic.md).
#
# A pure PROJECTION over the integrations-owned ``IntegrationMetricRow``
# fact rows (invariants 2 + 7): ``projection.py`` is PURE math (no DB, no
# network, no clock) and ``service.py`` is the DB-only
# ``traffic_snapshot_refresh`` executor — NO provider I/O anywhere in this
# package.

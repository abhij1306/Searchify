# Roadmap — Traffic

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping via `require_workspace_member`,
> the Postgres `FOR UPDATE SKIP LOCKED` task queue, immutable artifacts, provenance +
> version on every derived row, and config-in-config-only. Read [`../../Agents.md`](../../Agents.md)
> and [`../invariants.md`](../invariants.md) first — every rule there applies here too.

## 1. Goal & positioning

**Organic + AI-driven traffic analytics for the brand's own site**, sourced from **Google
Search Console** (query/page impressions, clicks, CTR, average position) and **Google
Analytics 4** (sessions, engagement, conversions). It answers "how much organic and
AI-referred traffic does the site actually get, on which pages and queries, and how does that
track the visibility we measure in audits?".

Traffic is the **measured, first-party counterpart** to the audit-derived visibility metrics:
audits measure how AI engines *answer*; Traffic measures how the site is *found and visited*.
The two join on the site's own pages (see §3 join) and feed the correlation view in
[`llm-analytics.md`](llm-analytics.md).

The GSC/GA4 connectors themselves are owned by the **GSC / GA4 / Bing integrations** roadmap
spec; Traffic **consumes** their sync output. This spec covers the traffic **data model,
sync task, projections, and screen** — not the OAuth/connector plumbing.

## 2. Relationship to existing subsystems (grep before adding — invariant 2)

Reuse, do not duplicate:

- **`Project`** (`backend/app/models/project.py`) + **`OwnedDomain`** (`models/brand.py`) —
  identify which site/property the traffic belongs to and validate that a synced property is
  actually an owned domain before ingest.
- **`PageArtifact`** — the immutable per-page row defined in the Technical Audit spec
  ([`technical-audit.md`](technical-audit.md) §3). Traffic **joins query/page metrics to the
  site's crawled pages** by canonical URL; when the Content surface exists it reuses the same
  page identity. Do not invent a second page table — cross-reference `PageArtifact`.
- **`PostgresTaskQueue`** (`app/orchestration/postgres_task_queue.py`) + the `TaskQueue`
  Protocol — sync runs as a queued task (invariant 8).
- **BYOK / Fernet** (`encrypt_secret`/`decrypt_secret`, `ProviderConnection` pattern) — GSC/GA4
  OAuth tokens are stored encrypted at rest and resolved at sync time only, never returned in a
  DTO or logged (invariant 6).
- **`ReferralEvent`** ([`llm-analytics.md`](llm-analytics.md) §3) — GA4/server-log ingest that
  yields AI-referral rows is landed by the same sync path; Traffic and LLM Analytics share the
  ingest batch (`TrafficImport`).

## 3. Data model (new tables — UUID PKs, workspace-scoped)

An immutable **ingest artifact per sync window**, deterministic **derived metric rows** with
provenance, and a **projection snapshot** — the same shape as the audit engine.

- **`TrafficImport`** — immutable ingest artifact for one sync window (invariant 3), written
  **once** by the worker that claimed the sync task, never mutated. `id`, `workspace_id`,
  `project_id`, `source` (`gsc|ga4`), `property_id` (the GSC site / GA4 property, validated
  against `OwnedDomain`), `window_start`, `window_end`, `dimensions` (JSONB: which dims were
  requested — page, query, country, device), `row_count`, `status`, `content_hash`,
  `raw_object_key` (nullable — large raw payloads go to object storage roadmap; Postgres holds
  metadata), `requested_at`, `completed_at`. Unique `(project_id, source, property_id,
  window_start, window_end)` so the same window is never double-imported. A re-sync of the same
  window creates a **new** `TrafficImport` identity (never an overwrite).
- **`TrafficMetric`** — derived row (invariant 4), one per `(import, dimension-key, date)`.
  `id`, `workspace_id`, `project_id`, `import_id` (FK — the immutable source, provenance),
  `source` (`gsc|ga4`), `metric_date`, `page_url` (canonicalized), `page_artifact_id`
  (nullable FK to `PageArtifact` when the URL joins a crawled page), `query` (nullable; GSC
  query dimension), `country`, `device`, and the measures:
  `impressions`, `clicks`, `ctr`, `position` (GSC); `sessions`, `engaged_sessions`,
  `conversions` (GA4). `source_version` (the connector/schema version), `analyzer_version`
  (the normalization version). Every row traces to its `import_id` + version (invariant 4).
- **`TrafficSnapshot`** — projection (invariant 7), computed from persisted `TrafficMetric`
  rows for a `(project, window, granularity)`. `id`, `workspace_id`, `project_id`,
  `window_start`, `window_end`, `granularity` (`day|week|month`), `metrics` (JSONB: totals,
  top pages/queries, CTR/position distributions, trend series), `source_import_ids` (JSONB),
  `formula_version`, `analyzer_version`, `created_at`. Rebuildable from the persisted metric
  rows; holds nothing not traceable to them.

All carry `workspace_id`, string-UUID PKs, and are accessed only via `require_workspace_member`
(invariant 5). No integer PKs, no `user_id`.

## 4. Sync task (Postgres SKIP LOCKED queue — invariant 8)

Sync runs as a scheduled **or** on-demand import task on the existing Postgres queue — no
Redis (invariant 8), reusing `PostgresTaskQueue` and the `TaskQueue` Protocol so a future
Redis impl needs no rewrite:

1. A `traffic_sync` task is enqueued (on demand from the UI, or by a scheduler for recurring
   syncs — recurring schedules are themselves a Release 1.1 roadmap item).
2. Worker claims it with `FOR UPDATE SKIP LOCKED`, sets `lease_owner` + `lease_expires_at`,
   and **commits the claim before any network I/O** (invariant 8) — never holds a DB
   transaction open across the GSC/GA4 API call.
3. Worker resolves the OAuth token from the encrypted connection (BYOK, invariant 6), fetches
   the window, and writes exactly **one** `TrafficImport` (immutable) plus its `TrafficMetric`
   rows (append-only, provenance-stamped). It **heartbeats** to extend the lease during the
   fetch.
4. A **sweeper** returns an expired lease to `retry_wait`, or `failed` after `max_attempts`.
   `SKIP LOCKED` + the unique window constraint prevent double-import; a succeeded sync is
   never re-run — a new window/re-sync is a new task identity.
5. On completion the worker enqueues/refreshes the `TrafficSnapshot` projection and, for GA4
   rows carrying referral signals, the `classify_referrals` task
   ([`llm-analytics.md`](llm-analytics.md) §5).

Cancellation is cooperative (invariant 9): the worker stops at the window/batch boundary.

## 5. Page join (to `PageArtifact` / Content)

`TrafficMetric.page_url` is canonicalized with the **same URL normalization** the analysis
pipeline already uses (`analysis/normalization.py` — reuse it, invariant 2) so GSC/GA4 page
rows and crawled `PageArtifact` rows join cleanly. When a Technical Audit crawl exists for the
project, `page_artifact_id` is resolved by canonical URL match; unmatched pages keep
`page_artifact_id=null` (still valid — they're measured pages the crawler hasn't seen). This
join is what lets the future Content and Opportunities surfaces rank pages by
traffic-vs-visibility gap.

## 6. API surface (roadmap; `/api/v1`, projections only — invariant 7)

- `GET /projects/{id}/traffic?from=&to=&granularity=` — headline projection over
  `TrafficSnapshot`: totals + trend series for impressions/clicks/CTR/position (GSC) and
  sessions/conversions (GA4).
- `GET /projects/{id}/traffic/pages?from=&to=&sort=` — paged page-level rows (projection over
  `TrafficMetric`, optionally joined to `PageArtifact`).
- `GET /projects/{id}/traffic/queries?from=&to=&sort=` — paged GSC query-level rows.
- `POST /projects/{id}/traffic/sync` — enqueue an on-demand `traffic_sync` task (returns the
  queued import id; the actual fetch is async on the worker). 409 if a sync for the same window
  is already in flight.

All workspace-scoped via `require_workspace_member` (invariant 5); cross-workspace access
returns 403/404. Read endpoints never call GSC/GA4 — they render persisted `TrafficMetric` /
`TrafficSnapshot` rows (invariant 7). Only the sync task talks to the provider.

## 7. Frontend (roadmap)

- **Route:** `/traffic` — already stubbed as a **disabled "soon"** nav item ("Traffic",
  `TrendingUp`) in the **Analytics** group of `frontend/components/layout/nav-items.ts`. Flip
  `live: true` when shipping.
- Reuse the MVP contract layer: add `frontend/lib/api/traffic.ts` (API module + zod schemas),
  a `queryKeys.traffic.*` entry, and the existing `trend-chart` primitive
  (`components/ui/trend-chart.tsx`) for the time series, plus the shared table/card primitives
  for the page/query tables.
- Every response passes `strictValidate`; ids are `z.string().uuid()`; no `user_id`.
- Same-origin `/api/*` proxying only (invariant 12); TanStack Query with the shared retry
  policy; polling for in-flight sync status (reuse the `/runs` polling pattern).

## 8. Config & tuning knobs (all in `backend/app/core/config/`)

Nothing tunable is hard-coded (invariant 1). Add a `config/traffic.py` module:

- `TRAFFIC_SYNC_DEFAULT_WINDOW_DAYS`, `TRAFFIC_MAX_WINDOW_DAYS`, `TRAFFIC_SYNC_DIMENSIONS`
  (default GSC/GA4 dimensions to request).
- `TRAFFIC_ROW_LIMIT_PER_WINDOW`, `TRAFFIC_REQUEST_TIMEOUT_S`, `TRAFFIC_MAX_ATTEMPTS`
  (queue retry budget), `TRAFFIC_SNAPSHOT_GRANULARITIES`.
- `TRAFFIC_NORMALIZATION_VERSION` — the `analyzer_version` stamped on `TrafficMetric`
  (invariant 4).
- GSC/GA4 API base urls + scopes belong in the integrations connector config, not here — do
  not duplicate them (invariant 2).

## 9. Suggested build order

1. Config: `config/traffic.py` knobs + version constant; migration for the 3 tables.
2. Normalization: reuse `analysis/normalization.py` URL canonicalization for the page join;
   add a deterministic GSC/GA4 row → `TrafficMetric` mapper (table-tested against fixtures).
3. `traffic_sync` queued task + worker path (reuse `PostgresTaskQueue`, commit-before-I/O,
   BYOK token resolution) — gated behind the GSC/GA4 connector integration.
4. `TrafficSnapshot` projection builder.
5. API routers (projections + the enqueue endpoint) + zod contracts.
6. Frontend `/traffic` screen (wire `trend-chart` + tables, flip the disabled nav item live).

## 10. Explicit non-goals (MVP of this surface)

- **No ad / paid-traffic analytics** — organic + AI-driven traffic only; no Google/Meta Ads.
- **No billing / cost-of-traffic** anywhere in this surface.
- **No server-log crawler analytics here** — verified AI-crawler identification and
  crawler-to-page analytics are the **separate** Release 1.3 server/edge-log ingestion roadmap
  item, not built in Traffic.
- **No OAuth/connector plumbing here** — GSC/GA4 auth is owned by the GSC/GA4/Bing integrations
  spec; Traffic consumes its sync output.
- **No recomputation in read paths** — every traffic endpoint renders persisted
  `TrafficMetric`/`TrafficSnapshot` rows; only the sync task hits the provider (invariant 7).

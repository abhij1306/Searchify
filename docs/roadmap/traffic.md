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
The two join on the site's own pages (see §5 page join) and feed the correlation view in
[`llm-analytics.md`](llm-analytics.md).

The GSC/GA4 connectors — OAuth, the `IntegrationSyncRun` queue task, the immutable
`IntegrationImportArtifact`s, and the derived `IntegrationMetricRow` fact rows — are owned
**entirely** by the **GSC / GA4 / Bing integrations** roadmap spec
([`integrations.md`](integrations.md)). Traffic does **not** fetch from any provider and does
**not** define its own import/metric tables: it is a **projection over
`IntegrationMetricRow`** (the single source of truth for first-party search/traffic facts).
This spec covers only the traffic **projection, page join, AI-referral classification hook,
and screen** — not the OAuth/connector/sync plumbing and not a second provider-fetching
pipeline (invariant 2 — one concept, one owner).

## 2. Relationship to existing subsystems (grep before adding — invariant 2)

Reuse, do not duplicate:

- **`Project`** (`backend/app/models/project.py`) + **`OwnedDomain`** (`models/brand.py`) —
  identify which site/property the traffic belongs to and validate that a synced property is
  actually an owned domain before ingest.
- **`PageArtifact`** — the immutable per-page row defined in the Technical Audit spec
  ([`technical-audit.md`](technical-audit.md) §3). Traffic **joins query/page metrics to the
  site's crawled pages** by canonical URL; when the Content surface exists it reuses the same
  page identity. Do not invent a second page table — cross-reference `PageArtifact`.
- **`IntegrationMetricRow` / `IntegrationImportArtifact` / `IntegrationSyncRun`**
  ([`integrations.md`](integrations.md) §3) — the connection, sync task, immutable import
  artifacts, and derived first-party fact rows. Traffic **reads** `IntegrationMetricRow` and
  does **not** re-fetch from GSC/GA4 or define a parallel import/metric table (invariant 2).
- **BYOK / Fernet + the Postgres `TaskQueue`** — owned by the integrations spec; Traffic never
  resolves a token or claims a provider-fetch task itself. Its only queued work is the
  projection refresh described in §4.
- **`ReferralEvent`** ([`llm-analytics.md`](llm-analytics.md) §3) — GA4 AI-referral rows are
  classified **from the same `IntegrationMetricRow` output** (the GA4 referrer/landing
  dimensions), not from a Traffic-owned ingest batch; Traffic and LLM Analytics both project
  the integrations import output.

## 3. Data model (projection only — Traffic owns no import/fetch tables)

The immutable import artifacts and the derived first-party fact rows are owned by
[`integrations.md`](integrations.md): `IntegrationImportArtifact` is the written-once import
(invariant 3) and `IntegrationMetricRow` is the provenance-stamped derived row (invariant 4).
Traffic introduces **no** `TrafficImport` and **no** `TrafficMetric` table — those would
duplicate the integrations ownership and create a second, competing source of truth (invariant
2). Traffic's only owned table is a **projection snapshot** computed from
`IntegrationMetricRow`:

- **Source of truth (owned by integrations, read-only here):** `IntegrationMetricRow`
  ([`integrations.md`](integrations.md) §3) supplies the per-`(provider, date, dimension_key)`
  measures — `impressions/clicks/ctr/position` (GSC) and `sessions/engaged_sessions/
  conversions` (GA4) — each already carrying `source_artifact_id` + `importer_version`
  provenance (invariant 4) and a `project_id` resolved via the integrations
  `IntegrationPropertyMapping`. Traffic filters these rows by `project_id`/`workspace_id`; it
  never re-fetches from GSC/GA4. The page join (§5) resolves `page_artifact_id` from
  `IntegrationMetricRow.dimension_key` (the page URL) at projection time.
- **`TrafficSnapshot`** — projection (invariant 7), computed from persisted
  `IntegrationMetricRow` rows for a `(project, window, granularity)`. `id`, `workspace_id`,
  `project_id`, `window_start`, `window_end`, `granularity` (`day|week|month`), `metrics`
  (JSONB: totals, top pages/queries, CTR/position distributions, trend series),
  `source_metric_row_ids` (JSONB — the `IntegrationMetricRow`s aggregated) and
  `source_artifact_ids` (JSONB — their upstream immutable artifacts, so the projection traces
  to raw evidence), `formula_version`, `analyzer_version`, `created_at`. Rebuildable from the
  persisted metric rows; holds nothing not traceable to them.

Because late-data corrections are handled **upstream** (a re-sync bumps the integrations
`resync_seq` and lands a **new** immutable `IntegrationImportArtifact` + a newer
`importer_version` `IntegrationMetricRow`, never an overwrite — [`integrations.md`](integrations.md)
§3/§4), the Traffic projection simply reads the **latest `importer_version` per
`(provider, date, dimension_key)`** and rebuilds the snapshot; there is no Traffic-owned
immutable-import concept to version.

The `TrafficSnapshot` carries `workspace_id`, a string-UUID PK, and is accessed only via
`require_workspace_member` (invariant 5). No integer PKs, no `user_id`.

## 4. Projection refresh (no provider fetch here — invariants 2, 7)

**Traffic never fetches from a provider.** All provider I/O — OAuth, paging GSC/GA4, and
writing immutable import artifacts + derived `IntegrationMetricRow`s — happens exactly once, in
the integrations `IntegrationSyncRun` worker ([`integrations.md`](integrations.md) §4). There is
**no** `traffic_sync` task and **no** second provider-fetching worker (invariant 2). Traffic's
only work is (re)building the `TrafficSnapshot` projection and triggering AI-referral
classification, both as pure projections over already-persisted `IntegrationMetricRow` rows:

1. When an integrations sync finishes and lands/updates `IntegrationMetricRow`s for a project's
   GSC/GA4 property, a **snapshot-refresh** projection job is enqueued (the integrations worker
   fires it as its post-derivation step — [`integrations.md`](integrations.md) §4 step 5). A
   user "refresh" from the UI enqueues the same projection job; it never re-hits a provider.
2. The snapshot-refresh job reads the **latest-`importer_version`** `IntegrationMetricRow`s for
   the `(project, window)` (so upstream late-data corrections are picked up automatically) and
   recomputes `TrafficSnapshot`, stamping `source_metric_row_ids` + `source_artifact_ids` +
   `formula_version`/`analyzer_version` (invariants 4, 7). It performs **no** network I/O, so
   the queue's commit-before-I/O rule (invariant 8) is trivially satisfied.
3. For GA4 rows carrying referral signals, the same completion also triggers the
   `classify_referrals` task ([`llm-analytics.md`](llm-analytics.md) §5) — again a projection
   over `IntegrationMetricRow`, not a second fetch.

Snapshot refresh is idempotent: recomputing from the same latest-version metric rows yields the
same snapshot, and a new upstream `IntegrationImportArtifact`/`importer_version` (late-data
re-sync) simply triggers a fresh recompute. Cancellation, where a refresh is queued, is
cooperative (invariant 9); the projection stops at a metric-row batch boundary.

## 5. Page join (to `PageArtifact` / Content)

The page-URL `dimension_key` on the source `IntegrationMetricRow` is canonicalized with the
**same URL normalization** the analysis pipeline already uses (`analysis/normalization.py` —
reuse it, invariant 2) so GSC/GA4 page rows and crawled `PageArtifact` rows join cleanly. When
a Technical Audit crawl exists for the project, the projection resolves a `page_artifact_id` by
canonical URL match; unmatched pages resolve to null (still valid — they're measured pages the
crawler hasn't seen). This join is what lets the future Content and Opportunities surfaces rank
pages by traffic-vs-visibility gap.

## 6. API surface (roadmap; `/api/v1`, projections only — invariant 7)

- `GET /projects/{id}/traffic?from=&to=&granularity=` — headline projection over
  `TrafficSnapshot`: totals + trend series for impressions/clicks/CTR/position (GSC) and
  sessions/conversions (GA4).
- `GET /projects/{id}/traffic/pages?from=&to=&sort=` — paged page-level rows (projection over
  the project's `IntegrationMetricRow`s, optionally joined to `PageArtifact`).
- `GET /projects/{id}/traffic/queries?from=&to=&sort=` — paged GSC query-level rows (projection
  over `IntegrationMetricRow`).
- `POST /projects/{id}/traffic/sync` — request fresh provider data. This does **not** run its
  own fetch; it enqueues an on-demand `IntegrationSyncRun` on the owning GSC/GA4 connection via
  the integrations surface ([`integrations.md`](integrations.md) §4/§5) and returns the queued
  run id. The `TrafficSnapshot` refresh (§4) is triggered when that integrations run completes.
  409 if a sync for the same window is already active upstream.

All workspace-scoped via `require_workspace_member` (invariant 5); cross-workspace access
returns 403/404. Read endpoints never call GSC/GA4 — they render persisted
`IntegrationMetricRow` / `TrafficSnapshot` rows (invariant 7). No Traffic code path talks to a
provider; provider I/O lives solely in the integrations sync worker.

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

- `TRAFFIC_DEFAULT_WINDOW_DAYS`, `TRAFFIC_MAX_WINDOW_DAYS`, `TRAFFIC_SNAPSHOT_GRANULARITIES`
  (projection window + granularity knobs).
- `TRAFFIC_FORMULA_VERSION` / `TRAFFIC_NORMALIZATION_VERSION` — the `formula_version` /
  `analyzer_version` stamped on `TrafficSnapshot` (invariant 4).
- Provider-fetch knobs (sync window/dimensions, row limits, request timeout, queue retry
  budget, GSC/GA4 API base urls + scopes) belong to the integrations connector config, **not
  here** — Traffic performs no fetch, so it must not duplicate them (invariant 2).

## 9. Suggested build order

1. Config: `config/traffic.py` projection knobs + version constants; migration for the single
   `TrafficSnapshot` table (no import/metric tables — those are owned by integrations).
2. Normalization: reuse `analysis/normalization.py` URL canonicalization for the page join
   against `IntegrationMetricRow.dimension_key` (table-tested against fixtures).
3. `TrafficSnapshot` projection builder over `IntegrationMetricRow` (latest-`importer_version`
   per key) + the snapshot-refresh job triggered by the integrations sync worker. **No**
   provider fetch and **no** `traffic_sync` task — provider I/O stays in the integrations
   `IntegrationSyncRun` worker.
4. API routers (projections + the `POST .../sync` pass-through that enqueues an
   `IntegrationSyncRun`) + zod contracts.
5. Frontend `/traffic` screen (wire `trend-chart` + tables, flip the disabled nav item live).

## 10. Explicit non-goals (MVP of this surface)

- **No ad / paid-traffic analytics** — organic + AI-driven traffic only; no Google/Meta Ads.
- **No billing / cost-of-traffic** anywhere in this surface.
- **No server-log crawler analytics here** — verified AI-crawler identification and
  crawler-to-page analytics are the **separate** Release 1.3 server/edge-log ingestion roadmap
  item, not built in Traffic.
- **No OAuth/connector plumbing here** — GSC/GA4 auth is owned by the GSC/GA4/Bing integrations
  spec; Traffic consumes its sync output.
- **No recomputation in read paths** — every traffic endpoint renders persisted
  `IntegrationMetricRow`/`TrafficSnapshot` rows; only the integrations `IntegrationSyncRun`
  worker hits the provider (invariant 7).
- **No second provider-fetching pipeline** — Traffic defines no `traffic_sync` task, no
  `TrafficImport`/`TrafficMetric` table, and no provider-fetch worker; it is purely a
  projection over the integrations import output (invariant 2).

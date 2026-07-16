# Roadmap — Cross-run Visibility trend history

> **Status: IMPLEMENTED (v2 Visibility Insights).** This spec is retained as a design record;
> the surface has shipped. Live owners:
> - Backend: `GET /projects/{id}/visibility/trends` in `backend/app/api/projects.py`, projected
>   by `get_visibility_trends` in `backend/app/domain/analysis/service.py`
>   (`VisibilityTrendPoint`). The companion persisted-evidence endpoint
>   `GET /projects/{id}/visibility/evidence` (`VisibilityEvidenceResponse{items,truncated}`)
>   feeds the Mentions & Citations + Query Fanout tabs.
> - Frontend: Trends tab `frontend/components/visibility/visibility-trends.tsx` within the
>   four-tab workspace `frontend/components/visibility/visibility-dashboard.tsx`.
> - Tests: `backend/tests/component/test_analysis_http.py`,
>   `backend/tests/component/test_analysis_api.py`, `frontend/lib/visibility/dashboard.test.ts`,
>   `frontend/app/(app)/visibility/page.test.tsx`.
>
> The design below is preserved for context. It follows the same conventions as the MVP: UUID
> PKs, workspace scoping via `require_workspace_member`, immutable artifacts, provenance +
> version on every derived row, and config-in-config-only.

## 1. Goal & positioning

The MVP Visibility dashboard is **single-run**: `GET /projects/{id}/visibility?audit_id=<id>`
projects one selected audit (defaulting to the latest completed). This surface adds **cross-run
trend history** — Visibility Score, Share of Voice (SOV), and brand-vs-competitor rankings
plotted **over time across multiple completed audits** for a project.

The critical property that makes this cheap and safe: it is **almost entirely a projection
over data that already exists**. Every completed audit already persists exactly one
`MetricSnapshot` (`backend/app/models/analysis.py`) with `visibility_score`, the full `metrics`
dict, `analyzer_version`, `scoring_rule_version`, and provenance
(`source_analysis_ids`/`source_artifact_ids`). A trend is those per-run snapshots, ordered by
time. **No recomputation, no provider calls, no re-extraction** (invariant 7).

## 2. Existing hooks to reuse (grep before adding — invariant 2)

This surface deliberately adds **no new persistence** at MVP-of-this-surface. It wires
together things that are already built:

- **`MetricSnapshot`** (`backend/app/models/analysis.py`) — one row per completed audit,
  already carries `visibility_score`, `metrics` (JSONB: headline rates, SOV, per-engine,
  per-prompt stability, citation shares), `total_completed`/`total_failed`, `analyzer_version`,
  `scoring_rule_version`, and its source-evidence ids. **This is the entire data source.**
- **`Audit`** (`models/audit.py`) — supplies `completed_at`/`created_at` (the trend x-axis),
  `configuration`, and per-engine snapshots (`AuditEngineSnapshot`) so a trend can be filtered
  by `logical_engine` (invariant 10).
- **The existing single-run projection** in `app/api/projects.py`
  (`GET /projects/{id}/visibility`) + its `analysis/` projection helpers — the trends endpoint
  reuses the same DTO-shaping code path per point (do not fork a second projector, invariant 2).
- **Frontend `trend-chart` primitive** (`frontend/components/ui/trend-chart.tsx`) — **already
  built but unused in MVP**, kept ready for exactly this view (see frontend-architecture.md
  §9). Wire it in; do not build a new chart component.

## 3. Data model

**No new tables at MVP of this surface.** The trend is a **projection** (invariant 7) over the
already-persisted per-run `MetricSnapshot` rows. This keeps the change minimal and honors
"reports/metrics are projections, not recomputation".

A `VisibilityTrendPoint` DTO (response-only, not persisted). For a raw per-run point it
projects a single audit; for a week/month **bucket** it folds every contributing
`MetricSnapshot`, so provenance is a **list**, not a single id:
`audit_id` (nullable — set for a raw per-run point, null for a multi-run bucket),
`completed_at`, `logical_engine` (nullable — present when the point is an
engine-filtered slice), `visibility_score`, `sov` (response-level + mention-level),
`brand_mention_rate`, `owned_citation_rate`, `sentiment` (**null**), `avg_position`
(**null**), `source_snapshot_ids` (JSONB list — the `MetricSnapshot.id`s this point folds;
one for a raw point, many for a bucket — provenance, invariant 4), and **version metadata**
`analyzer_versions` + `scoring_rule_versions` (the distinct versions across the folded
snapshots) plus a `spans_version_boundary` flag. When a bucket would fold snapshots produced
under **different** `analyzer_version` / `scoring_rule_version` values, the point is either
labelled as version-mixed (all contributing versions listed, `spans_version_boundary=true`) so
the UI never attributes a version step to a real visibility shift, **or** — under the strict
config option (§4) — such a bucket is **not emitted at all** and the range is returned as
raw per-run points instead. Provenance is therefore never ambiguous across a version change.

**Optional future optimization (roadmap+, not required):** a `VisibilityTrendSnapshot`
projection-cache table (UUID PK, workspace-scoped, `metrics` JSONB, `source_snapshot_ids`
JSONB, `formula_version`) if per-request projection over many runs ever becomes a hotspot.
It would be a rebuildable cache of the same projection — never a new source of truth, always
traceable to the `MetricSnapshot` rows it folds (invariant 4 + 7). Not built at MVP; a plain
query-time projection is sufficient.

## 4. Determinism & the null metrics (invariant 9)

`sentiment` and `avg_position` are **present in the schema but null at MVP** (invariants doc,
"Note on not-yet-computed metrics"). The trend view **carries them forward as null** and the
UI renders `—` — it must **not** back-fill them with a heuristic that pretends to be
deterministic, and must **not** call an LLM to compute a trend point (invariant 9). Because
every point is a projection of a persisted, versioned snapshot, the trend is fully
reproducible from the same audits.

**Version continuity:** points may span different `analyzer_version` / `scoring_rule_version`
values if audits ran across a scoring change. The endpoint returns the version(s) on every
point and the UI surfaces a marker where the version changes, so a step in the line is never
silently attributed to a real visibility shift. For **bucketed** points that fold many
snapshots this means listing every contributing version (`analyzer_versions` /
`scoring_rule_versions`) with `spans_version_boundary`; a config knob
(`TRENDS_STRICT_VERSION_BUCKETS`) makes version-crossing buckets **prohibited** instead —
the range then falls back to raw per-run points so no bucket ever mixes versions. Never re-run
old audits under the new version to "normalize" the series — that would be recomputation
(invariant 7) and would mint new artifacts (invariant 3).

## 5. API surface (roadmap; `/api/v1`, projection only — invariant 7)

- `GET /projects/{id}/visibility/trends?engine=&from=&to=&granularity=` — the ordered series of
  `VisibilityTrendPoint`s for the project's completed audits, optionally filtered by
  `logical_engine` (invariant 10) and time window. `granularity` controls whether raw per-run
  points or bucketed (week/month) aggregates are returned; bucketing is a deterministic
  aggregate over the persisted snapshots, still no recomputation. A bucket may fold **multiple**
  `MetricSnapshot` rows, so each bucketed point carries `source_snapshot_ids` (the full list it
  folds) and lists every contributing `analyzer_version` / `scoring_rule_version` with a
  `spans_version_boundary` flag (invariants 4 + 7). Buckets that would cross a version boundary
  are either version-labelled or — under the strict config option (§4) — **not emitted**, with
  the range returned as raw per-run points instead; provenance is never ambiguous.
- Reuses the existing `/projects/{id}/visibility` router file (`app/api/projects.py`) — add the
  sub-route there rather than a new module (invariant 2).

Workspace-scoped via `require_workspace_member` (invariant 5); only completed audits with a
`MetricSnapshot` contribute. Cross-workspace access returns 403/404. **No provider call, no
re-extraction** — reads persisted snapshots only (invariant 7).

## 6. Frontend (roadmap)

- **Where:** wire the trend view into the existing `/visibility` screen (a "Trend" tab/toggle
  alongside the single-run projection) **or** the `/analytics` route
  ([`llm-analytics.md`](llm-analytics.md)); the same series feeds either. `/visibility` is
  already MVP-live, so a trend tab there is the lowest-friction landing spot.
- Reuse the built-but-unused `trend-chart` primitive (`components/ui/trend-chart.tsx`) — this
  is exactly what it was kept for. Add a `getVisibilityTrends` call to
  `frontend/lib/api/visibility.ts` (extend the existing module, invariant 2), a
  `visibilityTrendPointSchema` in `schemas.ts`, and a `queryKeys.visibility.trends(...)` key.
- The chart plots `visibility_score` / `sov` over `completed_at`, with an engine filter mapped
  to `logical_engine`. `sentiment`/`avg_position` render `—` (still null, §4). Version-change
  markers on the x-axis (§4).
- Every response passes `strictValidate`; ids are `z.string().uuid()`; no `user_id`. Same-origin
  `/api/*` proxying only (invariant 12); TanStack Query with the shared retry policy.

## 7. Config & tuning knobs (all in `backend/app/core/config/`)

Minimal — this is a projection. In `config/analysis.py` (reuse; do not fork):

- `VISIBILITY_TREND_DEFAULT_GRANULARITY`, `VISIBILITY_TREND_MAX_POINTS`
  (cap the series length for a request), `VISIBILITY_TREND_BUCKETS` (allowed granularities).
- Reuse the existing `ANALYZER_VERSION` / `SCORING_RULE_VERSION` constants — the trend stamps
  each point with the version its source snapshot already carries; it introduces **no new**
  version constant (invariant 2).

## 8. Suggested build order

1. Backend projection: add the `trends` sub-route to `app/api/projects.py` + a projection
   helper that reads `MetricSnapshot` rows for the project's completed audits, ordered by
   `completed_at`, shaped into `VisibilityTrendPoint`s (unit-tested against seeded snapshots;
   no provider calls).
2. Optional bucketing/granularity aggregation (deterministic).
3. Frontend: extend `lib/api/visibility.ts` + `schemas.ts` + `query-keys.ts`; add the Trend
   tab to `/visibility` wiring the existing `trend-chart` primitive.
4. Config knobs in `config/analysis.py`.

## 9. Explicit non-goals (MVP of this surface)

- **No recomputation of historical runs** — the trend projects the `MetricSnapshot` each audit
  already persisted; it never re-scores or re-runs a past audit (invariant 7).
- **No back-fill of null metrics** — `sentiment` and `avg_position` stay null/`—`; no heuristic
  or LLM fills them (invariant 9).
- **No new immutable artifacts** — reading snapshots mints nothing; a rerun of an audit is a
  new audit identity with its own snapshot, never an overwrite (invariant 3).
- **No cross-project or cross-workspace trends** — a trend is scoped to one project within one
  workspace (invariant 5).
- **No provider calls in the trend path** — it is a pure projection over persisted evidence.

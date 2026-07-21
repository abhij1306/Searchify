# Site Health

Site Health is Searchify's in-house **on-page + AEO crawler**. It discovers a
project's URLs with a first-party HTTP crawler (no headless browser, no
PageSpeed/CrUX, no raw-HTML storage), analyzes each admitted page against a
deterministic rule catalog, scores it on two dimensions (**Technical** and
**AEO**), and surfaces the result as a dashboard, a grouped issues catalog, and a
per-URL detail view. Every persisted row is projected through a workspace-scoped
service layer — the API never re-fetches, re-scores, or fabricates a metric.

This document is the reconciled reference for the feature's **entitlements,
statuses, API surface, exports, and frontend routes**. It matches the code in
`backend/app/api/site_health.py`, `backend/app/core/config/site_health.py`,
`backend/app/analysis/site_health/`, and `frontend/app/(app)/site-health/`.

- Deep worker/analysis internals: `docs/roadmap/technical-audit.md` (original
  design spec) and `docs/backend-architecture.md`.
- Local entitlement granting: `docs/DEVELOPMENT.md` → "Site Health entitlements".
- Design tokens / per-screen layout: `docs/design.md`.

---

## Entitlements & capabilities

Site Health is **capability-gated per workspace**, stored one row per workspace
in `workspace_site_health_entitlements`. The capability key (`plan_key`) is
`free` or `starter` — never a marketing display name. A workspace with no
explicit entitlement **fail-closes to Free** (the most restrictive capability).

| Capability | Discovery mode | Discovered total disclosed? | Monitored selection | Analysis scope |
|---|---|---|---|---|
| `free` | `sample` (deterministic, seeded, read-only) | **No** — the full-site/discovered total is never revealed | Not allowed (a sample set is auto-selected) | The Free sample only |
| `starter` | `full` (progressive inventory) | Yes | User picks a monitored URL set (quota-limited) | The selected monitored set |

- **Free sample behavior.** Free crawls a deterministic, seeded sample and is
  **read-only**: the user cannot pick a monitored set, and no event, crawl
  projection, or export ever leaks a discovered/total/frontier count
  (`can_view_discovered_total = false`). The crawl's admitted (visible) URL count
  is *not* a full-site total and is shown. This non-disclosure is enforced in
  three layers: the entitlement flag, the event serializer
  (`redact_event_payload`), and the crawl projection (which nulls
  `discovered_count` / `total_url_count` / `has_more_site_urls`).
- **Starter monitored selection.** Starter runs the full progressive inventory.
  The user selects a monitored URL set via a **full-set, versioned** replacement
  (`PUT /projects/{id}/monitored-urls`). The set is bounded by the entitlement's
  `monitored_url_limit` (workspace-wide, counted under a `FOR UPDATE` entitlement
  lock). Selecting URLs converts the deterministic Free sample rows to
  user-managed rows (deactivated rows are never deleted, so evidence survives).
- **Granting a capability locally:** see `docs/DEVELOPMENT.md`. Production billing
  may later call the same domain service
  (`app.domain.site_health.entitlements.set_entitlement`).

`GET /api/v1/entitlements` returns: `plan_key`, `access_mode`,
`sample_url_limit`, `monitored_url_limit`, `can_view_discovered_total`,
`capability_revision`, and timestamps.

---

## Status vocabulary

All status/vocabulary constants are owned by
`backend/app/core/config/site_health.py` (read from it; do not hardcode).

- **Crawl status** (`CrawlResponse.status`): `draft`, `validating`, `queued`,
  `running`, `completed`, `partially_completed`, `failed`, `cancelled`. The last
  four are terminal.
- **Discovery status**: `pending`, `running`, `completed`, `sample_completed`,
  `failed`, `cancelled`.
- **Analysis status** (crawl-level): `pending`, `running`, `completed`,
  `partially_completed`, `failed`, `cancelled`.
- **Per-page presentation status** (`PageSummary.analysis_status`): the derived,
  mockup-facing status. A raw `failed` page-analysis row is **never** surfaced as
  page copy — it maps to `error` (or `blocked` for a policy denial such as
  robots/SSRF, carrying the error code). Possible values: `completed`,
  `partially_completed`, `pending`, `running`, `error`, `blocked`, `cancelled`,
  `not_selected`.
- **Rule outcome**: `pass`, `fail`, `not_applicable`, `error`.
- **Severity**: `critical`, `high`, `medium`, `low`, `info`.
- **Dimension**: `technical`, `aeo`.
- **Scores.** Technical / AEO / overall scores are `0–100` floats. A missing or
  failed score is **`null`** in the API and renders as an em dash (`—`) in the
  UI — never a fabricated `0`.

---

## API surface

All endpoints live under `/api/v1` (no `workspace_id` in the path). The active
workspace is resolved by `require_active_workspace` from the `X-Workspace-Id`
header (or the caller's default workspace) and **every** lookup is filtered by
it, so a foreign/missing id is always a `404` (invariant 5). Keyset (cursor)
pagination is used throughout; a malformed/tampered/scope-mismatched cursor is a
typed `400`, never a `500`.

| Method & path | Purpose |
|---|---|
| `GET /entitlements` | Workspace Site Health entitlement (seeds fail-closed Free on first use). |
| `POST /site-crawls` | Create + queue a crawl for a project. `seed` must be an integer string. `201`; a second active crawl for the project is `409` (`crawl_already_active`); an unusable root is `422` (`invalid_root`); unknown project is `404`. |
| `GET /site-crawls?project_id=&limit=&cursor=` | List crawls (created-at keyset). |
| `GET /site-crawls/{crawl_id}` | Crawl summary/projection (redacted for Free). |
| `POST /site-crawls/{crawl_id}/cancel` | Cancel a crawl → `cancelled`. |
| `GET /site-crawls/{crawl_id}/inventory?limit=&cursor=&query=&status=&monitored=` | Admitted-URL inventory (selection source of truth). |
| `GET /projects/{project_id}/monitored-urls` | Current monitored set + quota + `selection_version`. |
| `PUT /projects/{project_id}/monitored-urls` | Full-set, versioned monitored-set replacement. `403` `starter_required` (Free) / `site_health_quota_exceeded`; `409` `stale_selection_version` (carries `current_selection_version`); `422` for unknown URL ids. |
| `GET /site-crawls/{crawl_id}/pages?limit=&cursor=&query=&status=&monitored=` | Dashboard page rows (derived `analysis_status` + `error_code`, monitored flag, scores). |
| `GET /site-crawls/{crawl_id}/pages/{site_url_id}` | Per-URL detail (facts, delivery, evaluations, issues, link refs). |
| `GET /site-crawls/{crawl_id}/pages/{site_url_id}/issue-history?limit=&cursor=` | Crawl-bounded issue history for a URL. |
| `GET /site-crawls/{crawl_id}/issues?limit=&cursor=&query=&severity=&category=&dimension=&rule=&site_url_id=` | Grouped issues catalog + summary tiles. The grouped-issue wire filter is `rule` (not `rule_id`). |
| `GET /site-crawls/{crawl_id}/issues/{canonical_id}` | Grouped-issue detail (a non-representative member id canonicalizes to the earliest `(created_at, id)`). |
| `GET /projects/{project_id}/site-health?crawl_id=` | Dashboard projection (defaults to the latest completed crawl). |
| `GET /site-crawls/{crawl_id}/events?stream=` | Event replay (`stream=false`, default → ordered JSON list) or SSE (`stream=true`). Free payloads are redacted. |
| `GET /site-crawls/{crawl_id}/export.csv?view=` | CSV export. |
| `GET /site-crawls/{crawl_id}/export.md?view=` | Markdown export. |

### Key crawl-projection fields

`CrawlResponse` aliases model columns to the contract:
`random_seed → seed`, `admitted_url_count → visible_url_count`,
`analyzed_url_count → analyzed_count`, `failed_url_count → failed_count`,
`rule_catalog_version → rule_version`. For a **Free** (non-disclosing) crawl,
`discovered_count`, `total_url_count`, and `has_more_site_urls` are `null`.

---

## Exports

CSV (`export.csv`) and Markdown (`export.md`) render the **same
workspace-scoped, already-projected rows** the JSON API returns, so an export can
never leak more than the API. The `view` query parameter selects the projection:

- `inventory` — admitted-URL inventory columns.
- `pages` — dashboard page columns (status, error code, scores).
- `issues` — grouped issues columns.

Exports are **authenticated blob downloads** (`Content-Disposition: attachment`),
so a selected non-default workspace's `X-Workspace-Id` header is carried (a plain
`<a href>` navigation cannot). CSV/Markdown cells beginning with a
spreadsheet-formula trigger (`=`, `+`, `-`, `@`) are prefixed with `'` to
neutralize formula injection; Markdown cell content is additionally escaped so a
`|` or newline cannot break the table.

---

## Frontend routes

Site Health and Issues are live MVP nav items.

| Route | Screen |
|---|---|
| `/site-health` | The Site Health screen: discovery-in-progress, inventory selection, live analysis, and the completed dashboard (mockups 708 / 709 / 712 / 713). The phase is derived from the crawl + pages queries. |
| `/site-health/crawls/[crawlId]/pages/[siteUrlId]` | Per-URL detail: metadata, Technical/AEO/overall score rings (`—` for null), delivery metrics, all issues by severity, and crawl-bounded issue history (mockup 711). |
| `/issues` | Grouped Issues catalog: severity/occurrence/affected-page summary tiles, grouped cards with remediation, server-backed search/filter/pagination, and affected-URL navigation (mockup 710). |

Data flow notes:

- **Polling-first.** The screen polls the crawl/pages/dashboard while active. SSE
  (`use-crawl-events.ts`, a credentialed abortable `fetch` reader — not
  `EventSource`, so `X-Workspace-Id` is sent) is only a polling-invalidation
  accelerator; a dropped stream never stops polling.
- **Exports** go through `lib/site-health/download.ts` →
  `apiClient.getBlob` so the workspace header + credentials are carried, and the
  object URL is revoked after download.
- **Selection** commits a full versioned monitored set; a `409`
  `stale_selection_version` surfaces a stale notice and rebases (no silent
  overwrite).
- **Discovered inventory continuity.** A Starter recrawl freezes a bounded
  newest-first lineage of earlier full-crawl ids in its configuration. The
  inventory and `All Discovered` read models union those immutable observation
  sets while the new crawl re-discovers the site, so starting analysis never
  collapses hundreds of discovered URLs to only the monitored subset. Current
  results stay current-crawl-only; inherited rows link to the source crawl that
  owns their persisted detail. Free/sample crawls ignore this lineage entirely.

---

## Screen lifecycle & phase precedence

The Site Health route renders one canonical dashboard layout. Its score cards,
compact status row, and inventory stay mounted while their data/mode changes.
`resolveSiteHealthPhase(crawl, plan, hasMonitoredSelection)` in
`frontend/lib/site-health/status.ts` is the single source of truth for those
view modes. Its clauses are mutually exclusive and evaluated in this explicit,
deterministic precedence (top wins):

1. **no crawl** → `empty` (first-run "Start discovery" card).
2. **`completed` / `partially_completed`** → `dashboard`.
3. **any crawl with `score_summary`** (including `cancelled`/`failed` mid-run) →
   `dashboard`. Score data always outranks the discovering/analyzing sub-states,
   so a landed projection is never hidden behind an active-looking view.
4. **`failed` without data** → `terminal` (explicit stopped card + restart).
5. **`cancelled` without data**: Starter with discovered URLs → `selection`
   (the inventory persists through a cancel and re-seeds the next crawl);
   otherwise → `terminal`.
6. **active Starter crawl + committed monitored set** → `analyzing`, including
   the interval where re-discovery is running and `analysis_status` still says
   `pending`.
7. **discovery still running** → `discovering`.
8. **analysis running** → `analyzing`.
9. **Starter + analysis pending** → `selection`; otherwise (Free auto-analysis)
   → `analyzing`.

### Cancellation with partial data

When a run is cancelled after it produced scores, the product keeps the **latest
dashboard, partial scores, and URL inventory visible**, explicitly labels the run
**Cancelled** (a text-labelled badge + notice, never color alone —
`dashboardRunNotice` in `status.ts`), and offers **Re-crawl**. The same
notice covers `partially_completed` (Partial) and `failed`-with-data. A cancel
that produced *no* data routes to `selection` (Starter, inventory survives) or
`terminal`.

### Retaining content during transitions

- **Cancelling** (cancel request in flight): the discovery/analysis views keep
  their inventory and counts on screen and swap the status line to an
  `aria-live` "Cancelling…" message — nothing is torn down mid-request.
- **Re-crawl starting** (`recrawlStarting` in `use-site-health-screen.ts`): the
  prior dashboard/selection stays in view behind an info notice until the new
  crawl's first projection takes over.
- **Recrawl** re-seeds from the committed monitored set — a fresh crawl
  re-discovers and enqueues the persisted selection (a cancelled crawl cannot
  enqueue analyze tasks itself). Its `All Discovered` tab retains the earlier
  full inventory until re-discovery refreshes those URL observations.

### Count integrity during loading

The live analysis counters come from server-side aggregates
(`analyzed_count` / `failed_count`) and the per-project monitored count, never
from the bounded page window. Until the selected total is known (no terminal
`score_summary` **and** the monitored count has not resolved), **Total pages**
and **Queued** render `—` rather than a misleading `0`. A failed monitored-count
fetch is surfaced as a warning (`projectSelectedError`) instead of silently
approximating or disabling actions. Missing scores always render `—`, never a
fabricated zero.

The same server-backed Monitored / All Discovered / Errors table renders during
analysis and after completion. Its first cursor page polls while active, so row
statuses and scores fill in without swapping to a separate results screen.

Free non-disclosure is preserved across every state: no phase leaks a
discovered/full-site total, and sample-mode discovery never implies continued
full-site scanning.

---

## Guardrails (for anyone extending Site Health)

- Keep workspace resolution on `require_active_workspace`; a foreign/missing id
  must be an indistinguishable `404`.
- Preserve Free count/event/export **non-disclosure** (never leak a
  discovered/full-site total).
- Keep inherited inventory ids as read-scope references only. Never manufacture
  a `SiteUrlObservation` or copy old analysis into a new crawl.
- Read status/severity/dimension/limits/error tokens from
  `app/core/config/site_health.py`; never hardcode them.
- No raw-HTML storage, no PageSpeed/CrUX, no headless browser.
- The service layer only **projects persisted rows** — it never re-fetches or
  re-scores.

# Site Health — Current Handoff

**Last updated:** 2026-07-17
**Repository:** `abhij1306/Searchify`
**Branch:** `vorflux/site-health`
**Pull request:** https://github.com/abhij1306/Searchify/pull/5

## Current status

Site Health Slices 1–9 are implemented, committed, pushed, and integrated with the latest `main` branch. The feature branch is no longer a broken checkpoint or partial implementation.

The current integration commit is `6fdaed2` (`merge: resolve Site Health with visibility v2`). GitHub reports the pull request as mergeable with no content conflicts.

## Completed work

### Slices 1–5 — persistence, crawling, selection, and analysis

- Workspace Site Health entitlements for Free sample and Starter monitored-selection behavior.
- Durable crawl, URL, task, artifact, observation, monitored URL, analysis, evaluation, issue, snapshot, and event persistence.
- Generic Postgres queue integration without changing shared claim ordering.
- SSRF-resistant HTTP discovery with bounded redirects, response sizes, content types, URL admission, and deterministic normalization.
- Atomic monitored-set lifecycle with quota and selection-version checks.
- Deterministic HTML fact extraction, structured-data inspection, Technical/AEO rules, scoring, issue projection, link references, snapshots, worker lifecycle reconciliation, cancellation, retry, and partial-completion behavior.

Key commits:

- `5374274` — persistence graph, entitlements, queue, and `0008_site_health`
- `9c7f0ff` — frontend contracts and pure helpers
- `acd707f` — secure discovery and progressive inventory
- `b89ec14` — monitored-set lifecycle and worker guards
- `6f8bfb1` — analysis worker pipeline

### Slice 6 — APIs, isolation, SSE, and exports

- Workspace-isolated crawl, inventory, monitored selection, dashboard, pages, grouped issues, page detail, issue history, events, and export endpoints.
- Selected-crawl URL scoping prevents historical-catalog and Free-after-Starter leakage.
- Typed/filter-bound keyset cursors and 400 responses for malformed or replayed cursors.
- Free count and event redaction without hidden total side channels.
- Stable grouped-issue canonical identity and crawl-bounded history.
- Complete page-detail evaluations and deduplicated link references.
- Credentialed, abortable SSE transport with polling retained as the primary progress mechanism.
- Authenticated CSV/Markdown blob exports with spreadsheet-formula neutralization.
- Independent reconciliation coverage for all seven previously outstanding review items.

Key commits:

- `fad1139` — API checkpoint
- `e177389` — API isolation and detail projections
- `0c021e3` — Slice 6 reconciliation and regression proof

### Slice 7 — Site Health workflow and dashboard UI

- `/site-health` discovery, staged monitored selection, live analysis, cancellation, polling/SSE invalidation, dashboard scores, coverage, tabs, page states, and authenticated exports.
- Site Health navigation enabled.
- Missing, blocked, and failed scores render `—`; the UI does not invent zero scores.
- Server-backed filtering and cursor pagination are used instead of filtering only the current client page.

Commit: `04c7c6a`.

### Slice 8 — Issues and per-URL detail UI

- `/issues` grouped catalog with summaries, severity/dimension/search filters, remediation, affected URLs, keyset pagination, and copy-fix support.
- `/site-health/crawls/[crawlId]/pages/[siteUrlId]` with URL metadata, Technical/AEO/Combined scores, delivery facts, normalized facts, issues, evaluations, evidence, link references, remediation, and bounded issue history.
- Dashboard View links and Issues navigation activated.
- Frontend schemas aligned with the actual backend grouped-issues/detail/history contracts.

Commit: `bd0470f`.

### Slice 9 — integration and documentation

- Added `docs/site-health.md` as the shipped product/API reference.
- Updated README, architecture, design, development, and roadmap documentation.
- Added deterministic DB-backed integration coverage for:
  - create → discover → select → analyze → dashboard → issues → URL detail → export;
  - create conflict and cancellation;
  - stale monitored-selection conflict;
  - partial/error projections without fabricated scores;
  - Free redaction through events and projections;
  - non-default workspace reads and exports.

Commit: `4ff8d0d`.

### Latest `main` integration

The branch was merged with `main` commit `854ef0c` (Visibility Insights v2).

Resolved integration details:

- Preserved both Site Health and Visibility Insights schemas and TypeScript exports in `frontend/lib/api/schemas.ts` and `frontend/lib/api/types.ts`.
- Preserved all other auto-merged Visibility Insights changes.
- Added no-op merge migration `0009_merge_site_health_openai` to join the parallel `0008_site_health` and `0008_direct_openai_retirement` Alembic branches.
- Updated `docs/DEVELOPMENT.md` with the branched-and-merged migration graph.

Integration commit: `6fdaed2`.

## Verification completed

### Backend

- Full merged backend suite: **429 passed**.
- All backend Ruff checks pass when excluding the unrelated pre-existing comment in `backend/app/domain/auth/service.py`.
- Alembic reports one head: `0009_merge_site_health_openai`.
- A clean temporary PostgreSQL database successfully upgraded from the empty baseline through both `0008` branches to the merged `0009` head.

### Frontend

- Full merged Vitest suite: **310 passed**.
- `pnpm exec tsc --noEmit`: passed.
- `pnpm check:policy`: passed.
- `pnpm build`: passed, including `/site-health`, `/issues`, per-URL detail, and merged Visibility routes.

### Browser verification already performed

- Discovery in progress.
- Inventory selection and quota behavior.
- Analysis progress and page states.
- Completed dashboard.
- Grouped Issues catalog and affected-URL expansion.
- Per-URL detail.
- Dashboard-to-detail navigation.
- Non-default workspace request path.

The temporary design mockup images used during implementation have been removed from the repository because the feature is implemented and the owner requested their deletion.

## Pending work

The following independent final verification remains optional before merging or can be performed later from this branch:

1. Run a fresh live worker crawl against a migrated server and capture the complete browser journey from crawl creation through export.
2. Explicitly exercise live SSE resume, timeout, disconnect, and polling fallback against the real `SessionLocal()` database path.
3. Run final responsive and accessibility browser smoke checks in light and dark themes.
4. Capture fresh final evidence if required for release documentation.
5. Investigate any GitHub checks responsible for the PR's `UNSTABLE` state; there are no merge conflicts.

These are verification tasks, not known missing Site Health implementation slices.

## Known issue unrelated to Site Health

A full `uv run ruff check .` reports one pre-existing E501 line-length violation in `backend/app/domain/auth/service.py:3`. The Site Health and merge work did not modify that file. All other backend Ruff checks pass.

## Useful commands

```bash
export PATH="$HOME/.local/bin:$PATH"
export TEST_DATABASE_URL="postgresql+asyncpg://postgres:searchify_dev_password@localhost:55432/test_db"

cd infra/docker
docker compose up -d db

cd ../../backend
uv sync --extra dev --frozen
uv run pytest -q
uv run ruff check . --exclude app/domain/auth/service.py
uv run alembic heads

cd ../frontend
pnpm install --frozen-lockfile
pnpm test -- --run
pnpm exec tsc --noEmit
pnpm check:policy
pnpm build
```

## Guardrails for future changes

- Preserve workspace resolution through `require_active_workspace`; foreign or missing workspace IDs must remain indistinguishable 404s.
- Preserve Free count, event, and export redaction.
- Keep SSE as an invalidation accelerator; polling must continue if the stream drops.
- Do not change shared Postgres queue claim ordering without proving safety for both audit and Site Health workers.
- Do not store raw fetched HTML or expose secret-bearing headers/evidence.
- Extend the migration graph from `0009_merge_site_health_openai`; do not create another revision directly from either `0008` parent.

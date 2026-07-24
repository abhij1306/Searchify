# HANDOFF — v1: GSC/GA4/Bing integrations + Traffic + LLM Analytics

> Self-contained handoff for any agent picking this up from the GitHub branch alone.
> Everything referenced lives in this repo. The approved implementation plan is committed at
> [`docs/plans/v1-integrations-traffic-analytics.md`](v1-integrations-traffic-analytics.md)
> (summary: `…-summary.md`). Read `Agents.md` + `docs/invariants.md` first — all 12 invariants apply.

## 1. State of the work (2026-07-24)

**Branch:** `vorflux/integrations-traffic-analytics` → PR target `main`.
**All 34 approved-plan tasks are implemented and committed** (I1–I12 integrations backend, A1–A12 traffic+LLM-analytics backend, F1–F10 frontend), each with its tests green at commit time; `ruff`, `pnpm lint`, `pnpm build`, `pnpm run check:policy` all green.

**What was built (one paragraph):** first-party data foundations — OAuth integrations for Google Search Console + GA4 (one shared Google grant) and Bing Webmaster (Microsoft grant) with Fernet-encrypted tokens; a sync pipeline (Postgres SKIP-LOCKED queue worker + cadence dispatcher) producing immutable per-page import artifacts and derived `IntegrationMetricRow`s with full provenance + `resync_seq` re-syncs; a deterministic (no-LLM) AI-referral classifier/sanitizer with an `AnalyticsTask` queue chain (`ingest_referrals → classify_referrals → analytics_snapshot_refresh`, plus `traffic_snapshot_refresh` and a retention sweep); Traffic and LLM Analytics read APIs serving persisted projections only (inv. 7); and three frontend surfaces — Settings → Integrations (4th tab, OAuth-callback landing), `/traffic`, `/analytics` — wired into nav.

**Verification status:**
- Backend: all focused suites green per task. **A full-suite final gate must be re-run after the P0 fixes in §3** (previous run was environment-interrupted).
- Frontend: full suite green (681 tests), production build clean.
- API/system testing against a live seeded local stack: **Group A — 127/127 API assertions PASSED** (token hygiene, mapping 422/409 matrix, sync enqueue 202/409/422 + 480d clamp, traffic projection hand-math exact match, keyset pagination + cursor tamper/replay 400, cross-workspace 404 isolation, disconnect/revoke semantics incl. `pending_revocation`, traffic/sync 202 fan-out array excluding Bing, analytics `insufficient_data`, referrals pagination). Browser groups B–H are specified in §6 but NOT yet run.
- Post-build code review of the full diff: complete — **2 majors unfixed** (§3), 4 minors (§4).
- Simplify pass over the whole diff: applied (commit "Simplify: …" on this branch); any items it left pending are listed in §4.5.

## 2. Reproducing the dev/test stack (no external files needed)

1. Postgres 16: `cd infra/docker && cp .env.example .env 2>/dev/null; env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL docker compose up -d db` (the `env -u` guards invariant 11: shell-exported vars silently override the compose `.env`).
2. `backend/.env` per `backend/.env.example`, plus: `DATABASE_URL=postgresql+asyncpg://postgres:searchify_dev_password@localhost:5432/searchify`, any non-default `JWT_SECRET_KEY`/`ENCRYPTION_KEY`, a random `REFERRAL_HASH_SALT`, and dummy `INTEGRATION_GOOGLE_CLIENT_ID/SECRET` + `INTEGRATION_MICROSOFT_CLIENT_ID/SECRET` (real OAuth secrets are a deploy task — plan risk R4).
3. `cd backend && uv sync --extra dev && uv run alembic upgrade head` (greenfield policy: schema changes = edit models + `downgrade base && upgrade head`; never add revision files).
4. Backend: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers` (`--proxy-headers` matters: the OAuth redirect_uri derives from `request.base_url`, and the Next proxy forwards `x-forwarded-host` — without it the OAuth callback 302 points at :8000).
5. Frontend: `frontend/.env.local` with `BACKEND_ORIGIN=http://localhost:8000`; `pnpm install && pnpm dev` (:3000). Browser must use `localhost:3000` (same-origin `/api/*` proxy, inv. 12) — never :8000.
6. Seed a demo user/project via the API (register → personal workspace; create project with owned domains matching the integration property refs, e.g. `acme-running.example.com`).
7. **Stub provider for E2E/browser tests** (no real credentials exist): run a tiny HTTP stub on `127.0.0.1:9876` implementing: `GET /authorize` (302 auto-approve back to the app's redirect_uri with `code`+`state`, plus a deny mode), `POST /token` (access+refresh tokens), `POST /revoke` (200 or 500 for the failure path), GSC `searchAnalytics.query` + `sites.list`, GA4 `runReport`, Bing `GetSites/GetPageStats/GetQueryStats`. Patch the backend's config literals to point at the stub **before app import** (the authorize/token/revoke URLs + SSRF host allow-list + provider API base URLs are module-level `Final` dicts in `app/core/config/integrations.py` — monkeypatch them in a test-only launcher script that then starts uvicorn in-process; never commit such a patch). Seed grants/connections/mappings + metric rows directly in the DB (mirror `backend/tests/component/analytics_helpers.py` fixture shapes), then drive the real chain: call `enqueue_post_sync_projections()` + `AnalyticsWorker().run_until_idle()`. Keep seeded sync runs terminal (or pin `available_at` far-future) so live workers don't claim them; never run `audit_worker` against seeded demo audits.

## 3. P0 — review findings to FIX FIRST (2 majors)

### 3.1 MAJOR: re-sync of an already-projected window never re-triggers snapshot refresh
- **Where:** `backend/app/domain/analytics/enqueue.py` — `enqueue_traffic_snapshot_refresh` and `enqueue_analytics_snapshot_refresh`; `_enqueue_task` inserts `ON CONFLICT DO NOTHING` on the unique permanent `idempotency_key`.
- **Bug:** refresh idempotency keys are `(project_id, window_start, window_end)` only. After a re-sync at a bumped `resync_seq` (the I10 late-data-revision design, dispatcher re-ticks, or same-day "Sync now"), new artifacts/metric rows land but the refresh enqueue dedupes to `None` — `TrafficSnapshot`/`AnalyticsSnapshot` keep serving data built from the older `resync_seq` indefinitely. No test exercises a second sync of an already-projected window.
- **Fix:** thread the data revision into the refresh idempotency key (`…:<project>:<window_start>:<window_end>:<resync_seq>`), resolved from the triggering artifact's sync run inside `enqueue_post_sync_projections` and in the classify→snapshot chain link (`backend/app/domain/analytics/tasks.py` `run_classify_referrals`). Add a lifecycle test: second sync of a projected window ⇒ exactly one new refresh task; same-revision duplicate enqueue still dedupes.

### 3.2 MAJOR: GA4 property mappings are un-creatable via the public API
- **Where:** `backend/app/domain/integrations/mappings.py::create_mapping`; 422 mapping in `backend/app/api/integrations.py`.
- **Bug:** owned-domain write-time validation is applied uniformly to all providers, but a real GA4 `property_ref` is a numeric property id (`123456789` / `properties/123456789`); normalizing it can never equal an `OwnedDomain` ⇒ every GA4 mapping create 422s ⇒ every GA4 sync fails `unmapped_property` at derivation. Existing tests masked it by using domain-shaped GA4 refs. (Plan-level defect in I8 that was faithfully implemented.)
- **Fix:** scope the owned-domain rule to domain-shaped property refs (GSC `sc-domain:`/URL-prefix properties, Bing site URLs). For GA4, validate id shape only (non-empty, numeric/`properties/<id>`) and rely on the connection test for reachability. Update `backend/tests/component/test_integrations_mappings_api.py` to use a realistic numeric GA4 ref.

## 4. P1 — review minors

1. **GA4/Bing paging may terminate early when clients drop malformed rows** — `backend/app/workers/integration_worker.py` decides last-page on the *normalized* row count (rows where client normalization returns non-None). A full raw page with ≥1 dropped row looks like a short page ⇒ remaining rows silently skipped (same issue on the resume path). Terminate on the raw/provider row count (`start_row` vs provider `rowCount`), not the post-filter count. See `connectors/integrations/ga4.py` / `bing.py` row filtering.
2. **Partial fan-out invisible on 409** — `backend/app/api/traffic.py` `POST /projects/{id}/traffic/sync`: each `enqueue_sync_run` commits independently; if connection #1 enqueued before #2 raises `ActiveWindowConflictError`, the bare 409 hides that #1 will run. Include the already-enqueued connection ids in the 409 `detail` (standard FastAPI error body; no zod change needed).
3. **Referrals drill-down duplicates after a re-sync** — `backend/app/domain/analytics/service.py::get_llm_analytics_referrals` pages all classification+event rows with no revision filter; `ReferralEvent` dedupe is per `(import_id, content_hash)`, so a re-sync ingests a second copy of each logical referral. Filter to events whose `source_metric_row_id` is at the latest `resync_seq` per row identity (the snapshot builder already folds this way via `select_latest_referral_facts`).
4. **mypy `type: ignore` at `backend/app/connectors/integrations/oauth.py:147`** (`_coerce_expires_in`) — replace with a real coercion. (Repo gates don't run mypy; cosmetic.)

### 4.5 Simplify-pass status (applied vs pending)
A whole-diff simplify pass ran and its apply phase landed in two commits on this branch: `6c02ddf` (bulk of the work) + `ac57b52` (final file + two stale A6-era test assertions updated for the wired A8 snapshot executor). The simplify agent was cancelled before writing its final report, so the applied set is recorded here. Applied (verified in-tree): shared `connectors/integrations/_http.py` for provider HTTP; dead-knob removal in `core/config/integrations.py`; `domain/analytics/tasks.py` helper made public (`raise_if_task_terminal`) and payload-window/series consolidations in `domain/analytics/*` and `domain/traffic/*`; `domain/analytics/ingest.py` now calls the config-owned `unpack_dimension_key` instead of re-implementing the format; `workers/analytics_worker.py` not-wired stub inlined (dispatch table unchanged); frontend `lib/integrations/sync-runs.ts` now owns `SYNC_RUN_POLL_MS` + sync-run status helpers (old `lib/traffic/sync.ts` deleted), shared display formatters live in `lib/format.ts` (date/count/URL/granularity) with domain modules re-exporting, and shared table/chip building blocks extracted in `components/settings/integration-card.tsx` + `components/traffic/traffic-screen.tsx`. Gates after the apply pass: focused backend suites 285 passed / ruff clean; frontend vitest 101/101 on touched areas, `pnpm lint` + `pnpm build` clean. Pending (optional polish, not required): shared `TrendCard`/empty-state component extractions, `_get_project_or_404` consolidation into `app/api/deps.py`. Deliberately skipped by the simplify review: keeping both `countDomainMax` algorithms, no query-key factory restructure, no config-module splits.

### Do NOT re-flag (accepted deviations)
Microsoft revoke is local-only (no MS revoke endpoint exists); Bing scope `https://webmaster.bing.com/api/webmaster.manage` (no narrower scope documented; `bingads.manage` is the Ads API — rejected); GA4 aggregate rows persist `""` for UA/session identity; traffic stat-card deltas compare against the prior displayed bucket; referral pages are 50 rows; `IntegrationOAuthState` is an intentional 8th table beyond the spec's seven; queue specs order by `randomized_position` (not `created_at`).

## 5. P1 — final gates after fixes

```bash
cd backend  && uv run pytest tests/unit tests/component -q && uv run ruff check .
cd backend  && uv run alembic downgrade base && uv run alembic upgrade head
cd frontend && pnpm vitest run && pnpm lint && pnpm build && pnpm run check:policy
```

## 6. P2 — browser test groups (specified, not run)

Run against the §2 stack (backend via stub-patched launcher + stub provider on :9876, seeded data incl. a correlation-ok window AND an insufficient_data window, plus an empty second project). Use agent-browser or equivalent; record walkthroughs.
- **B — OAuth 302 through the proxy:** connect Google happy path (assert authorize URL carries both scopes + `redirect_uri` on `localhost:3000` + state) → lands on `/settings?tab=integrations&connected=gsc` with success notice + GSC+GA4 card; Bing happy path; provider-deny ⇒ `error=oauth_exchange_failed`; bogus/replayed state ⇒ `oauth_state_invalid`. (inv. 12: curl cannot reproduce the double-CORS failure mode — browser required.)
- **C — Settings → Integrations UI:** connected-idle, syncing (run badge + poll), disconnect dialog (shared-grant copy: last connection ⇒ "Disconnect & revoke"; sibling remains ⇒ grant stays alive), empty-first-run; Test + Sync-now actions; badge map connected/needs_reauth/pending_revocation/error/revoked (seed grant statuses directly).
- **D — `/traffic`:** 6 mono stat cards, count charts on `domainMax` vs CTR/position on 0–100, null → chart gaps, keyset paging + sort matches API order, day/week/month granularity, empty state CTA to `/settings?tab=integrations`, Sync now → poll → invalidation.
- **E — `/analytics`:** correlation card ok state AND `insufficient_data` (neutral badge + em-dash — never a fabricated coefficient), per-source donut, referrals drill-down (badges, source filter, keyset paging), themes rollup, empty state.
- **F — Sync E2E (recorded):** Sync now → runs succeed → C5 chain → refreshed projections visible in both screens; re-sync bumps `resync_seq` with old rows retained and no double-counting; dispatcher smoke (short cadence ⇒ one scheduled run per connection, second tick deduped) then stop the dispatcher.
- **G — Regression:** settings tabs 1–3 + deep-links, nav Analyze group + top-bar titles, visibility trends unchanged (TrendChart default domain 100).
- **H — Cross-cutting:** token-hygiene sweep (no token material in any response/log observed), deviation log vs the approved UI copy, walkthrough recordings.
Visual targets existed as approved mockups during the build (not committed); compare behavior and structure, not pixels.

## 7. P3 — PR finalization

The PR for this branch must contain: what/why summary; a detailed `## Testing` section (Group A 127/127 result + backend/frontend gate results + browser-group results once run); and a link to this handoff (`docs/plans/v1-integrations-handoff.md`) for the pending items. Do not merge until §3 P0 fixes land and §5 gates are green; §6 browser groups are the pre-merge evidence bar set by the plan (they need only the stub provider, not real credentials).

## 8. Contracts that must not drift

- **C1 dataset ids:** `gsc_page_daily`, `gsc_query_daily`, `ga4_channel_daily` (sessionDefaultChannelGroup,date), `ga4_source_medium_daily` (sessionSource,sessionMedium,date), `ga4_referrer_daily` (fullReferrer,date), `ga4_landing_daily` (landingPage,sessionSource,sessionMedium,date), `bing_page_daily`, `bing_query_daily`. `dimension_key` = declared-order dims joined `" | "` — the ONLY owners are `pack_dimension_key`/`unpack_dimension_key` in `backend/app/core/config/integrations.py` (never reimplement).
- **C2** OAuth callback 302: `/settings?tab=integrations&connected=<provider>` / `&error=<code>`.
- **C3** `POST /projects/{id}/traffic/sync` ⇒ **202 bare JSON array** of `{sync_run_id, connection_id, status}` (one per active mapped GSC/GA4 connection; Bing excluded).
- **C4** paged envelopes `{items, next_cursor}`; keyset helpers in `backend/app/domain/site_health/normalization.py` (`encode/decode_keyset_cursor`, `filter_fingerprint`, `CursorScopeError` → 400).
- **C5** post-sync chain: integration worker derivation → `enqueue_post_sync_projections(session, *, project_id, import_artifact_ids)` → per artifact `ingest_referrals` → (on completion) `classify_referrals` → `analytics_snapshot_refresh`; plus `traffic_snapshot_refresh` per affected window.
- **C6** backend DTOs are source of truth and match the frontend strict zod (`frontend/lib/api/schemas.ts`) exactly — change both sides or none.
- Sync enqueue entry point: `enqueue_sync_run(session, *, workspace_id, connection_id, sync_kind=..., window_start=None, window_end=None)` in `app/domain/integrations/sync.py` (commits internally; raises not-found 404 / window-invalid 422 / active-window-conflict 409). Default trailing window ends yesterday.
- Provider clients resolve via the `INTEGRATION_CLIENT_BUILDERS` lazy registry in config; all provider HTTP goes through injected-transport clients (never logged tokens; SSRF host allow-list in config).
- Two `ANALYZER_VERSION` constants exist — analytics code uses `app/core/config/analysis.py` ("b6-analysis-1") only, never `config/site_health.py`'s same-named one.
- Owned-domain mapping validation: exact host equality after `analysis/normalization.normalize_domain` (GSC `sc-domain:` prefix stripped via config literal) — see §3.2 for the GA4 fix.
- Microsoft grant: revoke is local-only; probe = real Bing `GetSites` via the pinned host `ssl.bing.com` (scope `https://webmaster.bing.com/api/webmaster.manage` + `offline_access`).
- Frontend OAuth = full-page 302 navigation via `lib/navigate.ts assignLocation(integrationsApi.oauthStartUrl(provider))` — never `apiClient`.

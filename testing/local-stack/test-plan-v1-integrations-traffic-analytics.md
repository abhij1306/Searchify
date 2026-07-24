# Test Plan — v1: Integrations (GSC/GA4/Bing) + Traffic + LLM Analytics

Repo: `<repo-root>`, branch `vorflux/integrations-traffic-analytics`.
Plan source: `docs/integrations-traffic-analytics.md` (Wave 6 seeded
local-stack browser pass). Mocks: 14 HTML design mockups (not committed — described per-state in the group C/D/E cases below).
Written: 2026-07-24 (planning stage; execution awaits orchestrator go-ahead —
A10/A11 landing in working tree, F6–F10 pending).

## Change Classification

| Field | Value |
|---|---|
| UI files changed | `frontend/components/settings/{integration-settings,integration-card,integrations-empty-state,settings-screen}.tsx`, `components/ui/trend-chart.tsx`, `lib/api/{integrations,traffic,analytics}.ts` + schemas/query-keys; PENDING: `app/(app)/traffic`, `app/(app)/analytics`, `components/traffic/*`, `components/analytics/*`, `nav-items.ts`, `lib/icons.ts`, `top-bar.tsx` |
| Backend files changed | `app/api/{integrations,analytics,traffic}.py`, `app/connectors/integrations/*`, `app/domain/{integrations,analytics,traffic}/*`, `app/models/{integrations,analytics,traffic}.py`, `app/workers/{integration_worker,integration_dispatcher,analytics_worker}.py`, `app/core/config/{integrations,analytics,traffic}.py`, `app/core/security.py`, `infra/docker/docker-compose.yml` |
| Proto/Schema files | none (SQLAlchemy models; greenfield alembic recreate) |
| Mobile app files | none |
| Has UI changes | YES |
| Has mobile app changes | NO |
| UI testing required | YES (agent-browser) |
| Mobile testing required | NO |
| Design mocks provided | YES — 14 HTML mockups in `/code/.plans/designs/` → pixel-fidelity comparison required |
| Change depth | FULL-FEATURE |

## Test environment (per testing/local-stack/setup-instructions.md — authoritative)

- Postgres 16: docker (`docker-db-1`, healthy, :5432). ALWAYS start compose with
  `env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL docker compose up -d db`.
- Backend :8000 — must be RESTARTED on the feature branch (currently old main).
- Frontend :3000 — `pnpm dev` already running; `.env.local` has `BACKEND_ORIGIN=http://localhost:8000`.
- Browser hits `localhost:3000` ONLY (same-origin `/api/:path*` proxy, inv. 12). NEVER :8000 from the browser.
- Seed: `bash testing/local-stack/seed.sh` → demo@searchify.dev / DemoPass123!, project "Acme Running Shoes"
  (owned domains `acme-running.example.com`, `blog.acme-running.example.com`; unintended `support.acme-running.example.com`).
- Auth = HttpOnly cookie (`searchify_session`); curl with `-c/-b cookies.txt`. Workspace = default (earliest-joined); `X-Workspace-Id` header optional.

## Key contract facts pinned from code (grounding for the cases below)

- OAuth: `GET /api/v1/integrations/oauth/{gsc|ga4|bing}/start` → 302 provider authorize; `/callback` → ALWAYS 302 → `/settings?tab=integrations&connected=<provider>` or `&error=oauth_state_invalid|oauth_not_configured|oauth_exchange_failed` (C2). Provider `error` param → `error=oauth_exchange_failed`.
- OAuth URLs are module-level `Final` dicts in `app/core/config/integrations.py` + `INTEGRATION_APPROVED_ENDPOINT_HOSTS` (SSRF allow-list). A stub launcher monkeypatches these BEFORE app import (test-only, never committed).
- OAuth client creds env: `INTEGRATION_GOOGLE_CLIENT_ID/SECRET`, `INTEGRATION_MICROSOFT_CLIENT_ID/SECRET` (empty ⇒ start → 503 `oauth_not_configured`). `REFERRAL_HASH_SALT` needed (dev warns on default).
- `_redirect_uri` = `request.base_url` + callback path ⇒ backend must run `uvicorn --proxy-headers` so the Next proxy's `x-forwarded-host` makes base_url `http://localhost:3000`.
- Google consent ⇒ grant + `gsc` AND `ga4` connections; Bing ⇒ `microsoft_oauth` grant + `bing` connection. Scopes: `webmasters.readonly` + `analytics.readonly` (Google); `offline_access` + `https://webmaster.bing.com/api/webmaster.manage` (Microsoft). Microsoft has NO revoke endpoint (local disconnect only).
- `GET /integrations` DTO keys (strict): id, workspace_id, grant_id, provider, label, account_ref, grant_status, granted_scopes, last_synced_at, created_at, updated_at — NO token fields.
- Derivation resolves mapping by `connection.account_ref` == mapping `property_ref`; mapping property must normalize (`sc-domain:` strip / scheme+www strip) to a project OwnedDomain else 422.
- Seed property refs: GSC `sc-domain:acme-running.example.com`; GA4 `https://acme-running.example.com/`; Bing `https://acme-running.example.com`.
- Sync: `POST /integrations/{id}/sync` 202 `{sync_run_id, connection_id, status}`; 409 `sync_active_window_conflict`; 422 `sync_window_invalid`. `POST /projects/{id}/traffic/sync` 202 ARRAY, one entry per ACTIVE mapped GSC/GA4 connection (Bing excluded ⇒ expect exactly 2).
- Datasets (C1): gsc_page_daily(page,date), gsc_query_daily(query,date), ga4_channel_daily(sessionDefaultChannelGroup,date), ga4_source_medium_daily(sessionSource,sessionMedium,date), ga4_referrer_daily(fullReferrer,date), ga4_landing_daily(landingPage,sessionSource,sessionMedium,date); `dimension_key` = values joined with " | " in declared order. Traffic consumes all EXCEPT ga4_referrer_daily (owned by referral ingest).
- Traffic GA4 inclusion: channel ∈ {"Organic Search"} OR classifier match on source/medium.
- Sort whitelists: pages {impressions,clicks,ctr,position,sessions,conversions}; queries {impressions,clicks,ctr,position}; default `-impressions`; page size 50 (+1 lookahead); cursor scope errors/tamper ⇒ 400; bad sort/window/granularity ⇒ 422.
- Analytics: CORRELATION_MIN_SAMPLE=8 aligned day-buckets else `{"state":"insufficient_data","coefficient":null}`; referrals page size 50; bad cursor ⇒ 400; bad source/window ⇒ 422. `ai_source` vocab: chatgpt|gemini|claude|perplexity|copilot|google_ai_overview|other. Host rules: chatgpt.com, chat.openai.com, gemini.google.com, claude.ai, perplexity.ai, copilot.microsoft.com (+UTM +UA tiers).
- Windows: traffic default 28d, max 480d; granularities day|week|month; reads serve PERSISTED snapshots only (absent ⇒ empty payload 200, never recompute).
- C5 chain: `enqueue_post_sync_projections(session, project_id=, import_artifact_ids=)` → per artifact `ingest_referrals` → `classify_referrals` → `analytics_snapshot_refresh`; + `traffic_snapshot_refresh` per distinct window. `AnalyticsWorker.run_until_idle()` drains (no network I/O in any analytics kind).
- Helpers: `backend/tests/component/analytics_helpers.py` (seed_ga4_import, seed_metric_row, seed_visibility_snapshot, seed_theme_analysis) — reuse patterns for the seed script; sync runs seeded TERMINAL (`succeeded`).
- Visibility series source: persisted `MetricSnapshot` rows (dashboard statuses) keyed by day ⇒ ok-correlation needs ≥8 MetricSnapshots on ≥8 distinct days inside the window.
- Grant badge map: connected→success, needs_reauth/pending_revocation→warning, error→danger, revoked→neutral.

## Worker / data safety (MANDATORY during execution)

- NEVER start `app.workers.audit_worker` (fake BYOK keys; would corrupt seeded running audit — tasks are available_at-pinned, but don't risk it).
- `integration_dispatcher`: STOPPED except its one controlled smoke test (it would enqueue scheduled syncs and retry pending_revocation revokes, destroying seeded UI states).
- Seeded `IntegrationSyncRun` rows: TERMINAL status only. For UI "syncing/queued" screenshots: pin `available_at`/`lease_expires_at` to ~2099 (seed_audits.py pattern) or stop the integration worker.
- `integration_worker`: run ONLY stub-patched, and only for the sync E2E steps.
- `analytics_worker`: no network I/O; run on demand (`run_until_idle`) for seeding, background for the sync E2E.
- Stub tokens are literal dummies (`stub-access-token` etc.); never print real secret VALUES from any .env (names only).

## Test Setup Plan (exact commands, unconditional)

S1. Postgres: `cd <repo-root>/infra/docker && env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL docker compose up -d db` (idempotent; already healthy).
S2. Ensure backend/.env has (append if absent; never echo values): `REFERRAL_HASH_SALT=<random 64>`, `INTEGRATION_GOOGLE_CLIENT_ID=stub-google-client-id`, `INTEGRATION_GOOGLE_CLIENT_SECRET=stub-google-client-secret`, `INTEGRATION_MICROSOFT_CLIENT_ID=stub-microsoft-client-id`, `INTEGRATION_MICROSOFT_CLIENT_SECRET=stub-microsoft-client-secret`.
S3. Stop old backend; `cd backend && uv sync && uv run alembic downgrade base && uv run alembic upgrade head` (greenfield recreate — WIPES DB; per plan A12).
S4. Start stub provider `testing/local-stack/stub_provider.py` (committed) (127.0.0.1:9876):
    - `GET /authorize` → 302 `{redirect_uri}?code=stub-code&state={state}`; `&mode=deny` → 302 `{redirect_uri}?error=access_denied&state=...`.
    - `POST /token` → 200 `{access_token:"stub-access-token",refresh_token:"stub-refresh-token",expires_in:3600,scope,token_type:"Bearer"}`; `mode=fail` → 400.
    - `POST /revoke` → 200 (toggleable 400 for pending_revocation).
    - GSC: `POST /webmasters/v3/sites/{ref}/searchAnalytics/query` (paged fixture rows incl. >50 pages/>50 queries for keyset tests), `GET /webmasters/v3/sites` (probe).
    - GA4: `POST /v1beta/properties/{ref}:runReport` (per-dimension fixture rows: channel/source_medium/referrer/landing).
    - Bing: `GET /webmaster/api.svc/json/{GetSites,GetPageStats,GetQueryStats}`.
    - Serves rows modeled on `backend/tests/fixtures/integrations/*.json`; request log endpoint `/__log` for assertions (e.g. revoke called).
S5. Stub launcher `testing/local-stack/stub_launcher.py` (committed): patches `app.core.config.integrations` Finals (authorize/token/revoke URLs → `http://127.0.0.1:9876/...`; add `127.0.0.1` to `INTEGRATION_APPROVED_ENDPOINT_HOSTS`; `GSC_API_BASE_URL`/`GA4_API_BASE_URL`/`BING_API_BASE_URL` → stub) BEFORE importing app, then runs uvicorn or a worker module in-process.
S6. Start stub (background, `wait_for_port=9876`); start backend via launcher: `uv run python testing/local-stack/stub_launcher.py api` (== uvicorn `--host 0.0.0.0 --port 8000 --proxy-headers`).
S7. Re-seed: `bash testing/local-stack/seed.sh`.
S8. Verify proxy+redirect wiring BEFORE browser: `curl -s -c cookies.txt -X POST localhost:3000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"email":"demo@searchify.dev","password":"DemoPass123!"}'`; `curl -sI -b cookies.txt "localhost:3000/api/v1/integrations/oauth/gsc/start"` ⇒ 302, `Location: http://127.0.0.1:9876/authorize?...redirect_uri=http%3A//localhost%3A3000/api/v1/integrations/oauth/gsc/callback...` (assert redirect_uri origin is localhost:3000 — proves `--proxy-headers` works).
S9. Seed integration graph: `testing/local-stack/seed_integrations.py` (committed) (direct ORM; patterns from `seed_audits.py` + `analytics_helpers.py`):
    - Google grant (transport google_oauth, status connected, Fernet-encrypted dummy tokens via `encrypt_secret`, granted_scopes both) + GSC conn (`account_ref=sc-domain:acme-running.example.com`) + GA4 conn (`account_ref=https://acme-running.example.com/`).
    - Microsoft grant + Bing conn (`account_ref=https://acme-running.example.com`).
    - 3 ACTIVE mappings (one per connection) → Acme project.
    - Terminal sync runs + immutable artifacts + `IntegrationMetricRow`s: 14 consecutive days ending 2026-07-21; gsc pages (>50 distinct page URLs for keyset paging) + queries (>50); ga4 channel ("Organic Search" + "Referral" + "Paid Search" non-included control), source/medium (`google | organic`, `chatgpt.com | referral`, `gemini.google.com | referral`, `bing | organic` control), landing, referrer (`https://chatgpt.com/`, `https://gemini.google.com/app`, `https://claude.ai/`, `https://news.ycombinator.com/` non-AI control).
    - 10 Audits + `MetricSnapshot`s on 10 distinct days in the full window (per_engine rates) + theme analyses (seed_theme_analysis pattern) for the themes rollup.
    - Windows: W_FULL = 2026-07-08..2026-07-21 (≥8 aligned days ⇒ correlation ok); W_SHORT = 2026-07-19..2026-07-21 (<8 ⇒ insufficient_data).
    - Second project "Empty Co" (same workspace): NO connections/mappings/rows (empty states).
    - Grant status fixtures (DB flips for badge screenshots): one needs_reauth + one pending_revocation grant WITH a live connection (seeded, restored after).
    - Then drive the REAL chain: `enqueue_post_sync_projections()` for all artifact ids + `AnalyticsWorker.run_until_idle()` (covers W_FULL + W_SHORT, all granularities).
S10. Sanity-verify projections via API (A-group smoke) before any browser step.
S11. Browser: `test -f /var/tmp/browser-state.json && npx agent-browser state load /var/tmp/browser-state.json` else login via UI once + `state save`. Viewport 1920×1080. Evidence under `/code/.generated_artifacts/{images,recordings}/`.

## Unit Test Audit (precondition — main agent owns these; verify at execution)

Backend (exist in diff): unit `test_integrations_config`, `test_oauth_state`, `test_integrations_oauth`, `test_analytics_config`, `test_referral_classification`, `test_referral_sanitize`, `test_traffic_projection`, `test_analytics_snapshot`; component `test_integrations_{models,oauth_api,api,sync_api,mappings_api}`, `test_integration_{queue,sync_enqueue,worker,derivation,dispatcher,ga4,bing}`, `test_analytics_{queue,snapshot}`, `test_{referral_ingest,classify_referrals_worker,referral_retention,post_sync_chain,traffic_models,traffic_refresh,llm_analytics_api}`; PENDING (in flight): `test_traffic_api`, `test_traffic_sync_api`. Frontend: `lib/api/{integrations,traffic,analytics}.test.ts` (incl. token-leak strict case), `components/settings/integration-settings.test.tsx`, `settings-screen.test.tsx`, `ui/primitives.test.tsx`; PENDING: traffic/analytics screen tests.
Execution gate: `cd backend && uv run pytest tests/unit tests/component -q && uv run ruff check .`; `cd frontend && pnpm test && pnpm lint && pnpm build && pnpm run check:policy`. Any miss = blocking issue back to main agent.

## Test Cases

### Group A — API verification (curl through :3000 proxy, cookie auth; method: API)

A1. **Login + workspace sanity** — POST /api/v1/auth/login (demo creds) ⇒ 200 + `searchify_session` cookie; GET /auth/me ⇒ 200. Catches: env/migration regressions blocking everything.
A2. **GET /integrations shape + token hygiene** — ⇒ 200 array; each item has EXACTLY the 11 strict DTO keys; response body contains no `token|secret|access|refresh` (case-insensitive grep). Catches: token leak (inv. 6), DTO drift vs zod strict schema (frontend would hard-fail).
A3. **POST /integrations/{id}/test** — stub probe ok ⇒ `{status:"ok"|"success", error_code:""}` (pin exact tokens at execution); stub toggled 401 ⇒ failed + `error_code=grant_auth_failed`; unknown id ⇒ 404. Catches: probe error mapping, needs_reauth surfacing.
A4. **POST /integrations/{id}/sync validation** — no body ⇒ 202 `{sync_run_id, connection_id, status:"queued"}`; immediate duplicate same window ⇒ 409 `sync_active_window_conflict`; only `window_start` ⇒ 422; `window_start>window_end` ⇒ 422 `sync_window_invalid`; window > backfill clamp ⇒ clamped or 422 per service (pin at execution). Catches: partial-index conflict mapping, window validation.
A5. **Sync history/detail** — GET /integrations/{id}/syncs ⇒ list contains the run; GET .../syncs/{id} ⇒ full projection (status transitions queued→succeeded once worker drains; row_count>0; resync_seq; completed_at set). Unknown sync id ⇒ 404. Catches: projection correctness, row_count aggregation.
A6. **Property mappings CRUD + validation** — POST (gsc, `sc-domain:acme-running.example.com`) ⇒ 201; duplicate ACTIVE ⇒ 409; provider mismatch (`ga4` on gsc conn) ⇒ 422; unowned property (`sc-domain:evil.example.com`) ⇒ 422; URL-form ref `https://www.acme-running.example.com/blog` on second project-owned host ⇒ 201 (normalization); GET list ⇒ rows; DELETE ⇒ 204 + re-GET shows `disabled`; re-create after disable ⇒ 201 (slot freed). Catches: owned-domain resolution, partial-index conflict, status-flip semantics.
A7. **Cross-workspace isolation (inv. 5)** — register user2 (own workspace); with user2 cookie: GET /integrations ⇒ lacks user1 connections; GET/DELETE user1's connection/sync/mapping ids ⇒ 404 (never 403/200/data). Catches: workspace-scoping holes on every new route.
A8. **Disconnect shared-grant semantics** — DELETE gsc connection ⇒ 204; GET /integrations ⇒ ga4 remains + grant still `connected` (no revoke call in stub log). DELETE ga4 (last on grant) ⇒ 204; stub `/__log` shows `/revoke` called; GET /integrations ⇒ no Google connections. Reconnect via OAuth (B1) to restore for later groups. Catches: revoke-on-last-only, token drop, event append.
A9. **Revoke-failure ⇒ pending_revocation** — stub revoke 400; delete last connection ⇒ 204; DB check grant status `pending_revocation` + tokens retained (no API exposes grant w/o connections). Restore stub 200. Catches: failure-path grant lifecycle.
A10. **GET /projects/{id}/traffic reads** — no params ⇒ latest persisted snapshot (window = seeded); exact from/to/granularity=day ⇒ totals == hand-computed seeded sums (impressions/clicks; ctr/position derived; sessions/conversions only from included GA4 rows — "Paid Search" excluded); null buckets stay null; `granularity=year` ⇒ 422; from>to ⇒ 422; window >480d ⇒ 422; random uuid ⇒ 404; user2 ⇒ 404; "Empty Co" ⇒ 200 empty payload (zeroed/null totals, empty series). Catches: inclusion-rule leaks, snapshot window matching, validation mapping.
A11. **Traffic pages/queries keyset** — GET pages ⇒ 50 items + `next_cursor`, default `-impressions` order verified against DB; page 2 via cursor (no overlap); `sort=clicks` asc; `sort=bogus` ⇒ 422; cursor replay with changed sort ⇒ 400; tampered cursor ⇒ 400. Same for queries (whitelist has no sessions/conversions ⇒ `sort=sessions` ⇒ 422). Catches: keyset scope binding, whitelist enforcement, lookahead off-by-one.
A12. **POST /projects/{id}/traffic/sync fan-out (C3)** — ⇒ 202 ARRAY of exactly 2 `{sync_run_id, connection_id, status}` (gsc + ga4; NEVER bing); poll both ⇒ succeeded; immediate repeat while active ⇒ 409. Empty project (no mapped connections) ⇒ 202 `[]` or 404/422 (pin contract at execution from `test_traffic_sync_api.py`). Catches: provider fan-out vocabulary, pass-through wiring.
A13. **GET /projects/{id}/llm-analytics** — default ⇒ latest snapshot; W_FULL ⇒ `correlation.state="ok"`, coefficient ∈ [-1,1], `sample_size>=8`; sources incl. chatgpt+gemini sessions>0 + non-AI excluded from AI volume; engine_visibility series present per seeded engine; W_SHORT window ⇒ `state="insufficient_data"`, `coefficient=null`, `sample_size<8`; bad granularity ⇒ 422; user2 ⇒ 404. Catches: fabricated-correlation bug (must be null, never invented), breakdown math.
A14. **Referrals drill-down keyset + filter** — GET referrals ⇒ 50/page + next_cursor, newest-first; `?source=chatgpt` ⇒ only chatgpt rows; non-AI row has `is_ai_referral=false, ai_source="other", confidence="exact"`, null logical_engine/match_signal; `source=bogus` ⇒ 422; cursor replay with changed source ⇒ 400. Catches: filter/scope binding, sanitize surfacing.
A15. **Themes rollup** — GET themes ⇒ rows grouped by frozen theme/intent with seeded rates (brand_mention_rate, share_of_voice nullability honest). Catches: rollup join correctness.
A16. **Unauthenticated** — no cookie ⇒ 401 on every new endpoint (integrations, traffic, llm-analytics). Catches: missing auth deps.

### Group B — OAuth 302 mechanics through the proxy (stub; method: browser + curl pre-verify; RECORD)

B1. **Connect Google happy path** — Settings→Integrations→Connect Google ⇒ full-page nav through :3000 proxy ⇒ lands on stub `/authorize` (assert URL query: client_id=stub id, redirect_uri=`http://localhost:3000/api/v1/integrations/oauth/gsc/callback`, BOTH scopes, state non-empty) ⇒ stub auto-approves ⇒ callback ⇒ final URL `/settings?tab=integrations&connected=gsc` + success Alert ("Google connected.") + Google card with GSC+GA4 sub-rows + scope chips. Catches: proxy redirect mangling (R5/inv. 12), redirect_uri origin bug (proxy-headers), state persist/consume, one-consent-two-connections.
B2. **Connect Bing** — same ⇒ `connected=bing` + Microsoft card + `offline_access`+webmaster scope in authorize URL. Catches: transport dispatch.
B3. **Provider-denied path** — stub mode=deny ⇒ callback `error=access_denied` ⇒ landing `error=oauth_exchange_failed` + error Alert. Catches: error-param mapping.
B4. **Bogus state replay** — browser to `/api/v1/integrations/oauth/gsc/callback?code=x&state=bogus` ⇒ landing `error=oauth_state_invalid`. Catches: state verification/consume-before-exchange.
B5. **Missing params** — callback with neither code nor state ⇒ `error=oauth_state_invalid`. Catches: param validation.

### Group C — Settings → Integrations UI (F5; method: browser; mock fidelity + visual review)

C1. **Connected & idle vs mockup** (`integrations-settings-connected-idle-dark.html`): per-grant cards (eyebrow, title, badge top-right, 20px pad), GSC+GA4 sub-rows, mono timestamps, scope chips, badge=success. Side-by-side screenshot comparison at 1920×1080 dark.
C2. **Empty first-run vs mockup** (`...-empty-first-run-dark.html`) on fresh user: empty-state card + both Connect CTAs.
C3. **Syncing vs mockup** (`...-syncing-dark.html`): queued run pinned (worker stopped) ⇒ run-status badge + Sync now disabled; grant-level error note after failed Test (stub 401).
C4. **Disconnect dialog vs mockup** (`...-disconnect-dialog-dark.html`): GSC dialog ⇒ "shared grant remains active" copy + plain "Disconnect"; Bing (last conn) ⇒ "Disconnect & revoke" + revoke warning copy; confirm each ⇒ list updates per A8. Screenshot dialog open.
C5. **Test + Sync now actions** — Test ⇒ inline result; Sync now ⇒ badge poll queued→running→succeeded (worker up) + row count + last_synced_at refresh; refetchInterval stops at terminal.
C6. **Callback notices** — `connected=gsc|bing` success tones; each error code ⇒ danger tone; params stripped after mount ⇒ refresh shows NO stale notice.
C7. **Grant badge states** — DB-seeded needs_reauth (warning), pending_revocation (warning + retry note), error (danger), revoked (neutral + "Reconnect to resume") ⇒ screenshot each.

### Group D — /traffic screen (F6/F7 — when landed; browser; mock fidelity)

D1. **Populated vs `analytics-dashboards-traffic-main.html`**: range chip + Day|Week|Month segmented control + Sync now w/ last-synced note; SIX mono stat cards (impressions/clicks/CTR/position/sessions/conversions); trend cards — counts on truthful domain (domainMax), CTR/position on 0–100 (lines sit low BY DESIGN); GSC late-data nulls ⇒ chart GAPS (not zeros); pages + queries tables dense 32/40px, mono numerals, `—` for nulls.
D2. **Empty vs `...-traffic-empty.html`** (Empty Co project) ⇒ empty state + link navigates to `/settings?tab=integrations`.
D3. **Syncing vs `...-traffic-syncing.html`**: Sync now spinner/disabled + info banner while run active.
D4. **Keyset paging + sort** — >50 seeded pages/queries: Next/Prev cursor stack, no row overlap/dup, boundary page; sortable columns toggle asc/desc and match API order; page-size exactly 50.
D5. **Granularity switch** — day|week|month refetches + re-renders from persisted snapshots (week/month buckets correct vs seeded math).
D6. **Range presets** — window swaps; unmeasured window ⇒ graceful empty payload (no crash, no fabricated zeros).
D7. **Visual quality review** — alignment, spacing, clipping/overflow, z-index of dropdown chips, contrast, table density, responsive 1440px + 1920px.

### Group E — /analytics screen (F8/F9 — when landed; browser; mock fidelity)

E1. **Populated vs `analytics-dashboards-llm-main.html`**: referral volume + share trends, per-source donut + legend, per-engine visibility series, correlation card OK (coefficient + sample size), themes table.
E2. **insufficient_data vs `...-llm-insufficient-data.html`** (W_SHORT): neutral badge + `—`, coefficient NEVER fabricated.
E3. **Empty vs `...-llm-empty.html`** (Empty Co).
E4. **Referrals drill-down**: columns (occurred_at mono, landing_url, referrer_host, ai_source badge, confidence, match_signal), `?source=` filter, keyset paging.
E5. **Granularity + range switching** per D5/D6.
E6. **Visual quality review** per D7.

### Group F — Sync-now → data appears E2E (stub; browser+API+DB; RECORD)

F1. With stub-patched integration worker + analytics worker running: Sync now on /traffic ⇒ 202 fan-out ⇒ UI polls ⇒ runs succeed ⇒ C5 chain fires (analytics tasks drained) ⇒ traffic + llm-analytics reads reflect refreshed snapshot; last_synced_at advances. DB: re-sync same window ⇒ NEW metric rows at `resync_seq+1`, old retained, reads serve latest-seq only (totals unchanged ⇒ no double-count). Catches: worker claim/commit-before-IO, derivation mapping, C5 hook, UI polling/invalidate.
F2. **Dispatcher smoke**: start `integration_dispatcher` with `INTEGRATION_SYNC_CADENCE_SECONDS=5` ⇒ one `scheduled` run per active connection appears; second tick ⇒ no duplicates (active-window dedupe); STOP dispatcher immediately. Catches: scheduler wiring; verifies it doesn't stampede.

### Group G — Regression spot-checks (only surfaces the diff touches)

G1. Settings tabs 1–3 render + deep-link switching incl. 4th tab (settings-screen.tsx touched by F5).
G2. Nav Analyze group shows Traffic + LLM Analytics with icons (F10); top-bar titles "Traffic"/"LLM Analytics".
G3. Visibility dashboard trends render unchanged (TrendChart default domainMax=100 preserved).

### Group H — Cross-cutting

H1. **Token hygiene sweep**: no token/secret string in ANY network response observed during browser runs (stub tokens are `stub-*` sentinels — grep HAR/notifications for them).
H2. **Mock fidelity log**: per-state deviation table (mock vs implementation) for all 8 implemented states; any spacing/typography/color/component-completeness mismatch = fail → back to main agent.
H3. **Walkthrough recordings**: one .webm per group B/C/D/E/F covering all changed flows + states.

## Execution-order notes
- A-group smoke BEFORE browser (S10). Destructive API cases (A8/A9) run AFTER C-group UI cases that need connected state; restore via B1/B2 reconnect.
- C3/D3 syncing states: stop integration worker + pin available_at=2099; resume after.
- F1 runs last (needs workers up); F2 after F1, then dispatcher stopped for good.

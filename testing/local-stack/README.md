# Local-stack test tooling — v1 Integrations + Traffic + LLM Analytics

Ready-made, runnable implementations of the test stack for the v1
integrations slice — see
[`docs/integrations-traffic-analytics.md`](../../docs/integrations-traffic-analytics.md)
(contracts, revision model, dev-stack gotchas). Nothing here is imported by
the app; everything is test-only tooling run by hand. No real secrets
anywhere — OAuth client creds and provider tokens are literal `stub-*`
dummies.

## Files

| File | Purpose |
|---|---|
| `stub_provider.py` | Standalone stub OAuth provider + GSC/GA4/Bing APIs on `127.0.0.1:9876`. OAuth `/authorize` (302 auto-consent, `?mode=deny`), `/token`, `/revoke`; GSC `searchAnalytics/query` + `sites` probe; GA4 `:runReport`; Bing `GetSites/GetPageStats/GetQueryStats`. Toggle failure modes + inspect calls via `POST /__mode`, `GET /__log`, `POST /__reset`. Stub provider rows are generated only for dates ≥ 2026-07-22 so a stub-driven sync never overlaps the seeded history (2026-07-08..2026-07-21). Run: `python3 testing/local-stack/stub_provider.py` (env `STUB_PORT`). |
| `stub_launcher.py` | Pre-import monkeypatch launcher. Points the integrations config Finals (OAuth URLs, SSRF host allow-list, GSC/GA4/Bing API bases) at the stub BEFORE app import, then runs a process in-process. Includes `_PublicHostMiddleware` so the OAuth `redirect_uri` reflects the public origin through the Next proxy (uvicorn ignores `x-forwarded-host`). Usage (from `backend/`): `uv run python ../testing/local-stack/stub_launcher.py api` (== uvicorn :8000 `--proxy-headers`), `integration-worker`, `analytics-worker`, `dispatcher`. |
| `seed.sh` + `seed_audits.py` | Base seed: demo user `demo@searchify.dev / DemoPass123!`, project "Acme Running Shoes" (owned domains `acme-running.example.com`, `blog.acme-running.example.com`; unintended `support.acme-running.example.com`), prompt set, fake BYOK provider connections, 4 audits across the lifecycle. Idempotent. Run: `bash testing/local-stack/seed.sh` (backend must be up). |
| `seed_integrations.py` | Integrations fixture graph: Google grant + GSC/GA4 connections, Microsoft grant + Bing connection, 3 ACTIVE mappings, terminal sync runs + artifacts, 14 days of metric rows (55 GSC pages + 55 queries/day for keyset paging, GA4 AI-referral sources, 2 Bing control days), 10 audits + MetricSnapshots on 10 distinct days (visibility axis for correlation), theme analyses, "Empty Co (no integrations)" project. Then drives the REAL C5 chain (`enqueue_post_sync_projections` + `AnalyticsWorker.run_until_idle()`) incl. explicit W_SHORT (2026-07-19..21) refreshes. Idempotent (skips if the Google grant exists). Run AFTER `seed.sh`, from `backend/`: `uv run python ../testing/local-stack/seed_integrations.py`. |
| `group_a_api_tests.py` | The Group A API harness — 127 assertions (token hygiene, mapping validation, sync windows, traffic exact-match math, keyset pagination + cursor tamper/replay, cross-workspace 404 isolation, disconnect/revoke semantics, traffic/sync fan-out, analytics `insufficient_data`, referrals). Passed 127/127 on this branch pre-PR. Seeded ids (project, empty project, gsc/ga4/bing connections) are resolved at runtime by name/provider — no hardcoded UUIDs. Run against the live stack from `backend/`: `uv run python ../testing/local-stack/group_a_api_tests.py`. Re-run after any backend contract change as the API regression gate. |
| `test-plan-v1-integrations-traffic-analytics.md` | The full test plan: contract facts pinned from code, worker/data-safety rules (never run `audit_worker` with fake BYOK keys; dispatcher stopped except its one smoke test; pin `available_at`≈2099 for "syncing" UI states), exact cases A1–A16 / B1–B5 / C1–C7 / D1–D7 / E1–E6 / F1–F2 / G1–G3 / H1–H3 with expected values, and execution-order constraints (destructive A8/A9 after the C-group; F1 last). Groups A–H all ran green on this branch (see the test report). |
| `setup-instructions.md`, `known-issues.md` | MVP-era base-stack docs kept for context (see header notes inside). `docs/integrations-traffic-analytics.md` + this README supersede them for this branch. |

## Bring-up order

```bash
# 1. Postgres (repo .env supplies credentials)
cd infra/docker && env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL docker compose up -d db

# 2. backend/.env: REFERRAL_HASH_SALT=<random 64>, INTEGRATION_GOOGLE_CLIENT_ID/SECRET=stub-*,
#    INTEGRATION_MICROSOFT_CLIENT_ID/SECRET=stub-*

# 3. Schema (greenfield recreate — wipes DB)
cd backend && uv sync --extra dev && uv run alembic downgrade base && uv run alembic upgrade head

# 4. Stub provider (background, port 9876)
python3 ../testing/local-stack/stub_provider.py

# 5. Backend via stub launcher (port 8000)
uv run python ../testing/local-stack/stub_launcher.py api

# 6. Frontend (port 3000; .env.local BACKEND_ORIGIN=http://localhost:8000)
cd frontend && pnpm install && pnpm dev

# 7. Seed
bash testing/local-stack/seed.sh
cd backend && uv run python ../testing/local-stack/seed_integrations.py

# 8. Group A API regression (expect 127/127)
uv run python ../testing/local-stack/group_a_api_tests.py
```

Then browser groups B–H per the test plan (browser only ever hits
`http://localhost:3000` — invariant 12; never :8000).

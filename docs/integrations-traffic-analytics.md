# Integrations + Traffic + LLM Analytics — dev reference

The durable dev details + gotchas for the v1 integrations slice (GSC/GA4/Bing
OAuth integrations → paged syncs → provenance-carrying metric rows → Traffic
and LLM Analytics snapshot projections). Subsystem overviews live in
[`backend-architecture.md`](backend-architecture.md) and
[`frontend-architecture.md`](frontend-architecture.md); the roadmap specs are
[`roadmap/integrations.md`](roadmap/integrations.md),
[`roadmap/traffic.md`](roadmap/traffic.md),
[`roadmap/llm-analytics.md`](roadmap/llm-analytics.md). This document is the
single successor of the retired v1 plan/handoff docs.

## Contracts that must not drift

- **C1 dataset ids:** `gsc_page_daily`, `gsc_query_daily`, `ga4_channel_daily`
  (sessionDefaultChannelGroup,date), `ga4_source_medium_daily`
  (sessionSource,sessionMedium,date), `ga4_referrer_daily` (fullReferrer,date),
  `ga4_landing_daily` (landingPage,sessionSource,sessionMedium,date),
  `bing_page_daily`, `bing_query_daily`. `dimension_key` = declared-order dims
  joined `" | "` — the ONLY owners are `pack_dimension_key`/
  `unpack_dimension_key` in `backend/app/core/config/integrations.py`.
- **C2** OAuth callback 302: `/settings?tab=integrations&connected=<provider>`
  / `&error=<code>`. Frontend connect = full-page navigation via
  `lib/navigate.ts assignLocation(integrationsApi.oauthStartUrl(provider))` —
  never `apiClient`.
- **C3** `POST /projects/{id}/traffic/sync` ⇒ **202 bare JSON array** of
  `{sync_run_id, connection_id, status}` (one per active mapped GSC/GA4
  connection; Bing excluded from Traffic).
- **C4** paged envelopes `{items, next_cursor}`; keyset helpers in
  `backend/app/domain/site_health/normalization.py` (`encode/decode_keyset_cursor`,
  `filter_fingerprint`, `CursorScopeError` → 400).
- **C5** post-sync chain: integration-worker derivation →
  `enqueue_post_sync_projections(session, *, project_id, import_artifact_ids)`
  → per artifact `ingest_referrals` → `classify_referrals` →
  `analytics_snapshot_refresh`; plus `traffic_snapshot_refresh` per affected
  window.
- **C6** backend DTOs are source of truth and match the frontend strict zod
  (`frontend/lib/api/schemas.ts`) exactly — change both sides or none.
- **409 shape (both sync endpoints):** on an active-window conflict,
  `POST /integrations/{id}/sync` and `POST /projects/{id}/traffic/sync` both
  return detail `{"error": "sync_active_window_conflict",
  "enqueued_connection_ids": [...]}` — the fan-out names the connections that
  DID enqueue before the conflict; the single-connection endpoint always
  returns the empty list.
- Sync enqueue entry point: `enqueue_sync_run(session, *, workspace_id,
  connection_id, sync_kind=..., window_start=None, window_end=None)`
  (`app/domain/integrations/sync.py`; commits internally; 404 / 422
  `sync_window_invalid` / 409). Default trailing window ends yesterday.
- Provider clients resolve via the `INTEGRATION_CLIENT_BUILDERS` lazy registry
  in config; all provider HTTP goes through injected-transport clients (tokens
  never logged; SSRF host allow-list in config).
- Two `ANALYZER_VERSION` constants exist — analytics code uses
  `app/core/config/analysis.py` ("b6-analysis-1") only, never the same-named
  one in `config/site_health.py`.
- Owned-domain mapping validation: exact host equality after
  `analysis/normalization.normalize_domain` (GSC `sc-domain:` prefix stripped
  via config literal). **GA4 is the exception** — see below.
- Microsoft grant: revoke is local-only; probe = real Bing `GetSites` via the
  pinned host `ssl.bing.com` (scope
  `https://webmaster.bing.com/api/webmaster.manage` + `offline_access`).

## Revision model (`resync_seq`) — the part easiest to break

- `IntegrationSyncRun.resync_seq` is allocated per **(connection, sync_kind,
  window)** by `_next_resync_seq`; a completed window re-syncs at seq+1, old
  revisions are retained (`uq_integration_metric_row_identity` includes
  `resync_seq`, so revisions coexist by design).
- Snapshot-refresh idempotency keys carry the revision:
  `analytics:<kind>:<project>:<window_start>:<window_end>:<resync_seq>`.
  Payloads stay window-only. This is what makes a re-sync of an
  already-projected window re-fire the refresh (a same-revision duplicate
  still dedupes). Do not drop the suffix.
- Reads serve the latest revision via
  `domain/analytics/ingest.py::metric_row_not_superseded()` (anti-join on the
  identity tuple + `resync_seq >`; the ONLY owner — ingest projection and
  referrals drill-down both apply it). **Scope limit:** because allocation is
  per (connection, sync_kind), disconnect→reconnect starts a NEW connection at
  seq 0 that does not supersede the old connection's rows (spec-sanctioned;
  `docs/roadmap/integrations.md`).
- Known transient: during a re-sync chain the referrals drill-down can briefly
  empty — seq-N metric rows commit before the seq-N ingest/classify chain
  lands, and superseded events are hidden immediately. Self-heals when the
  chain completes.
- Paging termination uses the provider's RAW row count
  (`page.raw_row_count`), never the post-normalization count — a full raw page
  with dropped malformed rows is NOT a last page (same rule on
  `_dataset_resume`; artifacts persist the raw `row_count`). Artifacts written
  before this change stored normalized counts; a mid-flight resume across that
  deploy can still terminate early (self-heals on the next re-sync).

## GA4 property refs — canonical form

GA4 property refs are NUMERIC ids; Google's account listing returns the
resource-name spelling `properties/123`. The canonical stored form is the
**bare numeric id**:

- `is_ga4_property_ref` validates shape (`^(?:properties/)?\d+$`);
  `normalize_ga4_property_ref` strips the prefix (both in
  `app/core/config/integrations.py`).
- `create_mapping` validates shape (NOT the owned-domain rule — a numeric id
  can never equal an `OwnedDomain`) and persists the canonical form, so the
  two spellings share one active-owner slot.
- `resolve_active_mapping` normalizes the incoming `account_ref` before the
  owner lookup; `build_metric_row_values` copies `mapping.property_ref`, so
  metric rows are canonical automatically.
- `Ga4Client` normalizes before building `…/v1beta/properties/{id}:runReport`
  (a raw prefixed ref would produce `properties/properties%2F…` → Google 400).
  The stub provider matches any `:runReport` path, so this bug class is
  invisible against the stub — the component test
  `test_prefixed_account_ref_normalizes_to_canonical_ref_and_url` pins it.
- Nothing in app code writes `account_ref` yet (OAuth-attached connections
  default it to `""` until the account-selection flow lands); seeds/tests set
  it directly. Keep the connection's `account_ref` in the provider's spelling
  and everything else canonical.

## Frontend analytics ranges

`/analytics` owns its preset vocabulary in `lib/analytics/options.ts`
(mirroring the traffic surface): default `latest` sends NO window bounds
(backend serves the freshest persisted snapshot); bounded presets resolve via
`rangeToWindow` to `from`+`to` UTC **calendar dates** (the API binds dates
both-or-neither). Do NOT reuse the visibility trend's `rangeToFrom` — its
from-only ISO datetime is for run-timestamp filtering and 422s here. A
bounded preset with no matching persisted window renders an honest info
alert, never the empty state or a recompute.

## Dev/test stack

Runnable tooling (stub provider, pre-import config-patch launcher, seeds,
127-assertion Group A harness, full A–H browser test plan):
[`testing/local-stack/`](../testing/local-stack/README.md) — follow its
README bring-up order. Gotchas that bite every session:

- **Shell profile exports EMPTY `DATABASE_URL`/`POSTGRES_*`** — prefix every
  backend/alembic/pytest/seed command with
  `env -u DATABASE_URL -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_DB`
  (invariant 11). `uv` lives at `~/.local/bin/uv`.
- On this dev machine the app/alembic/pytest/seeds all reach the **host
  Postgres at 127.0.0.1:5432** (root `.env` creds) — the docker `db`
  container publishes no ports. The pytest suite self-provisions a throwaway
  `searchify_tests_<runid>` DB.
- Recreate a clean DB:
  `psql <root .env creds> -c "DROP DATABASE IF EXISTS searchify WITH (FORCE);" -c "CREATE DATABASE searchify;"`
  then `uv run alembic upgrade head` (greenfield policy: edit models +
  recreate; never add revision files), `bash testing/local-stack/seed.sh`
  (needs the :3000 proxy up), then
  `cd backend && … uv run python ../testing/local-stack/seed_integrations.py`.
- The browser must use `localhost:3000` (same-origin `/api/*` proxy,
  invariant 12) — never :8000. OAuth `redirect_uri` derives from the
  forwarded host (`stub_launcher.py`'s `_PublicHostMiddleware`; production
  front proxies preserve the public Host — uvicorn `--proxy-headers`).
- Worker safety: never run `audit_worker` against seeded demo audits (FAKE
  BYOK keys ⇒ real terminal failures); keep the integration dispatcher
  stopped except its one smoke test; pin `available_at`≈2099 for
  "syncing" UI states; keep seeded sync runs terminal.

## Verify commands (focused)

```bash
cd backend && env -u DATABASE_URL -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_DB \
  uv run pytest tests/unit tests/component -q      # full: ~12.5 min
cd backend && … uv run ruff check .
cd backend && … uv run alembic upgrade head
cd backend && … uv run python ../testing/local-stack/group_a_api_tests.py  # 127 assertions, needs live stack
cd frontend && pnpm vitest run && pnpm lint && pnpm run check:policy && pnpm build
```

## Accepted deviations / known limitations

- `POST /projects/{id}/traffic/sync`'s 409 dict detail: the frontend
  `readErrorBody` falls back to `JSON.stringify(payload)` for non-string
  details — accepted; no zod change.
- Cross-connection supersession gap (see Revision model above).
- Bing single-shot page with `raw_row_count >= page_size` triggers one extra
  short-circuit request + a spurious empty artifact (benign; derivation
  no-op).
- `_coerce_expires_in`: JSON `true` coerces to 1s (bool is an int subclass) —
  behavior-preserving from the original `int()` path.
- Public-preview OAuth connect dead-ends at the sandbox-local stub redirect
  for remote viewers (stub limitation only).
- Seeded BYOK keys are placeholders; real audit runs correctly terminal-fail
  (`testing/local-stack/known-issues.md`).

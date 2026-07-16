# Roadmap — GSC / GA4 / Bing integrations

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

Connect a workspace's **Google Search Console (GSC)**, **Google Analytics 4 (GA4)**, and **Bing
Webmaster Tools** accounts via OAuth and ingest their search/traffic data on a schedule, so the
roadmap **Traffic** and **LLM Analytics / AI-referral** surfaces have real first-party data to
render. This is the Release 1.2 line item from the master plan
([`../../cube27-aeo-visibility-mvp-architecture-plan-v2.md`](../../cube27-aeo-visibility-mvp-architecture-plan-v2.md)
§20): *Google Search Console; Google Analytics 4; AI referral classification; conversion
correlation; owned-page opportunity mapping.*

Positioning constraints:
- **Official APIs only.** No scraping, no headless-browser data lifts — if a provider has no
  sanctioned API for a metric, that metric is out of scope (non-goal §10).
- **A data source, not a product surface.** Integrations feed other surfaces; the consumers are
  [`traffic.md`](traffic.md) (impressions/clicks/CTR/position, sessions, conversions) and
  [`llm-analytics.md`](llm-analytics.md) (AI-referral classification from GA4 referrer/landing
  data). This spec owns *connection + sync + immutable import artifacts*; it does **not** own
  the dashboards that read them.
- **BYO-account, tenant-isolated.** Each connection belongs to exactly one workspace; tokens
  never cross workspace boundaries and never leave the server (invariant 5, invariant 6).

This surface deliberately **reuses the BYOK crypto + Postgres-queue + immutable-artifact
machinery already coded for the audit slice** rather than inventing a parallel stack (invariant
2). An `IntegrationConnection` is the OAuth-token analog of `ProviderConnection`; a sync task is
the analog of an `AuditTask`; an import artifact is the analog of a `RawResponseArtifact`.

## 2. OAuth connection flow (tokens Fernet-encrypted, never returned)

Google (GSC + GA4 share Google OAuth) and Microsoft (Bing) both use the authorization-code
flow with refresh tokens. The flow is entirely **server-side**; the browser never sees a token.

```
user clicks "Connect Google" in Settings → Integrations
  → backend builds the provider authorize URL (scopes from config) + a signed state nonce
  → GET /integrations/oauth/{provider}/start  → 302 to provider consent screen
  → provider redirects back to GET /integrations/oauth/{provider}/callback?code=&state=
  → backend exchanges code → {access_token, refresh_token, expiry, granted_scopes}
  → encrypt_secret(refresh_token) + encrypt_secret(access_token) → IntegrationConnection row
  → 302 back to Settings → Integrations (connection now "connected")
```

Rules:
- **Tokens are Fernet-encrypted at rest exactly like the BYOK key** — reuse
  `app/core/security.py` `encrypt_secret` / `decrypt_secret` (invariant 2 — do **not** add a
  second crypto helper; invariant 6 — encrypted at rest). The decrypted token is resolved from
  the `IntegrationConnection` **only** at sync time, never from env, and is **never** placed in
  a response DTO, a log line, or an import artifact (invariant 6). Authorization headers are
  redacted from logs.
- **Refresh handled server-side.** When the access token is near expiry, the sync worker
  exchanges the encrypted refresh token for a fresh access token and re-encrypts it in place.
  Token refresh is the one permitted mutation of the credential columns (it is a credential
  rotation, not a data artifact — see the single-writer note in §4).
- **Approved endpoints + scopes live in config** (invariant 1). The authorize/token/redirect
  URLs, the OAuth client id, and the minimal scope set per provider come from
  `app/core/config/integrations.py`; the OAuth **client secret** is an env-injected secret (a
  deployment credential, resolved via `Settings`, never checked in), not hard-coded.
- **State nonce** is signed + short-lived to prevent CSRF on the callback; it encodes the
  target `workspace_id` + `provider` so the callback lands the token on the right connection
  without trusting a client-supplied id (invariant 5).

## 3. Data model (new tables — UUID PKs, workspace-scoped)

Mirror the provider/audit shape: one **connection** (credentials) owns scheduled **sync runs**,
each producing immutable **import artifacts**, from which the consumer surfaces derive rows.

- **`IntegrationConnection`** — one connected account. `id`, `workspace_id`, `provider`
  (discriminator: `gsc | ga4 | bing`), `transport` (`google_oauth | microsoft_oauth` — the
  physical OAuth surface, kept distinct from the logical provider so GSC+GA4 can share one
  Google grant), `label`, `access_token_encrypted` (Fernet), `refresh_token_encrypted`
  (Fernet), `token_expires_at`, `granted_scopes` (JSONB), `account_ref` (the provider-side
  property/site id, e.g. GA4 property id or GSC site URL), `status`
  (`connected | needs_reauth | revoked | error`), `last_synced_at`, `created_at`, `updated_at`.
  The two `*_encrypted` columns are **never** serialized into any DTO (invariant 6), exactly
  like `ProviderConnection.api_key_encrypted`.
- **`IntegrationSyncRun`** — one sync execution. Reuse the queue-row contract from
  `models/audit.py` `AuditTask` (§10 of backend-architecture): `id`, `connection_id`,
  `workspace_id`, `sync_kind` (`scheduled | on_demand | backfill`), `window_start`,
  `window_end` (the date range requested), `status`
  (`queued | leased | running | succeeded | retry_wait | failed | cancelled`), `lease_owner`,
  `lease_expires_at`, `heartbeat_at`, `attempt_count`, `max_attempts`, `idempotency_key`
  (unique), unique `(connection_id, sync_kind, window_start, window_end)`,
  `available_at`, `error_code`, `error_detail`, timestamps. Claimed with `FOR UPDATE SKIP
  LOCKED` (invariant 8). No double-claim.
- **`IntegrationImportArtifact`** — the immutable, written-once (invariant 3) record of one
  fetched page/batch of provider data. `id`, `sync_run_id`, `connection_id`, `workspace_id`,
  `provider`, `dataset` (e.g. `gsc.searchAnalytics`, `ga4.runReport`, `bing.pageStats`),
  `query_snapshot` (JSONB: the exact API query parameters — dimensions, metrics, date range —
  but **never** any credential), `payload_hash`, `fetched_at`, `row_count`, and the raw payload.
  Large payloads follow the audit pattern: metadata in Postgres, the multi-MB body to
  S3-compatible object storage (roadmap) keyed by `payload_hash`; small payloads may be inline
  JSONB. Written once by the claiming worker; a re-sync produces a **new** artifact identity,
  never an overwrite (invariant 3).
- **`IntegrationMetricRow`** — the derived, normalized fact row the consumer surfaces read.
  `id`, `workspace_id`, `project_id`, `provider`, `date`, `dimension_key` (e.g. page URL, query,
  country, referrer), `metrics` (JSONB: impressions/clicks/ctr/position for GSC; sessions/
  conversions/engagement for GA4; etc.), `source_artifact_id` (**provenance**, invariant 4),
  `importer_version`. One row per (provider, date, dimension_key). A derived row with no
  traceable `source_artifact_id` + version is invalid (invariant 4).
- **`IntegrationEvent`** — append-only lifecycle/audit events (connect, test, sync start/finish,
  reauth, revoke), same shape as `AuditEvent`. Satisfies the master plan §15 requirement to
  *audit connection creation, testing, rotation, and deletion*.

**Single-writer** (invariant 3): the worker that claimed the `IntegrationSyncRun` is the sole
writer of that run's artifacts + derived rows. The only credential mutation allowed outside a
fresh row is the server-side token refresh (§2), which is a rotation of the connection's own
encrypted columns, not an edit of any artifact.

## 4. Sync tasks (Postgres queue, commit-before-I/O, immutable artifacts)

Sync runs execute on the **same Postgres `FOR UPDATE SKIP LOCKED` queue** as audits (invariant
8), through the `TaskQueue` Protocol (`app/orchestration/task_queue.py`) so no Redis is
required. A dedicated `app/workers/integration_worker.py` (a sibling of `audit_worker.py`, not a
fork) claims runs:

1. In one short transaction: select eligible `IntegrationSyncRun` rows in priority order, lock
   with `FOR UPDATE SKIP LOCKED`, set `leased` + `lease_owner` + `lease_expires_at`, return.
2. **Commit the claim before any network I/O** (invariant 8) — never hold a DB transaction open
   across a Google/Bing API call.
3. Resolve + decrypt the token, refresh it if near expiry, then page the provider API for the
   requested window; write each page as an immutable `IntegrationImportArtifact` (invariant 3).
4. Worker **heartbeats** to extend the lease during a long backfill; a **sweeper**
   (`release_expired`) returns expired leased/running runs to `retry_wait`, or `failed` after
   `max_attempts`.
5. After the raw import lands, derive normalized `IntegrationMetricRow`s (with
   `source_artifact_id` + `importer_version` provenance, invariant 4) — a projection step, never
   a second fetch.
6. Cancellation is **cooperative** (invariant 9): the worker stops at the page boundary.

**Scheduling.** Scheduled syncs are enqueued by a lightweight periodic dispatcher (the same
mechanism the roadmap "recurring audit schedules" uses — one owner, invariant 2) that inserts a
`scheduled` `IntegrationSyncRun` per active connection per cadence, deduped by the unique
`(connection_id, sync_kind, window_start, window_end)` constraint so a missed tick never
double-imports. On-demand syncs are enqueued by `POST /integrations/{id}/sync`.

**Idempotency + late data.** GSC/GA4 data is revised for ~2–3 days after the fact. Re-syncing a
recent window creates a **new** artifact + new derived rows keyed to a newer `importer_version`;
the consumer reads the latest version per (provider, date, dimension_key). Old artifacts are
retained (immutable), never overwritten (invariant 3).

## 5. API surface (roadmap; `/api/v1`)

All workspace-scoped via `require_active_workspace` / `require_workspace_member` (invariant 5).
No endpoint ever returns a token (invariant 6).

- `GET /integrations` — list this workspace's connections (status, provider, `account_ref`,
  `last_synced_at`, `granted_scopes`) — **tokens absent**.
- `GET /integrations/oauth/{provider}/start` — begin OAuth (302 to consent).
- `GET /integrations/oauth/{provider}/callback` — exchange code, persist encrypted tokens, 302
  back to Settings.
- `POST /integrations/{id}/test` — validate the stored token against the provider (a cheap
  authenticated probe, analogous to `POST /provider-connections/{id}/test`); returns a status +
  `error_code`, never the token. Appends an `IntegrationEvent`.
- `POST /integrations/{id}/sync` — enqueue an on-demand `IntegrationSyncRun` (body: optional
  date window); 202 + the run id. 409 if a run for the same window is already active.
- `GET /integrations/{id}/syncs` / `GET /integrations/{id}/syncs/{sync_id}` — sync-run history +
  detail projection (status, window, row counts) — projection only (invariant 7).
- `DELETE /integrations/{id}` — revoke at the provider (best-effort), mark `revoked`, and drop
  the encrypted tokens. Appends an `IntegrationEvent`.

The **data** the imports feed is served by the consumer surfaces' own endpoints (Traffic,
LLM-Analytics), which read `IntegrationMetricRow` as versioned persisted evidence (invariant 7)
— they never call GSC/GA4/Bing directly.

## 6. Frontend (roadmap)

- **Settings → Integrations** — the connection manager: per-provider cards (Connect / Reconnect
  / Test / Sync now / Disconnect), status badge, last-synced timestamp, granted scopes. Rendered
  **disabled ("soon")** today: the frontend route map
  ([`../frontend-architecture.md`](../frontend-architecture.md) §2) already lists *Settings →
  Integrations (GSC/GA4/Bing)* as Roadmap, and the sidebar renders roadmap items disabled.
- Add an `integrations.ts` API module + zod schemas in `frontend/lib/api/` (mirroring
  `providers.ts`), `queryKeys.integrations.*`, reusing the existing card/badge/table primitives.
  The connection schema **must not include a token field** — mirror
  `providerConnectionSchema`, where the secret is never present, and `strictValidate` will fail
  loud if the backend ever leaks one (invariant 6).
- OAuth start/callback are full-page navigations (302s), so they go through the same-origin
  `/api/*` proxy (invariant 12); no cross-origin browser call to the backend or the provider.

## 7. Config & tuning knobs (all in `app/core/config/integrations.py`)

Nothing tunable is hard-coded in service/worker code (invariant 1):
- Per-provider OAuth **authorize / token / revoke URLs**, redirect path, and the **minimal
  scope set** (GSC: `webmasters.readonly`; GA4: `analytics.readonly`; Bing: the webmaster read
  scope). The OAuth **client id/secret** are env-injected via `Settings` (deployment secrets),
  not literals in this file.
- `SYNC_DEFAULT_WINDOW_DAYS`, `SYNC_BACKFILL_MAX_DAYS`, `SYNC_CADENCE` (default daily),
  `SYNC_LATE_DATA_REVISION_DAYS` (re-sync recent window), `SYNC_PAGE_SIZE`,
  `SYNC_REQUEST_TIMEOUT_S`, `SYNC_MAX_ATTEMPTS`, per-provider rate-limit knobs.
- The **approved-endpoint allow-list** — reject arbitrary/private-network hosts (master plan
  §15 SSRF/endpoint policy).
- The **dataset → dimensions/metrics** query templates used to build `query_snapshot`.

## 8. Suggested build order

1. Config: `integrations.py` (endpoints, scopes, sync knobs) + migration for the 4–5 tables.
2. `IntegrationConnection` model + OAuth start/callback + token encryption (reusing
   `encrypt_secret`), with `/test`. Unit-test the callback + token round-trip against a fake
   OAuth server (no live provider in tests); assert **no token appears in any DTO or log**.
3. `IntegrationSyncRun` + `integration_worker.py` on the existing `PostgresTaskQueue`
   (claim/heartbeat/sweeper), writing immutable `IntegrationImportArtifact`s — one provider
   first (GSC), table-tested against recorded fixture payloads.
4. Derivation → `IntegrationMetricRow` (provenance + `importer_version`).
5. Scheduled dispatcher (dedup via the unique window constraint).
6. API routers + Settings → Integrations UI (flip the disabled nav item live for GSC).
7. GA4, then Bing, as additional providers behind the same contract.
8. Wire the consumer surfaces ([`traffic.md`](traffic.md), [`llm-analytics.md`](llm-analytics.md))
   to read the derived rows.

## 9. Cross-references

- **Consumers:** [`traffic.md`](traffic.md) (search/traffic dashboards), [`llm-analytics.md`](llm-analytics.md)
  (AI-referral classification from GA4 referrer/landing data).
- **Reused machinery:** BYOK crypto (`core/security.py`), the Postgres queue + `TaskQueue`
  Protocol (`orchestration/*`), immutable-artifact pattern (`models/audit.py`).
- **Product rationale:** master plan §20 Release 1.2; §15 security.

## 10. Explicit non-goals (MVP of this surface)

- **No scraping / no unofficial data.** Only sanctioned provider APIs; if there is no official
  API for a metric, it is out of scope.
- **Tokens never leave the server** and are never returned in a DTO or written to a log/artifact
  (invariant 6). No client-side token handling.
- **No new crypto.** Reuse `encrypt_secret`/`decrypt_secret` — do not add a second secret store
  (invariant 2).
- **No billing / no paid-tier gating** in this surface.
- **Integrations do not compute product metrics** — they import + normalize; the Traffic and
  LLM-Analytics surfaces own the dashboards and read persisted rows only (invariant 7).

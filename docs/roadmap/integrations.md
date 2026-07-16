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
([`../architecture.md`](../architecture.md)
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
2). An `IntegrationOAuthGrant` is the OAuth-token analog of `ProviderConnection` (it owns the
encrypted credentials), an `IntegrationConnection` binds a logical provider to that grant, a sync
task is the analog of an `AuditTask`, and an import artifact is the analog of a
`RawResponseArtifact`.

## 2. OAuth connection flow (tokens Fernet-encrypted, never returned)

Google (GSC + GA4 share Google OAuth) and Microsoft (Bing) both use the authorization-code
flow with refresh tokens. The flow is entirely **server-side**; the browser never sees a token.

```text
user clicks "Connect Google" in Settings → Integrations
  → backend builds the provider authorize URL (scopes from config) + a signed state nonce
  → GET /integrations/oauth/{provider}/start  → 302 to provider consent screen
  → provider redirects back to GET /integrations/oauth/{provider}/callback?code=&state=
  → backend exchanges code → {access_token, refresh_token, expiry, granted_scopes}
  → encrypt_secret(refresh_token) + encrypt_secret(access_token) → find-or-create the
      workspace's IntegrationOAuthGrant for this transport (tokens stored once, on the grant)
  → attach the logical IntegrationConnection(s) for the grant (e.g. GSC + GA4 for a Google grant)
  → 302 back to Settings → Integrations (connection now "connected")
```

Rules:
- **Tokens are Fernet-encrypted at rest exactly like the BYOK key** — reuse
  `app/core/security.py` `encrypt_secret` / `decrypt_secret` (invariant 2 — do **not** add a
  second crypto helper; invariant 6 — encrypted at rest). Tokens are stored **once, on the
  `IntegrationOAuthGrant`** (§3), never duplicated per connection. The decrypted token is
  resolved from the grant **only** at sync time, never from env, and is **never** placed in a
  response DTO, a log line, or an import artifact (invariant 6). Authorization headers are
  redacted from logs.
- **Refresh handled server-side, serialized per grant.** When the access token is near expiry,
  the sync worker exchanges the encrypted refresh token for a fresh access token and re-encrypts
  it **on the grant**. Because two connections (e.g. GSC + GA4) or two windows can run
  concurrently against the **same** `IntegrationOAuthGrant`, the refresh is made **atomic per
  grant** so a stale worker cannot clobber a token another worker just rotated: the worker takes
  a **row lock** on the grant (`SELECT ... FOR UPDATE`) — or an equivalent Postgres advisory
  lock / compare-and-swap on `(grant_id, token_expires_at)` — re-reads the current token inside
  the lock, and only performs the exchange + re-encrypt if the token is still the one it saw
  (skipping the call if a concurrent worker already refreshed). Token refresh is the one
  permitted mutation of the credential columns (a credential rotation, not a data artifact — see
  the single-writer note in §4), and it mutates only the grant, never a connection.
- **Approved endpoints + scopes live in config** (invariant 1). The authorize/token/redirect
  URLs, the OAuth client id, and the minimal scope set per provider come from
  `app/core/config/integrations.py`; the OAuth **client secret** is an env-injected secret (a
  deployment credential, resolved via `Settings`, never checked in), not hard-coded.
- **State nonce** is signed + short-lived to prevent CSRF on the callback and to prevent
  account-linking (a token being planted on a workspace/session the initiator does not
  control). It encodes the target `workspace_id` + `provider` (so the callback lands the token
  on the right connection without trusting a client-supplied id, invariant 5) **and** binds to
  the **initiating user/session** — the id of the member who started the flow plus a
  server-issued session/CSRF token. On the callback the backend, **before persisting any
  token**, (a) verifies the state signature and that it has not expired, (b) verifies the
  callback is being made under the **same authenticated session** the state was minted for
  (matching user + session/CSRF token), and (c) enforces **one-time consumption** — the state
  is marked consumed atomically and a replayed or already-consumed state is rejected. Only
  after all three checks pass does the code exchange run and the encrypted tokens land on the
  workspace's `IntegrationOAuthGrant` (find-or-create for the transport), with the logical
  `IntegrationConnection`(s) attached to it (invariant 5, invariant 6).

## 3. Data model (new tables — UUID PKs, workspace-scoped)

Mirror the provider/audit shape: one **grant** owns the credentials; each **connection** (a
logical provider bound to that grant) owns scheduled **sync runs**, each producing immutable
**import artifacts**, from which the consumer surfaces derive rows.

- **`IntegrationOAuthGrant`** — **the credential-owning entity: one row per OAuth grant (the
  consent a workspace gives one transport), which owns the encrypted tokens and the whole
  refresh/revoke lifecycle.** A single Google grant covers **both** GSC and GA4 (they share the
  Google OAuth surface), so credentials are stored **once** on the grant and never duplicated
  per logical provider. `id`, `workspace_id`, `transport` (`google_oauth | microsoft_oauth` —
  the physical OAuth surface), `access_token_encrypted` (Fernet), `refresh_token_encrypted`
  (Fernet), `token_expires_at`, `granted_scopes` (JSONB), `status`
  (`connected | needs_reauth | pending_revocation | revoked | error` — `pending_revocation`
  is the disconnect-requested-but-remote-revoke-not-yet-confirmed state from §5, in which the
  encrypted tokens are deliberately retained), `created_at`, `updated_at`. The two
  `*_encrypted` columns are **never** serialized into any DTO (invariant 6), exactly like
  `ProviderConnection.api_key_encrypted`. **Token refresh and revocation happen here, once per
  grant** (§2), so a shared Google grant has a single, consistent credential lifecycle rather
  than two connection rows racing to re-encrypt the same tokens.
- **`IntegrationConnection`** — one logical connected surface (a `provider` bound to a grant).
  `id`, `workspace_id`, `grant_id` (FK → `IntegrationOAuthGrant`, **same workspace**), `provider`
  (discriminator: `gsc | ga4 | bing`), `label`, `account_ref` (the provider-side property/site
  id, e.g. GA4 property id or GSC site URL), `last_synced_at`, `created_at`, `updated_at`. The
  connection carries **no credential columns** — tokens live solely on the parent
  `IntegrationOAuthGrant` (above). GSC and GA4 are two `IntegrationConnection` rows that point
  at the **same** `IntegrationOAuthGrant`, so one consent yields both surfaces while refresh and
  revoke stay consistent. A connection's `provider` transport must be compatible with its
  grant's `transport` (`gsc | ga4` → `google_oauth`, `bing` → `microsoft_oauth`).
- **`IntegrationSyncRun`** — one sync execution. Reuse the queue-row contract from
  `models/audit.py` `AuditTask` (§10 of backend-architecture): `id`, `connection_id`,
  `workspace_id`, `sync_kind` (`scheduled | on_demand | backfill`), `window_start`,
  `window_end` (the date range requested), `status`
  (`queued | leased | running | succeeded | retry_wait | failed | cancelled`), `lease_owner`,
  `lease_expires_at`, `heartbeat_at`, `attempt_count`, `max_attempts`, `idempotency_key`
  (unique), `resync_seq` (a monotonic per-window attempt/version counter — see below),
  `available_at`, `error_code`, `error_detail`, timestamps. Claimed with `FOR UPDATE SKIP
  LOCKED` (invariant 8). No double-claim.
  **Window uniqueness is scoped so re-syncing a completed window stays possible** (the
  documented late-data correction behaviour in §4): a *partial* unique index enforces
  `(connection_id, sync_kind, window_start, window_end)` **only over active rows**
  (`status in (queued, leased, running, retry_wait)`), which dedupes concurrent/duplicate
  in-flight runs for the same window, while a **completed** run (succeeded/failed/cancelled)
  leaves the window free to be re-synced. The full re-sync identity is
  `(connection_id, sync_kind, window_start, window_end, resync_seq)`: a new re-sync bumps
  `resync_seq`, producing a distinct run row (and, downstream, a new immutable
  `IntegrationImportArtifact` for late data — invariant 3) without overwriting the prior one.
  **`resync_seq` is allocated atomically** so two concurrent re-syncs of the same completed
  window cannot pick the same value or break monotonicity: the next value is computed as
  `MAX(resync_seq) + 1` for the `(connection_id, sync_kind, window_start, window_end)` group
  **under a row/advisory lock on that window group** (or via a unique-conflict insert that
  retries with the next value on collision), enforced by a **full** unique constraint on
  `(connection_id, sync_kind, window_start, window_end, resync_seq)`. The lock/CAS guarantees
  each re-sync receives a distinct, monotonically increasing `resync_seq` while the *active-only*
  partial index above still dedupes in-flight runs.
- **`IntegrationImportArtifact`** — the immutable, written-once (invariant 3) record of one
  fetched page/batch of provider data. `id`, `sync_run_id`, `connection_id`, `workspace_id`,
  `provider`, `dataset` (e.g. `gsc.searchAnalytics`, `ga4.runReport`, `bing.pageStats`),
  `query_snapshot` (JSONB: the exact API query parameters — dimensions, metrics, date range —
  but **never** any credential), `payload_hash`, `fetched_at`, `row_count`, and the raw payload.
  Large payloads follow the audit pattern: metadata in Postgres, the multi-MB body to
  S3-compatible object storage (roadmap) keyed by `payload_hash`; small payloads may be inline
  JSONB. Written once by the claiming worker; a re-sync produces a **new** artifact identity,
  never an overwrite (invariant 3).
- **`IntegrationPropertyMapping`** — the explicit, constrained bridge from a connection's
  provider-side property to an owning **project**. `IntegrationConnection` is only
  workspace-scoped and carries a generic `account_ref`, so `project_id` on a derived row cannot
  be inferred from the connection alone; this entity supplies it. `id`, `workspace_id`,
  `connection_id`, `provider`, `property_ref` (the provider property id — GSC site URL / GA4
  property id), `project_id` (the owning project, in the **same** workspace),
  `status` (`active | disabled`), `created_at`, `updated_at`. **One active owner per property
  across *all* connections**, not merely per connection: a **partial** unique index on
  `(workspace_id, provider, property_ref)` **restricted to `status = active`** guarantees that a
  single workspace property can be owned by at most one active mapping (and therefore derives to
  exactly one project), so two different OAuth connections cannot both claim the same property
  for different projects (invariant 2). The mapping's `provider` is **bound to its referenced
  connection's `provider`** (validated on write — a `gsc` mapping must reference a `gsc`
  connection, never a `ga4`/`bing` one), and a same-workspace FK on both `connection_id` and
  `project_id` ensures a property can never be mapped to a project in another workspace
  (invariant 5). Sync/derivation workers **must resolve this mapping to obtain `project_id`
  before writing any `IntegrationMetricRow`**; a property that is **unmapped or ambiguous** (no
  active mapping) is **rejected** — the run records an `error_code` (e.g. `unmapped_property`)
  rather than guessing a project.
- **`IntegrationMetricRow`** — the derived, normalized fact row the consumer surfaces read.
  `id`, `workspace_id`, `project_id`, `property_ref`, `provider`, `dataset`, `date`,
  `dimension_key` (e.g. page URL, query, country, referrer), `metrics` (JSONB: impressions/
  clicks/ctr/position for GSC; sessions/conversions/engagement for GA4; etc.),
  `source_artifact_id` (**provenance**, invariant 4), `resync_seq` (the source run's re-sync
  revision — see `IntegrationSyncRun`), `importer_version`. `project_id`/`property_ref` are
  resolved via `IntegrationPropertyMapping` (above), never from client input or a bare
  `account_ref`. **Row identity is scoped by project/property/dataset — one row per
  `(project_id, property_ref, provider, dataset, date, dimension_key, resync_seq)`** — so rows
  from different properties or projects that happen to share a `dimension_key` and `date` never
  collide. **"Latest version" is selected by the deterministic re-sync revision `resync_seq`**
  (the source run's data-run identity), **not** by `importer_version` (which versions the
  transform code, not the data run): consumers read the row with the highest `resync_seq` for a
  given `(project_id, property_ref, provider, dataset, date, dimension_key)`. A derived row with
  no traceable `source_artifact_id` + version is invalid (invariant 4).
- **`IntegrationEvent`** — append-only lifecycle/audit events (connect, test, sync start/finish,
  reauth, revoke), same shape as `AuditEvent`. Satisfies the master plan §15 requirement to
  *audit connection creation, testing, rotation, and deletion*.

**Single-writer** (invariant 3): the worker that claimed the `IntegrationSyncRun` is the sole
writer of that run's artifacts + derived rows. The only credential mutation allowed outside a
fresh row is the server-side token refresh (§2), which is a serialized-per-grant rotation of the
`IntegrationOAuthGrant`'s own encrypted columns, not an edit of any artifact.

## 4. Sync tasks (Postgres queue, commit-before-I/O, immutable artifacts)

Sync runs execute on the **same Postgres `FOR UPDATE SKIP LOCKED` queue** as audits (invariant
8), through the `TaskQueue` Protocol (`app/orchestration/task_queue.py`) so no Redis is
required. A dedicated `app/workers/integration_worker.py` (a sibling of `audit_worker.py`, not a
fork) claims runs:

**TaskQueue contract for `IntegrationSyncRun`.** As coded today the Protocol
(`app/orchestration/task_queue.py`) and its Postgres implementation
(`app/orchestration/postgres_task_queue.py`) are **hard-coded to `AuditTask`**: every method is
typed against `AuditTask`, `claim`/`release_expired` `select(AuditTask)`, and
`postgres_task_queue.py` reads audit-only knobs (`ERROR_MAX_ATTEMPTS`, `lease_ttl_seconds`, the
`TASK_STATUS_*` constants) from `app.core.config.audits`. To avoid duplicating the
claim/lease/heartbeat/expiry/retry logic (invariant 2), the queue is made **generic over the
task type + its settings** rather than forked: the Protocol becomes `TaskQueue[T]` (generic over
the queue-row model), and `PostgresTaskQueue` is parameterized with the concrete model
(`AuditTask` / `IntegrationSyncRun`) and a small settings object supplying `lease_ttl_seconds`,
`max_attempts`, and the shared `TASK_STATUS_*` / `ERROR_MAX_ATTEMPTS` values (moved to a
queue-neutral config, with `config/audits.py` re-exporting them for the audit path). Because
`IntegrationSyncRun` reuses the exact same queue-row column contract (`status`, `lease_owner`,
`lease_expires_at`, `heartbeat_at`, `attempt_count`, `max_attempts`, `available_at`,
`idempotency_key`, `error_code`/`error_detail`), the identical `FOR UPDATE SKIP LOCKED`
claim/heartbeat/sweeper code serves both task types unchanged — same claim/lease/heartbeat/
expiry/retry semantics, one implementation. `integration_worker.py` then depends only on
`TaskQueue[IntegrationSyncRun]`, never on a concrete class. (Ordering columns like
`priority`/`randomized_position` used by the audit `ORDER BY` are either mirrored on the
integration row or supplied via a settings-provided ordering, so no audit-specific column is
assumed.)

1. In one short transaction: select eligible `IntegrationSyncRun` rows in priority order, lock
   with `FOR UPDATE SKIP LOCKED`, set `leased` + `lease_owner` + `lease_expires_at`, return.
2. **Commit the claim before any network I/O** (invariant 8) — never hold a DB transaction open
   across a Google/Bing API call.
3. Resolve + decrypt the token **from the run's `IntegrationOAuthGrant`**, refreshing it if near
   expiry **via the serialized-per-grant rotation in §2** (grant row lock / CAS, so concurrent
   GSC+GA4 or multi-window workers never clobber each other's rotated token), then page the
   provider API for the requested window; write each page as an immutable
   `IntegrationImportArtifact` (invariant 3).
4. Worker **heartbeats** to extend the lease during a long backfill; a **sweeper**
   (`release_expired`) returns expired leased/running runs to `retry_wait`, or `failed` after
   `max_attempts`.
5. After the raw import lands, derive normalized `IntegrationMetricRow`s (with
   `source_artifact_id` + `importer_version` provenance, invariant 4) — a projection step, never
   a second fetch. The derivation worker first **resolves the property's
   `IntegrationPropertyMapping` to obtain `project_id`** (§3); if the property is unmapped or
   ambiguous it fails the run with `unmapped_property` instead of assigning a project.
6. Cancellation is **cooperative** (invariant 9): the worker stops at the page boundary.

**Scheduling.** Scheduled syncs are enqueued by a lightweight periodic dispatcher (the same
mechanism the roadmap "recurring audit schedules" uses — one owner, invariant 2) that inserts a
`scheduled` `IntegrationSyncRun` per active connection per cadence, deduped by the
**active-only** partial unique index on `(connection_id, sync_kind, window_start, window_end)`
(§3) so a missed tick never double-imports a window that already has a run in flight — while a
deliberate re-sync of an already-completed window is still allowed (it bumps `resync_seq`).
On-demand syncs are enqueued by `POST /integrations/{id}/sync`.

**Idempotency + late data.** GSC/GA4 data is revised for ~2–3 days after the fact. Re-syncing a
recent window bumps `resync_seq` and creates a **new** artifact + new derived rows keyed to that
higher `resync_seq`; the consumer reads the **latest `resync_seq`** per
`(project_id, property_ref, provider, dataset, date, dimension_key)` (revision selection is by
`resync_seq`, the deterministic data-run identity — not by `importer_version`, which versions the
transform code). Old artifacts and rows are retained (immutable), never overwritten (invariant 3).

## 5. API surface (roadmap; `/api/v1`)

All workspace-scoped via `require_active_workspace` / `require_workspace_member` (invariant 5).
No endpoint ever returns a token (invariant 6).

- `GET /integrations` — list this workspace's connections (provider, `account_ref`,
  `last_synced_at`) joined to their grant's `status` + `granted_scopes` — **tokens absent**.
- `GET /integrations/oauth/{provider}/start` — begin OAuth (302 to consent).
- `GET /integrations/oauth/{provider}/callback` — exchange code, persist encrypted tokens on the
  `IntegrationOAuthGrant` (find-or-create for the transport) and attach the connection(s), 302
  back to Settings.
- `POST /integrations/{id}/test` — validate the connection's grant token (resolved from the
  connection's `IntegrationOAuthGrant`, not a connection column) against the provider (a cheap
  authenticated probe, analogous to `POST /provider-connections/{id}/test`); returns a status +
  `error_code`, never the token. Appends an `IntegrationEvent`.
- `POST /integrations/{id}/sync` — enqueue an on-demand `IntegrationSyncRun` (body: optional
  date window); 202 + the run id. 409 if a run for the same window is already active.
- `GET /integrations/{id}/syncs` / `GET /integrations/{id}/syncs/{sync_id}` — sync-run history +
  detail projection (status, window, row counts) — projection only (invariant 7).
- `DELETE /integrations/{id}` — disconnect a connection. **Because credentials live on the
  shared `IntegrationOAuthGrant`, provider revocation is grant-scoped and only fires when the
  *last* connection on a grant is removed** — deleting the GSC connection must never revoke a
  token the GA4 connection on the same Google grant is still using. So: the connection is first
  removed/deactivated; if **other active connections still reference the grant**, the grant's
  tokens are **retained** and nothing is revoked remotely. Only when the deleted connection is
  the **last** one on the grant is provider revocation attempted, and **local disconnect and
  provider revocation are separated so a failed remote revoke can never orphan a live remote
  grant:** **on successful** provider revocation the **grant** is marked `revoked` and its
  encrypted tokens are dropped; **on failed** provider revocation the encrypted tokens are
  **retained** and the **grant** is moved to a `pending_revocation` (error) state — tokens are
  **not** deleted and the grant is **not** marked fully `revoked` — so a background retry (or a
  later manual `DELETE`) can complete the remote revocation before the credentials are destroyed.
  Either outcome appends an `IntegrationEvent` (audit record preserved, invariant 6). The
  `status` enum carrying `pending_revocation`/`revoked` is the **grant's** (`IntegrationOAuthGrant`,
  §3); a connection is simply removed once its grant lifecycle no longer needs it.

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

1. Config: `integrations.py` (endpoints, scopes, sync knobs) + migration for the seven tables
   (`IntegrationOAuthGrant`, `IntegrationConnection`, `IntegrationSyncRun`,
   `IntegrationImportArtifact`, `IntegrationPropertyMapping`, `IntegrationMetricRow`,
   `IntegrationEvent`).
2. `IntegrationOAuthGrant` (token-owning) + `IntegrationConnection` models + OAuth start/callback
   + token encryption **on the grant** (reusing `encrypt_secret`), find-or-create the grant per
   transport and attach connection(s), with `/test`. Unit-test the callback + token round-trip
   against a fake OAuth server (no live provider in tests); assert **no token appears in any DTO
   or log** and that a shared Google grant yields both GSC + GA4 connections from one consent.
3. `IntegrationSyncRun` + `integration_worker.py` on the existing `PostgresTaskQueue`
   (claim/heartbeat/sweeper) with **serialized-per-grant token refresh**, writing immutable
   `IntegrationImportArtifact`s — one provider first (GSC), table-tested against recorded fixture
   payloads.
4. `IntegrationPropertyMapping` + derivation → `IntegrationMetricRow` (provenance +
   `importer_version` + `resync_seq`; `project_id`/`property_ref` resolved via the mapping).
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

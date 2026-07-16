# Invariants — Searchify

> Hard rules. A change that violates any of these is a review failure regardless of whether
> it "works". Numbered so reviews and commits can cite them (e.g. "violates invariant 5").
> Companion docs: [`../Agents.md`](../Agents.md), [`backend-architecture.md`](backend-architecture.md),
> [`frontend-architecture.md`](frontend-architecture.md), [`design.md`](design.md).

## The 12 hard rules

### 1. Config zero-tolerance
Tokens, thresholds, model ids, transport catalogs, guardrail knobs, timeouts, rate limits,
and any tunable magic number live **only** in `backend/app/core/config/*` (e.g.
`config/__init__.py` `Settings`, `config/provider_catalog.py`). Service, domain, worker,
analysis, and API code **reads** config; it never hard-codes these values inline. Frontend:
no magic endpoints or feature flags scattered in components — they belong in the API-contract
layer / env.

### 2. Grep before add / no duplication
Before adding a resource, function, schema, endpoint, token, or component, **grep for it
first**. If an equivalent exists, extend or reuse it. Two functions that do the same thing,
two tokens for the same colour, or two modules that own the same concept are all failures.
One concept → one owner.

### 3. Immutable artifacts / single-writer
`RawResponseArtifact`, `ProviderAttempt`, executions, and `AuditEvent` rows are **written
once and never mutated** after their terminal write. Exactly one writer owns each row (the
worker that claimed the task). No later stage edits a raw artifact or "repairs" it in place.
Re-running produces a **new** task/artifact identity, never an overwrite.

### 4. Provenance + version on every derived row
Every derived row (`ResponseAnalysis`, `BrandMention`, `CompetitorMention`, `Citation`,
`MetricSnapshot`) references the `RawResponseArtifact` it was computed from **and** the
`analyzer_version` (plus formula/rule version where applicable). A derived row with no
traceable source + version is invalid. This is what makes every metric traceable to raw
evidence.

### 5. Workspace auth on every query
Every project-owned read and write goes through the `require_workspace_member` dependency and
filters by `workspace_id`. **Never** scope by `user_id`, never trust an id alone, never add an
"admin" shortcut that bypasses workspace scoping. Cross-workspace access returns 403/404, not
data. All ids are string UUIDs; there are no integer PKs and no `user_id` scoping anywhere.

### 6. BYOK secrets: Fernet-encrypted, never returned, never logged
Provider API keys are **Fernet-encrypted at rest** (`encrypt_secret`/`decrypt_secret`). The
decrypted key is resolved from the `ProviderConnection` at execution time only, **never from
env**, and is **never** placed in a Response DTO, a log line, a `request_snapshot`, or a raw
artifact. Redact credentials + authorization headers from logs. **The brand/competitor list
is never sent to a provider** as part of a prompt.

### 7. Reports / metrics are projections
A report renderer or metrics endpoint **renders versioned, persisted evidence**. It never
performs a second extraction, never calls a provider, and never silently repairs analysis.
Aggregates (`MetricSnapshot`, `/visibility`, exports) read persisted analysis rows only. If
the data is not persisted, it does not appear in a report.

### 8. Postgres-queue leasing rules
The audit queue is Postgres via `FOR UPDATE SKIP LOCKED`. Rules:
- **Commit the claim before any network I/O.** Never hold a DB transaction open across a
  provider call.
- A claim sets `lease_owner` + `lease_expires_at`; the worker **heartbeats** to extend it.
- A **sweeper** returns expired leased/running tasks to `retry_wait` (or `failed` after
  `max_attempts`).
- **No double-claim**: two workers must never execute the same task (SKIP LOCKED guarantees
  it; the unique `(audit_id, prompt_index, repetition)` + unique `idempotency_key` back it up).
- A succeeded task is not re-executed; a rerun creates a new task identity.
- Orchestration depends on the `TaskQueue` Protocol so a future Redis impl needs no
  domain/reporting rewrite.

### 9. Determinism
- Slots are shuffled with the audit's **stored 64-bit `random_seed`** — the same seed
  reproduces the same order.
- Scoring is **deterministic alias/domain matching**. **No LLM is used for headline metrics.**
  (Sentiment + avg-position, which would need an LLM/context, are therefore NOT computed at
  MVP — see invariant-adjacent note below.)
- Cancellation is **cooperative only**: the worker stops at the execution boundary (before the
  next provider call / analysis stage). No mid-call kills, no zombie tasks.

### 10. Logical vs transport identity
Every route and every attempt records all three identities:
`logical_engine` (chatgpt|gemini|claude) + `transport_provider` (anthropic|google|openrouter)
+ `transport_model` (the exact model id). A result missing any of the three is invalid. This
is what lets the dashboard compare engines and gives unambiguous provenance. Example:
`logical_engine=gemini, transport_provider=openrouter, transport_model=google/<exact-id>`.

### 11. Gotcha 1 runbook — shell secrets override Docker Compose `${VAR}`
**Symptom:** `docker compose up` connects Postgres/backend with the wrong
credentials/database even though `.env` looks correct.
**Cause:** this machine exports `POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB`, and
`DATABASE_URL` into **every shell**. Compose resolves `${VAR}` in `docker-compose.yml` from
the **shell environment before `.env`** (`env_file:` only injects vars **inside** the
container, not into `${VAR}` interpolation). So the shell values win and silently override the
repo values.
**Workaround (use verbatim):**
```bash
env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL \
  POSTGRES_PASSWORD=<repo-.env-value> \
  docker compose -f infra/docker/docker-compose.yml up -d --force-recreate
```
Unset the four inherited vars for the Compose invocation and re-supply the repo `.env` value
explicitly. The `docker-compose.yml` carries this note as a baked-in comment.

### 12. Gotcha 2 runbook — tunnel double CORS header → same-origin rewrites
**Symptom:** frontend network calls fail in the browser with a CORS error about **duplicate**
`Access-Control-Allow-Origin` headers — but `curl` against the same backend succeeds.
**Cause:** the Vorflux preview/tunnel proxy injects its own `Access-Control-Allow-Origin: *`.
A FastAPI backend that also sets a specific ACAO (required when `allow_credentials=True`)
produces **two** ACAO headers, which browsers reject. `curl` does not enforce CORS, so it
cannot reproduce the failure.
**Fix:** the browser never talks cross-origin to the backend. Next.js `rewrites()` proxy
`/api/:path*` → the server-only `BACKEND_ORIGIN`, so all browser calls are **same-origin**
(`/api/...` relative). The API client uses a **relative base** (`/api/v1`), `cache:'no-store'`,
`credentials:'include'`.
```ts
// frontend/next.config.ts
async rewrites() {
  return [{ source: '/api/:path*', destination: `${process.env.BACKEND_ORIGIN}/api/:path*` }];
}
```
**Always test this in a real browser, not curl.**

## Note on not-yet-computed metrics (roadmap, keeps invariants 7 + 9 intact)
Sentiment and average-position are **present in the schema but null at MVP**. Computing them
would require an LLM/contextual judgement, which would break "no LLM for headline metrics"
(invariant 9). They are deferred to the roadmap and surfaced as `—` in the UI. Do not
back-fill them with a heuristic that pretends to be deterministic.

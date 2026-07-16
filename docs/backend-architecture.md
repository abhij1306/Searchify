# Backend Architecture — Searchify

> The visibility-slice backend of Searchify. Modular monolith: FastAPI **web** + a separate
> **worker** process, PostgreSQL as durable state **and** MVP task queue (no Redis). Built on
> the full target architecture (workspaces, UUID PKs, BYOK, Postgres queue) from day one.
> Companion docs: [`../Agents.md`](../Agents.md), [`invariants.md`](invariants.md),
> [`frontend-architecture.md`](frontend-architecture.md), [`design.md`](design.md).

## 1. Scope + MVP/roadmap table

Searchify is a full AEO product; the backend **codes only the visibility slice**. Everything
else is documented as roadmap so the product can grow into it without rework. Every
full-product surface is marked below.

| Screenshot surface | Owning subsystem (target) | Status |
|---|---|---|
| Auth (register/login) | `domain/auth` | **MVP — coded** |
| Workspaces + membership | `domain/workspaces` | **MVP — coded** |
| Brand/Project setup (Profile, aliases, domains, competitors) | `domain/projects` | **MVP — coded** |
| Prompt library (manual + CSV import) | `domain/prompts` | **MVP — coded** |
| Provider Settings (BYOK, connection test) | `domain/providers` + `connectors/answer_engines` | **MVP — coded** |
| Audit execution + queue + worker | `orchestration` + `workers` | **MVP — coded** |
| Analysis + metrics + dashboard projection | `analysis` | **MVP — coded** |
| Run/Executions evidence + CSV/MD export | `api/audits` + `analysis/exports` | **MVP — coded** |
| AI-suggested prompt generation (`/generate`) | `domain/prompts` | Roadmap — **stub returns not-implemented** |
| Discovery/analysis model (`DiscoveryModelConfig`) | `domain/providers` | Plumbing-only (stored, not invoked) |
| Cross-run Visibility trend history | `analysis` | **Coded** — `GET /projects/{id}/visibility/trends` (Trends tab) |
| Persisted execution evidence (mentions/citations + query fanout) | `analysis` | **Coded** — `GET /projects/{id}/visibility/evidence` |
| Sentiment + average position | `analysis` | Roadmap (nullable; not computed) |
| LLM Analytics / AI referrals | — | Roadmap |
| Traffic | — | Roadmap |
| Content (writer) | — | Roadmap |
| Opportunities | — | Roadmap |
| Site Health (HTTP/Screaming-Frog-style crawler, no browser) | — | Roadmap |
| Issues catalog | — | Roadmap |
| Brand / Competitors / E-E-A-T rich profile | — | Roadmap |
| Topics | — | Roadmap |
| Tone/Writing Style, Memory, Knowledge Base | — | Roadmap |
| GSC / GA4 / Bing integrations | — | Roadmap |
| Agent, MCP, Settings/white-labelling | — | Roadmap |
| HTML/JSON report renderers, S3 artifacts, Redis queue | `reporting` / infra | Roadmap |
| Direct OpenAI adapter (`openai.py` + `openai_parser.py`) | `connectors/answer_engines` | **Coded** — active transport (OpenRouter adapter/parser deleted) |

## 2. Runtime stack

- **Python 3.12**, FastAPI app-factory, thin router-per-domain modules.
- **Async SQLAlchemy 2.0** typed `Mapped`/`Base`, `async_sessionmaker`, `get_session()`
  dependency; **asyncpg** driver.
- **pydantic-settings** singleton `Settings(BaseSettings)` (config zero-tolerance, invariant 1).
- **Alembic** async migrations bound to `Base.metadata`.
- **Security**: argon2 password hashing, joserfc JWT in a secure **HttpOnly cookie**, Fernet
  `encrypt_secret`/`decrypt_secret` for BYOK.
- **httpx** async client for provider calls.
- **structlog** + correlation ids (optional Logfire).
- **Deploy**: FastAPI web + worker on Railway, PostgreSQL on Railway, Next.js on Vercel. No
  Redis, no S3 at MVP.

App factory (`app/main.py`) wires CORS, lifespan, explicit router includes under `/api/v1`,
and `/health`.

## 3. Registered API surface

All routes are under `/api/v1` and workspace-scoped. Thin routers delegate to `domain/*`
services.

| File | Purpose (routes) |
|---|---|
| `app/api/auth.py` | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` |
| `app/api/workspaces.py` | `GET /workspaces`, `POST /workspaces` |
| `app/api/projects.py` | `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}`, `GET /projects/{id}/visibility?audit_id=`, `GET /projects/{id}/visibility/trends`, `GET /projects/{id}/visibility/evidence` |
| `app/api/prompts.py` | `GET/POST /prompt-sets`, prompt CRUD (`PATCH/DELETE /prompts/{id}`), `POST /prompt-sets/{id}/import` (MVP CSV bulk-create), `POST /prompt-sets/{id}/generate` (**stub, not-implemented**) |
| `app/api/provider_connections.py` | `GET/POST /provider-connections`, `PATCH/DELETE /provider-connections/{id}`, `POST /provider-connections/{id}/test`; `GET /provider-catalog` |
| `app/api/audits.py` | `POST /audits`, `GET /audits`, `GET /audits/{id}`, `POST /audits/{id}/cancel`, `GET /audits/{id}/events` (SSE), `GET /audits/{id}/executions` |
| `app/api/audits.py` (cont.) | `GET /audits/{id}/metrics`, `GET /executions/{id}`, `GET /audits/{id}/export.csv`, `GET /audits/{id}/export.md` |

> The `brands/analyze`, `audits/estimate`,
> `audits/{id}/reports`, `reports/{id}/download` endpoints from [architecture.md](architecture.md) §14 are **roadmap** — the
> MVP surface is exactly the table above.

## 4. Audit request + settings contract

**Provider configuration lives only in Provider Settings** (`ProviderConnection` +
`ProviderRoute`). An audit **references** centrally-configured routes; it never carries API
keys or provider cards.

`POST /audits` request (workspace-scoped, resolves the project via `require_workspace_member`):

```jsonc
{
  "project_id": "<uuid>",
  "prompt_set_id": "<uuid>",          // or explicit prompt_ids[]
  "engines": ["chatgpt", "gemini", "claude"],   // logical engines to measure
  "repetitions": 3,                    // overrides project default_repetitions
  "benchmark_mode": "consumer_like",   // consumer_like | controlled_localized | forced_grounded
  "random_seed": "<optional 64-bit; generated + stored if omitted>"
}
```

Operational overrides an audit **may** snapshot at creation (from config defaults): enabled
engines, repeat count, region, per-provider + global concurrency, requests-per-minute,
retryable error classes, max attempts, request timeout. These are frozen into
`Audit.configuration` at creation and never re-read from live config after that (determinism,
invariant 9).

## 5. High-level flow

```
prompt  →  slot (prompt × engine × repetition)  →  AuditTask (enqueued, idempotency key)
        →  worker claims (FOR UPDATE SKIP LOCKED, commit before I/O)
        →  AnswerEngineAdapter.execute()  →  RawResponseArtifact (immutable) + ProviderAttempt
        →  deterministic analysis (ResponseAnalysis + BrandMention/CompetitorMention/Citation)
        →  MetricSnapshot projection (aggregate at finalize)
```

Adapters **execute and normalize only**; they never compute visibility ([architecture.md](architecture.md) §10). Analysis is
deterministic ([architecture.md](architecture.md) §11). Metrics are a **projection** of persisted analysis (invariant 7).

## 6. Subsystem ownership

| Package | Owns |
|---|---|
| `app/api/*` | Thin HTTP routers; no business logic. |
| `app/core/config/*` | **All** config: `Settings`, `provider_catalog.py`, thresholds, guardrails (invariant 1). |
| `app/core/{database,security,telemetry}.py` | `Base`/engine/session; argon2/JWT/Fernet; structlog + correlation ids. |
| `app/models/*` | SQLAlchemy persistence (UUID PKs, provenance columns). |
| `app/schemas/*` | Pydantic request/response DTOs (secrets never present). |
| `app/domain/{auth,workspaces,projects,prompts,providers,audits}/*` | Services + business rules per resource. |
| `app/connectors/answer_engines/*` | Answer-engine adapters + parsers (gemini, anthropic, openai — direct OpenAI Responses API). The retired OpenRouter adapter/parser were deleted. |
| `app/orchestration/{audit_state,task_queue,postgres_task_queue}.py` + `domain/audits/planner.py` | State machine, `TaskQueue` Protocol + Postgres impl, slot planning. |
| `app/analysis/{normalization,scoring,exports}.py` | Deterministic scoring, aggregation, CSV/MD export. |
| `app/workers/audit_worker.py` | Separate process: claim → execute → persist → analyze → transition. |

## 7. Persistence model

All models use **string UUID PKs** and are **workspace-scoped** (directly or via their
project). No integer PKs, no `user_id` columns.

| File / model | Purpose | Provenance / version columns |
|---|---|---|
| `models/user.py` `User` | Auth identity | — |
| `models/workspace.py` `Workspace`, `WorkspaceMember` | Tenant + membership | — |
| `models/project.py` `Project` | Workspace-scoped project (brand_name, website_url, country_code, language_code, benchmark_mode, default_repetitions) | — |
| `models/brand.py` `Brand`, `BrandAlias`, `Competitor`, `OwnedDomain`, `UnintendedDomain` | Normalized brand identity (serialized back to the dict the scorer expects) | — |
| `models/prompt.py` `PromptSet`, `Prompt` (text, theme, intent, branded, enabled, origin, `generation_evidence` JSONB) | Dedicated prompt resource | `origin` (generated/manual/imported) |
| `models/provider.py` `ProviderConnection` (Fernet secret), `ProviderRoute` (logical_engine + transport_provider + transport_model + is_default), `ProviderConnectionTest` (append-only), `DiscoveryModelConfig` (plumbing-only) | BYOK provider config | route carries logical+transport+model identity (invariant 10) |
| `models/audit.py` `Audit`, `AuditPromptSnapshot`, `AuditEngineSnapshot` | Run + frozen prompt/engine snapshots | `random_seed`, `configuration`, `analyzer_version` on finalize |
| `models/audit.py` `AuditTask` | Queue row (see §9) | `idempotency_key` unique, `(audit_id, prompt_index, repetition)` unique |
| `models/audit.py` `ProviderAttempt`, `RawResponseArtifact` | Append-only attempts + immutable raw payloads | writer = claiming worker (invariant 3) |
| `models/audit.py` `AuditEvent` | Append-only lifecycle events (SSE source) | — |
| `models/analysis.py` `ResponseAnalysis`, `BrandMention`, `CompetitorMention`, `Citation` | Deterministic per-execution analysis | each references its `RawResponseArtifact` + `analyzer_version` (invariant 4) |
| `models/analysis.py` `MetricSnapshot` | Aggregate run metrics (projection) | `analyzer_version` + formula version |

The execution row (`AiVisibilityExecution`, UUID-keyed) carries:
`prompt_index`, `prompt_text_snapshot`, `prompt_theme_snapshot`, `prompt_intent_snapshot`,
`repetition`, `randomized_position`, `status`, `answer_text`, `search_used`, `search_events`,
`citations`, `score` (JSONB), `request_snapshot` (**never contains the API key or brand list**),
`provider_metadata`, `error_code`, `error_message`, `latency_ms`.

## 8. Record / review / provenance contracts

- **Record**: the worker writes the `RawResponseArtifact` (immutable) + `ProviderAttempt`
  (append-only) exactly once per execution. `request_snapshot` excludes secrets + brand list.
- **Review**: `GET /executions/{id}` returns the persisted evidence — answer text,
  `search_used`, classified citations (owned/competitor/third-party), brand + competitor
  mentions, per-response `score` dict. No recomputation.
- **Provenance**: every derived row carries its source artifact + `analyzer_version` (invariant
  4). Metrics and exports read these rows only (invariant 7).

## 9. Answer engine + identity + BYOK

**Adapter contract** (`connectors/answer_engines/contracts.py`): `validate_connection()`,
`estimate()`, `execute()`, `normalize_response()`, `normalize_usage()`,
`normalize_citations()`, `classify_error()`. Adapters execute + normalize only.

**Logical vs transport identity** (invariant 10) is persisted on every route + attempt:

```
logical_engine     = gemini | chatgpt | claude
transport_provider = google | anthropic | openai   # active; `openrouter` is historical-only
transport_model    = <exact model id, e.g. gemini-flash-latest>
```

**Active engine transports (v2 direct-only, `provider_catalog.py`):** exactly one approved
route per engine —
- `gemini` → direct `google`, model `gemini-flash-latest`. Working direct adapter (grounding +
  citation parsing).
- `claude` → direct `anthropic`, model `claude-sonnet-4-6`. Working direct adapter.
- `chatgpt` → direct `openai` via the **OpenAI Responses API** (`openai.py` +
  `openai_parser.py`), model `gpt-5.4`.

`ACTIVE_TRANSPORTS` is `{openai, anthropic, google}`. `openrouter` is **retired** as an active
transport — it lives on only in `HISTORICAL_TRANSPORTS` so retired rows read safely (never an
active/approved/write route). Migration
`migrations/versions/0008_direct_openai_retirement.py` (marker `openrouter_retired_v2`) retires
active OpenRouter connections/routes and adds `provider_connections.deactivation_reason`,
`provider_routes.active`, and `provider_routes.deactivation_reason`. The OpenRouter
adapter/parser files were deleted.

**BYOK** (invariant 6): the decrypted key is resolved from `ProviderConnection` at execution
time, never from env, never persisted into snapshots/logs. `POST /provider-connections/{id}/test`
returns a connection status without leaking the key.
`GET /provider-catalog` exposes the approved transports/models + guardrail knobs from
`config/provider_catalog.py`.

## 10. Postgres task queue

Postgres is both durable state and the MVP queue (no Redis; [architecture.md](architecture.md) §7). Orchestration depends on
the `TaskQueue` Protocol so a future Redis impl needs no domain rewrite.

**`audit_tasks` (queue+lease) fields:** `id` (UUID), `audit_id`, `prompt_snapshot_id`,
`logical_engine`, `provider_route_snapshot`, `prompt_index`, `repetition`, `idempotency_key`
(unique), unique `(audit_id, prompt_index, repetition)`, `status`
(`queued|leased|running|succeeded|retry_wait|failed|cancelled`), `priority`, `available_at`,
`lease_owner`, `lease_expires_at`, `heartbeat_at`, `attempt_count`, `max_attempts`,
`result_artifact_id`, `error_code`, `error_detail`, timestamps.

**Claim/lease/heartbeat/sweeper** (invariant 8):
1. In one short transaction: select eligible rows in deterministic priority order, lock with
   `FOR UPDATE SKIP LOCKED`, set `leased` + `lease_owner` + `lease_expires_at`, return.
2. **Commit before any network I/O.** Never hold a transaction across a provider call.
3. Worker **heartbeats** to extend the lease during execution.
4. A **sweeper** (`release_expired`) returns expired leased/running tasks to `retry_wait`, or
   marks `failed` after `max_attempts`.
5. `SKIP LOCKED` + the two unique constraints prevent **double-claim**.
6. Succeeded tasks are never re-run; a rerun creates a new task identity.

`TaskQueue` Protocol: `claim() / heartbeat() / succeed() / retry() / fail() / cancel() /
release_expired()`. MVP impl = `PostgresTaskQueue`.

**Deterministic slot + cooperative cancel** (invariant 9): the planner freezes prompt/engine/
scoring snapshots, generates slots, **shuffles them from the stored 64-bit `random_seed`**, and
enqueues one `AuditTask` per slot with an idempotency key. Cancellation is cooperative — the
worker stops at the execution boundary (before the next provider call / analysis stage) and
terminalizes remaining executions.

## 11. Audit state machine

`app/orchestration/audit_state.py` centralizes an `_ALLOWED_TRANSITIONS` /
`transition_status` state machine:

```
DRAFT → VALIDATING → QUEUED → RUNNING → ANALYZING → REPORTING → COMPLETED
VALIDATING → FAILED
RUNNING/ANALYZING → PARTIALLY_COMPLETED
QUEUED/RUNNING → CANCELLED
```

A provider auth failure on one engine must **not** discard successful results from other
engines — the run resolves to `PARTIALLY_COMPLETED` and discloses coverage + failed tasks.
Invalid transitions raise.

## 12. Analysis + metrics projection

Deterministic analysis (`analysis/normalization.py`, `analysis/scoring.py`):
- Unicode/case normalization, boundary-safe alias matching, explicit alias + domain registry.
- URL canonicalization + tracking-param removal; **citation classification**: owned /
  competitor / third-party (+ unintended domain).
- Ordered-list/table/rank detection; deterministic mention detection.
- **Metrics**: brand-mention rate, owned-citation rate, mention→owned conversion,
  share-of-voice (response-level + mention-level), fanout injection rates, repeat stability.
- **Sentiment + average position are NOT computed** at MVP — exposed as nullable/absent and
  marked roadmap (would need an LLM; breaks invariant 9).

**Dashboard/metrics endpoints (projections, no provider calls):**
- `GET /audits/{id}/metrics` — single-run `MetricSnapshot` projection.
- `GET /projects/{id}/visibility?audit_id=<id>` — **selected-run** projection behind the
  **Overview** tab: Visibility Score, **per-engine comparison** for that run, and the
  **brand-vs-competitor rankings** table (Visibility% + SOV populated; sentiment + avg-position
  present but null). Defaults to the project's latest completed audit when `audit_id` is omitted.
- `GET /projects/{id}/visibility/trends` — cross-run projection behind the **Trends** tab: an
  ordered `VisibilityTrendPoint` series from persisted `MetricSnapshot` rows, optional
  `engine`/`from`/`to` filters and `granularity=run|week|month`. Empty history → `[]` (not 404).
- `GET /projects/{id}/visibility/evidence` — shared persisted dataset behind the **Mentions &
  Citations** and **Query Fanout** tabs: `VisibilityEvidenceResponse{items, truncated}`, a
  read-only projection of persisted mentions/citations plus normalized query-fanout events.
  Optional `audit_id`/`prompt_id`/`engine`/`from`/`to` filters and a bounded `limit`. Each item
  carries a fanout `state` of `queries_available | count_only | no_search`. No provider is
  called and no evidence is inferred/backfilled (invariant 7).
- `GET /executions/{id}` — single-execution evidence.
- `GET /audits/{id}/export.{csv,md}` — reproducible exports (`analysis/exports.py`).

## 13. Full-product surface map (owning subsystem + status)

See §1 for the screenshot-surface → subsystem → MVP/roadmap mapping. Summary of the target
subsystem layout the product grows into ([architecture.md](architecture.md) §5): `domain/{workspaces, brands, prompts,
providers, audits, visibility, citations, reports}`, `connectors/{answer_engines,
discovery_models, web_evidence, object_storage}`, `orchestration/*`, `analysis/*`,
`reporting/*`, `workers/*`. **At MVP only** `domain/{auth, workspaces, projects, prompts,
providers, audits}`, `connectors/answer_engines`, `orchestration/*`, `analysis/*`, and
`workers/audit_worker.py` are coded; the rest are documented placeholders.

## 14. Known Issues / Drift

- **All ids are UUIDs and workspace-scoped** — never integer PKs, never `user_id`-scoped
  (invariant 5).
- **Sentiment/avg-position are nullable** at MVP; every aggregate must tolerate null and never
  back-fill a fake heuristic (invariant 9).
- **`request_snapshot` must exclude the API key and brand list** (invariant 6).
- **[architecture.md](architecture.md) §14 lists roadmap endpoints** (`brands/analyze`, `audits/estimate`, reports/download).
  They are intentionally absent from the MVP surface (§3) — do not add them without a plan.
- **SSE `/events` is MVP on the backend**, but the frontend polls first and consumes SSE
  optionally; keep the endpoint stable even if the UI does not depend on it yet.
- Two operational gotchas (shell-env override, tunnel double-CORS) are documented as runbooks
  in [`invariants.md`](invariants.md) §11–12.

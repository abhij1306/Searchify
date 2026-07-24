# Backend Architecture — Searchify

> The visibility-slice backend of Searchify. Modular monolith: FastAPI **web** + a separate
> **worker** process, PostgreSQL as durable state **and** task queue (no Redis). Built on
> the full target architecture (workspaces, UUID PKs, BYOK, Postgres queue) from day one.
> Companion docs: [`../Agents.md`](../Agents.md), [`invariants.md`](invariants.md),
> [`frontend-architecture.md`](frontend-architecture.md), [`design.md`](design.md).

## 1. Scope + delivery table

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
| AI-suggested prompt generation (`/generate`) | `domain/prompts` + `connectors/agent` | **Coded** — `prompt-gen-v2` reads the curated BrandProfile plus topic descriptions with grounding rules; topic-driven generation via the `.env` default agent fills a set-wide pool of `GENERATION_ACTIVE_THRESHOLD` (default 20) `active` prompts, with later rows `proposed` until human promotion |
| Discovery/analysis model (`DiscoveryModelConfig`) | `domain/providers` | Plumbing-only (stored, not invoked) |
| Cross-run Visibility trend history | `analysis` | **Coded** — `GET /projects/{id}/visibility/trends` (Trends tab) |
| Persisted execution evidence (mentions/citations + query fanout) | `analysis` | **Coded** — `GET /projects/{id}/visibility/evidence` |
| Sentiment + average position | `analysis` | Roadmap (nullable; not computed) |
| LLM Analytics / AI referrals | `domain/analytics` + `models/analytics` + `workers/analytics_worker.py` | **Implemented** — deterministic referral classification/sanitization over integration metric rows (no LLM), `AnalyticsTask` queue chain (`ingest_referrals → classify_referrals → analytics_snapshot_refresh`, retention sweep), `GET /projects/{id}/llm-analytics(+referrals,+themes)` serving persisted `AnalyticsSnapshot` projections only |
| Traffic | `domain/traffic` + `models/traffic` | **Implemented** — `TrafficSnapshot`/`TrafficPageStat`/`TrafficQueryStat` projections over integration metric rows (page join → `SiteUrl` via `canonical_identity`), `GET /projects/{id}/traffic(+pages,+queries)` persisted-snapshot reads + `POST …/traffic/sync` enqueue pass-through |
| Content (writer) | `domain/content` + `connectors/discovery_models` + `workers/content_worker.py` | **Implemented (basic v1)** — env-driven single output type (`website_page`), default-on Website-context tool, cancel; briefs/revisions/CMS stay roadmap ([`roadmap/content-writer.md`](roadmap/content-writer.md)) |
| Opportunities | — | Roadmap |
| Site Health (HTTP/Screaming-Frog-style crawler, no browser) | `site_health` | **Implemented** — see [`site-health.md`](site-health.md) |
| Issues catalog | `site_health` | **Implemented** — grouped issues + per-URL detail |
| Brand / Competitors / E-E-A-T rich profile | `domain/projects` | **Partial** — tenant-scoped `BrandProfile` manual CRUD, immutable default-agent drafts + explicit acceptance, and shared KB context are coded; competitor profiles, E-E-A-T, and `/brand` UI remain roadmap |
| Topics | `domain/prompts` (`topics.py`) | **Coded** — first-class `Topic` table, per-project CRUD with active/proposed counts; generation groups prompts by topic |
| Tone/Writing Style, Memory, broader Knowledge Base product surface | — | Roadmap |
| GSC / GA4 / Bing integrations | `domain/integrations` + `connectors/integrations` + `workers/integration_worker.py` + `workers/integration_dispatcher.py` | **Implemented** — OAuth grants with Fernet-encrypted tokens (one shared Google grant ⇒ GSC+GA4; Microsoft ⇒ Bing), sync runs on the SKIP LOCKED queue (`INTEGRATION_QUEUE_SPEC`), immutable per-page `IntegrationImportArtifact`s, derivation to `IntegrationMetricRow` with provenance + `resync_seq`, scheduled dispatcher (spec: [`roadmap/integrations.md`](roadmap/integrations.md)) |
| Agent, MCP, Settings/white-labelling | — | Roadmap |
| HTML/JSON report renderers, S3 artifacts, Redis queue | `reporting` / infra | Roadmap |
| Direct OpenAI adapter (`openai.py` + `openai_parser.py`) | `connectors/answer_engines` | **Coded** — active transport |

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
- **Deploy**: FastAPI web + workers on Railway, PostgreSQL on Railway, Next.js on Vercel. Each
  worker is a **separate Railway service** sharing the same env: `python -m
  app.workers.audit_worker`, `app.workers.content_worker` (incl. `MISTRAL_API_KEY` and the
  `CONTENT_*` knobs), `app.workers.integration_worker` + `app.workers.integration_dispatcher`
  (integrations sync + cadence; need the `INTEGRATION_*` knobs, OAuth client secrets, and
  `REFERRAL_HASH_SALT`), and `app.workers.analytics_worker` (referral/projection chain; no
  network I/O). No Redis, no S3 at MVP.

App factory (`app/main.py`) wires CORS, lifespan, explicit router includes under `/api/v1`,
and `/health`.

## 3. Registered API surface

All routes are under `/api/v1` and workspace-scoped. Thin routers delegate to `domain/*`
services.

| File | Purpose (routes) |
|---|---|
| `app/api/auth.py` | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` |
| `app/api/workspaces.py` | `GET /workspaces`, `POST /workspaces` |
| `app/api/projects.py` | `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}`, `GET/PUT /projects/{id}/brand-profile`, `POST /projects/{id}/brand-profile/suggest`, `POST /projects/{id}/brand-profile/suggestions/{suggestion_id}/accept`, `GET /projects/{id}/visibility?audit_id=`, `GET /projects/{id}/visibility/trends`, `GET /projects/{id}/visibility/evidence` |
| `app/api/prompts.py` | `GET/POST /prompt-sets`, prompt CRUD (`PATCH/DELETE /prompts/{id}`), `POST /prompt-sets/{id}/import` (MVP CSV bulk-create), `POST /prompt-sets/{id}/generate` (AI topic+prompt generation via default agent), `POST /prompt-sets/{id}/prompts/bulk-status`, topics CRUD (`GET/POST /projects/{id}/topics`, `PATCH/DELETE /topics/{id}`) |
| `app/api/provider_connections.py` | `GET/POST /provider-connections`, `PATCH/DELETE /provider-connections/{id}`, `POST /provider-connections/{id}/test`; `GET /provider-catalog` |
| `app/api/audits.py` | `POST /audits`, `GET /audits`, `GET /audits/{id}`, `POST /audits/{id}/cancel`, `GET /audits/{id}/events` (SSE), `GET /audits/{id}/executions` |
| `app/api/audits.py` (cont.) | `GET /audits/{id}/metrics`, `GET /executions/{id}`, `GET /audits/{id}/export.csv`, `GET /audits/{id}/export.md` |
| `app/api/content.py` | `GET/POST /content/generations` (idempotent enqueue via `Idempotency-Key`), `GET /content/generations/{id}`, `POST /content/generations/{id}/regenerate` (new record, fresh context), `POST /content/generations/{id}/try-again` (new record, frozen context snapshot), `POST /content/generations/{id}/cancel` |
| `app/api/products.py` | `GET/POST /projects/{id}/products`, `GET/PATCH/DELETE /products/{id}`, `POST /projects/{id}/products/import` (CSV upload **or** `{ products: [...] }` JSON rows), `GET/POST /projects/{id}/competitor-products`, `PATCH/DELETE /competitor-products/{id}`, `GET /projects/{id}/products/visibility?audit_id=&engine=`, `GET /products/{id}/visibility/evidence?audit_id=&engine=&limit=`, `GET /projects/{id}/products/visibility/export.csv` |

> The `brands/analyze`, `audits/estimate`,
> `audits/{id}/reports`, `reports/{id}/download` endpoints from [architecture.md](architecture.md) §14 are **roadmap** — the
> The current coded surface is exactly the table above.

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
| `app/domain/{auth,workspaces,projects,prompts,providers,audits,content}/*` | Services + business rules per resource. |
| `app/connectors/answer_engines/*` | Answer-engine adapters + parsers (gemini, anthropic, openai — direct OpenAI Responses API). |
| `app/connectors/discovery_models/*` | Discovery/generative model connectors for the content vertical (Mistral chat-completions at v1). Provider-agnostic contract; the API key is env-held (`SecretStr`), resolved only at call time — deliberately **not** BYOK: content generation is a platform capability, measurement keys stay per-workspace. |
| `app/orchestration/{audit_state,task_queue,postgres_task_queue}.py` + `domain/audits/planner.py` | State machine, `TaskQueue` Protocol + Postgres impl (generic over queue specs — see §10), slot planning. |
| `app/analysis/{normalization,scoring,exports}.py` | Deterministic scoring, aggregation, CSV/MD export. |
| `app/workers/audit_worker.py` | Separate process: claim → execute → persist → analyze → transition. |
| `app/workers/content_worker.py` | Separate process for content generation: claim → build messages (prompt + optional deterministic Website-context projection) → call provider → atomic `finalize_attempt` (one locked transaction re-checking lease + cancel before writing the attempt + terminal state). |

## 7. Persistence model

All models use **string UUID PKs** and are **workspace-scoped** (directly or via their
project). No integer PKs, no `user_id` columns.

| File / model | Purpose | Provenance / version columns |
|---|---|---|
| `models/user.py` `User` | Auth identity | — |
| `models/workspace.py` `Workspace`, `WorkspaceMember` | Tenant + membership | — |
| `models/project.py` `Project` | Workspace-scoped project (brand_name, website_url, country_code, language_code, benchmark_mode, default_repetitions) | — |
| `models/brand.py` `Brand`, `BrandAlias`, `Competitor`, `OwnedDomain`, `UnintendedDomain` | Normalized brand identity (serialized back to the dict the scorer expects) | — |
| `models/brand.py` `BrandProfile` | Tenant-scoped curated brand knowledge base; one row per brand/project | Per-field source tokens (`manual` / `web_evidence` / `ai_suggested`) |
| `models/brand.py` `BrandProfileSuggestion` | Immutable default-agent profile draft awaiting explicit review/acceptance | Model host/model + prompt-template version + frozen input snapshot |
| `models/prompt.py` `PromptSet`, `Prompt` (text, theme, intent, branded, enabled, origin, `status` proposed/active/archived, `topic_id`, `normalized_text_hash` dedupe key, `generation_evidence` JSONB) | Dedicated prompt resource | `origin` (generated/manual/imported); generated rows carry model identity + `generation_run_id` in `generation_evidence` |
| `models/prompt.py` `Topic` (project-scoped, `origin` manual/generated, unique name per project) | Topic/category grouping for prompts (`Prompt.topic_id` SET NULL on delete) | `origin` (manual/generated) |
| `models/provider.py` `ProviderConnection` (Fernet secret), `ProviderRoute` (logical_engine + transport_provider + transport_model + is_default), `ProviderConnectionTest` (append-only), `DiscoveryModelConfig` (plumbing-only) | BYOK provider config | route carries logical+transport+model identity (invariant 10) |
| `models/audit.py` `Audit`, `AuditPromptSnapshot`, `AuditEngineSnapshot` | Run + frozen prompt/engine snapshots | `random_seed`, `configuration`, `analyzer_version` on finalize |
| `models/audit.py` `AuditTask` | Queue row (see §9) | `idempotency_key` unique, `(audit_id, prompt_index, repetition)` unique |
| `models/audit.py` `ProviderAttempt`, `RawResponseArtifact` | Append-only attempts + immutable raw payloads | writer = claiming worker (invariant 3) |
| `models/audit.py` `AuditEvent` | Append-only lifecycle events (SSE source) | — |
| `models/analysis.py` `ResponseAnalysis`, `BrandMention`, `CompetitorMention`, `Citation` | Deterministic per-execution analysis | each references its `RawResponseArtifact` + `analyzer_version` (invariant 4) |
| `models/analysis.py` `MetricSnapshot` | Aggregate run metrics (projection) | `analyzer_version` + formula version |
| `models/product.py` `Product`, `CompetitorProduct` | Product catalog (agentic commerce): own SKUs (aliases/variants/price/attributes) + competitor products for share-of-voice | unique `(project_id, sku)` / `(competitor_id, name)` |
| `models/product.py` `ProductResponseAnalysis`, `ProductMention` | Deterministic per-execution PRODUCT analysis (sibling of the brand-level pass, same persisted artifact) | `RawResponseArtifact` + `product_analyzer_version` + `product_scoring_rule_version` (invariant 4) |
| `models/product.py` `ProductMetricSnapshot` | Per-(audit, catalog-entry) aggregate product metrics (projection) | analyzer/rule versions + frozen `entry_id` in `metrics` |
| `models/content.py` `ContentGeneration` | Content request + queue row in one (AuditTask pattern): prompt, `output_type`, `website_context_*` (enabled/status/frozen snapshot), provider/model identity, output + usage, plus the full generic-queue column set (status/lease/attempts/idempotency) | `(workspace_id, idempotency_key)` unique + `request_fingerprint`; `generator_version`; `website_context_snapshot` frozen at enqueue |
| `models/content.py` `ContentGenerationAttempt` | Append-only per-provider-call attempts | writer = claiming worker inside `finalize_attempt` (invariant 3) |

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
transport_provider = google | anthropic | openai   # active direct transports
transport_model    = <exact model id, e.g. gemini-flash-latest>
```

**Active engine transports (v2 direct-only, `provider_catalog.py`):** exactly one approved
route per engine —
- `gemini` → direct `google`, model `gemini-flash-latest`. Working direct adapter (grounding +
  citation parsing).
- `claude` → direct `anthropic`, model `claude-sonnet-4-6`. Working direct adapter.
- `chatgpt` → direct `openai` via the **OpenAI Responses API** (`openai.py` +
  `openai_parser.py`), model `gpt-5.4`.

`ACTIVE_TRANSPORTS` is `{openai, anthropic, google}`. Each logical engine has one approved
direct route in `APPROVED_ROUTES`; retired routes are never executable.

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

The Site Health worker claims a config-bounded batch and executes it with
`asyncio` concurrency. Every claimed lease is heartbeated even while waiting
for a per-host slot; `global_concurrency`, `worker_concurrency`,
`per_host_concurrency`, and `per_host_delay_seconds` bound throughput and
politeness. Each task still uses short transactions and commits its claim before
network I/O, so concurrency does not weaken the leasing contract.

`TaskQueue` Protocol: `claim() / heartbeat() / succeed() / retry() / fail() / cancel() /
release_expired()`. MVP impl = `PostgresTaskQueue`.

**Generic queue extension (type-only).** `PostgresTaskQueue` is parameterized over a
`QueueSpec` (`app/core/config/task_queue.py`) so the same claim/lease/heartbeat/sweeper
machinery serves five task types — `audit_tasks`, the Site Health crawl queue,
`content_generations` (`CONTENT_QUEUE_SPEC` in `app/core/config/content.py`),
`integration_sync_runs` (`INTEGRATION_QUEUE_SPEC` in `app/core/config/integrations.py`), and
`analytics_tasks` (`ANALYTICS_QUEUE_SPEC` in `app/core/config/analytics.py`) — all with claim
order `priority desc → available_at asc → randomized_position asc`. The extension is type-only:
`succeed()` and the queue semantics are unchanged. The content worker deliberately uses the
queue only for claim/heartbeat/mark-running/cancel/release-expired; **terminal writes go
through its own atomic `finalize_attempt`** — one locked transaction per provider call that
re-checks the lease owner and a cancelled-in-flight status before appending the
`ContentGenerationAttempt` and stamping terminal state (a cancelled-in-flight call records
the attempt but discards the output).

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
`workers/audit_worker.py` are coded; since then `site_health` (crawler + issues) and the
**content vertical** (`domain/content`, `connectors/discovery_models`, `api/content.py`,
`workers/content_worker.py` — basic v1), and the **products vertical** (`domain/products`,
`models/product.py`, `analysis/product_scoring.py` + `analysis/product_service.py`,
`api/products.py` — agentic commerce catalog + deterministic product-visibility
projections, a sibling analyzer pass in `workers/audit_worker.py`) have shipped; the rest are documented placeholders.

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

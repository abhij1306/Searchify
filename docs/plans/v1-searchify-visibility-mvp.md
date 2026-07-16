# Searchify — AI-Visibility MVP + Product Documentation (Implementation Plan)

> One merged plan across backend + frontend for Searchify's own AI-visibility subsystem, built on the
> target architecture. The full-product surface (auth, workspaces, brand/project setup, prompts,
> provider settings, visibility dashboard, run/executions explorer, and the documented roadmap
> surfaces) is described in prose in `../architecture.md` and the `docs/` files this plan writes. All
> prior "blocking questions" are decided in the summary and reflected here.
>
> **Task numbering:** `D1` = the documentation task (written **first**, before all implementation);
> `B#` = backend/repo tasks; `F#` = frontend tasks; `V1` = final full-stack verification. `[after ...]`
> markers may cross layers (e.g. `F8 [after B4]`). **All B1/F1 implementation depends on D1** — docs
> come first per the user's requirement.

## Repository layout (docs/ + Agents.md by D1; backend/ by B1; frontend/ by F1)
```
Searchify/
  Agents.md                      # repo-root agent guide (D1)
  backend/
    app/{api,core,core/config,models,schemas,domain,connectors/answer_engines,orchestration,analysis,workers}/
    tests/{unit,component}/
  migrations/                    # Alembic async
  frontend/                      # Next.js App Router (see F# tasks)
  docs/                          # design.md, backend-architecture.md, frontend-architecture.md, invariants.md
  infra/docker/                  # docker-compose.yml
```

## Unified contract (both layers)
- **All ids are string UUIDs.** Workspace-scoped; no `user_id` scoping, no integer PKs.
- **API prefix `/api/v1`** (reference `/api/ai-visibility` dropped).
- Logical engines `chatgpt|gemini|claude`; MVP transports `anthropic|google|openrouter` (`openai` direct
  is **reserved — fast-follow, disabled at MVP**; chatgpt reaches MVP via `openrouter`).
- benchmark_mode `consumer_like|controlled_localized|forced_grounded`; prompt intents
  `discovery|comparison|purchase|service|local`.
- Browser → backend is **same-origin** via Next.js `rewrites()` (`/api/:path*` → server-only
  `BACKEND_ORIGIN`); the browser never sees a cross-origin backend URL (gotcha 2).

---

# Documentation (written first)

## D1 — All five doc files [first; before all implementation]
Written **before** any backend or frontend implementation (B1 and F1 both `[after D1]`). D1 may create
the `docs/` directory and empty package dirs only; no application code. Whole-product scope, explicit
MVP/roadmap marker per surface, both operational gotchas in `invariants.md` + a runbook section.
- **`Agents.md`** (repo root) — session bootstrap / terse style; what Searchify is (full AEO product +
  visibility-first MVP boundary); read-on-demand doc guide; default startup flow (grep before add;
  identify owning subsystem); always-on rules (config not in service code; workspace auth on every
  query; secrets never returned; provenance on derived rows); verify commands (focused pytest, ruff,
  alembic upgrade, docker compose up with the shell-env workaround).
- **`docs/backend-architecture.md`** — scope + MVP/roadmap table (every product surface marked
  coded/roadmap); runtime stack; registered API surface (`File | Purpose` router table); audit request +
  settings contract; high-level flow (prompt → slot → task → adapter → raw artifact → analysis → metrics
  projection); subsystem ownership; persistence model (`File | Purpose` per model + provenance/version
  columns); record/review/provenance contracts; answer-engine + logical/transport identity + BYOK;
  Postgres task queue (claim/lease/heartbeat/sweeper; deterministic slot + cooperative cancel);
  full-product surface map with owning-subsystem + MVP/roadmap status; Known Issues / Drift.
- **`docs/invariants.md`** — numbered hard rules: (1) config zero-tolerance (tokens/thresholds/model ids
  in `app/core/config/*`); (2) grep before add / no duplication; (3) immutable artifacts / single-writer;
  (4) provenance + version on every derived row; (5) workspace auth on every query — never user-id/admin
  shortcut; (6) BYOK secrets Fernet-encrypted, never returned, redacted from logs, brand list never sent
  to providers; (7) reports/metrics are projections; (8) Postgres-queue leasing rules (commit claim
  before network I/O; heartbeat; sweeper; no double-claim); (9) determinism (stored-seed slot shuffle;
  deterministic alias scoring, no LLM for headline metrics; cooperative cancel only); (10) logical vs
  transport identity recorded on every route/attempt; (11) **gotcha 1** runbook: shell secrets override
  compose `${VAR}` → `env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL … docker
  compose up` workaround; (12) **gotcha 2** runbook: tunnel double-ACAO → same-origin Next.js
  `rewrites()`, test in a real browser not curl.
- **`docs/frontend-architecture.md`** — stack & role; full-product route map (every surface tagged
  MVP/Roadmap); MVP-vs-roadmap boundary table; frontend subsystems (shell+auth; API contract layer;
  setup; prompts; providers; visibility; runs/executions; UI + token policy); live backend API usage
  (same-origin proxy; `/api/v1` endpoints per screen; workspace scoping; cookie session; polling for run
  progress — the backend SSE `/events` endpoint is MVP but the UI uses polling first and consumes SSE
  optionally); drift policy (`strictValidate` fails loud; backend is source of truth); zod
  data contracts; testing surface; architectural notes; companion-docs links.
- **`docs/design.md`** — a written design system **with concrete values so no visual reference is needed**:
  overview (globals.css is the single source of truth; dense clean B2B analytics aesthetic, original
  palette); theme model (explicit light + dark surface hierarchies); **actual token values** — light +
  dark hex palettes for surfaces/borders/text/accent, the **px type scale** (display/heading/body/label
  with line-heights + letter-spacing; mono tabular numerals), the **4px spacing steps**, **radii** (incl.
  pill), **elevation** shadow tokens; semantic status + **sentiment** (positive/neutral/negative) +
  **citation-classification** (owned/competitor/third-party) + **run-status** + **score-band** colors,
  all text contrast ≥ 4.5:1; component-primitive inventory (button, badge, table dense, card, score-ring,
  donut, trend-chart, tabs/segmented, sidebar, top bar, input/field, dialog, dropdown, tooltip, skeleton,
  history-drawer); **per-screen layout prose for all seven MVP screens** (Auth, App Shell, Brand/Project
  setup, Prompt library, Provider Settings, Visibility dashboard, Run/Executions explorer); motion +
  accessibility; implementation rules (bridged Tailwind tokens only; no raw hex in components; both
  themes always defined).
- **Test:** all five files exist; markdown lints; cross-references resolve; a reviewer can trace
  subsystem ownership and confirm every MVP screen has concrete tokens + layout prose so the MVP can be
  rebuilt from docs alone (no visual-reference dependency).

---

# Backend + repository foundation

## B1 — Repo skeleton + Docker Compose + Postgres + Alembic + core scaffolding [after D1]
- Monorepo dirs (above). `app/core/database.py` (`Base`, async engine, `async_sessionmaker`,
  `get_session()`), `app/core/config/__init__.py` (`Settings(BaseSettings)` singleton),
  `app/core/security.py` (argon2 / joserfc JWT / Fernet `encrypt_secret`/`decrypt_secret`),
  `app/core/telemetry.py` (structlog + correlation ids + optional
  Logfire), `app/main.py` (app factory, CORS, lifespan, explicit router stubs, `/health`).
- `migrations/env.py` bound to `Base.metadata`; `infra/docker/docker-compose.yml` (postgres + backend +
  worker) with the **shell-env override note baked in as a comment**; deps via uv (fastapi,
  sqlalchemy[asyncio], asyncpg, pydantic-settings, alembic, httpx, argon2-cffi, joserfc, cryptography,
  structlog, pytest, ruff). Python 3.12.
- **Test:** app imports; `/health` 200; `alembic upgrade head` on empty DB; `docker compose up` boots
  via the `env -u …` workaround.

## B2 — Auth + workspace vertical slice (UUID + workspace-scoped) [after B1]
- Models `User`, `Workspace`, `WorkspaceMember` (UUID PKs) exported via `app/models/__init__.py`;
  migration. Schemas + services `app/domain/{auth,workspaces}/`; JWT in a secure HttpOnly cookie;
  `require_workspace_member` dependency used by every downstream query. Routers `app/api/{auth,
  workspaces}.py`.
- **Test:** register/login sets cookie; workspace auto-created on first login; cross-workspace access →
  403/404; unit test for the workspace-auth dependency.

## B3 — Projects/brand + prompts vertical slice [after B2]
- Models `Project` (workspace-scoped), **normalized brand identity** (B-1): `Brand`, `BrandAlias`,
  `Competitor`, `OwnedDomain`, `UnintendedDomain`; **dedicated prompt resource** (Q3=A):
  `PromptSet`, `Prompt` (text, theme, intent, branded, enabled, origin, generation_evidence JSONB);
  migration. Port + adapt reference `_normalize_prompts` / `_normalize_benchmark_mode` and the prompt-
  intent config. A serialization shim rebuilds the plain dict `ScoringConfig.from_project` expects.
- Schemas (adapt `AiVisibilityProjectCreate/Update`, `PromptInput`, `CompetitorInput` → UUID/workspace);
  services `app/domain/{projects,prompts}/`; routers `app/api/{projects,prompts}.py` incl. `/prompt-sets`.
  **CSV import IS in MVP** — bulk-create prompts via the prompt resource (parsed in the browser at F7,
  persisted through the normal create/`/prompt-sets/{id}/import` path). Only **`/prompt-sets/{id}/generate`
  (AI-suggested prompts) is a stub** that returns not-implemented in MVP (B-4).
- **Test:** project CRUD persists normalized brand identity + prompts, workspace-scoped; prompt-intent +
  benchmark_mode validation; component test adapted from `tests/component/test_ai_visibility_api.py`.

## B4 — BYOK provider settings + adapters [after B2]
- Models `ProviderConnection` (workspace-scoped, Fernet secret, UUID), `ProviderRoute` (logical_engine +
  transport_provider + transport_model + is_default), `ProviderConnectionTest` (append-only),
  `DiscoveryModelConfig` (plumbing-only, B-4); migration. `app/core/config/provider_catalog.py` (approved
  transports/models + guardrail knobs, adapted from `config/ai_visibility.py`).
- Port adapters into `app/connectors/answer_engines/{contracts,gemini,gemini_parser,anthropic,
  anthropic_parser,openrouter,openrouter_parser}.py`. **MVP engine transports (B-3):** `gemini` direct
  (transport `google`) or via OpenRouter; `claude` direct (transport `anthropic`) or via OpenRouter;
  `chatgpt` **via OpenRouter only** at MVP. A **direct OpenAI adapter is an explicit fast-follow, NOT in
  MVP and not covered by MVP tests.** Key resolution reads the decrypted `ProviderConnection`, never env,
  never persisted into snapshots/logs.
- Routers `app/api/provider_connections.py` incl. `POST /provider-connections/{id}/test` (mirror
  `llm.py:test-connection`); `GET /provider-catalog`.
- **Test:** secret encrypted at rest + never in any Response DTO/log (assert redaction); `test` returns
  status; adapter unit tests for **all MVP engines** — Gemini direct (`google` transport, grounding +
  citation parsing), Claude direct (`anthropic`), and OpenRouter (chatgpt + claude) — adapted from
  `tests/unit/test_{anthropic,openrouter}_ai_visibility.py` plus new Gemini-direct coverage; each asserts
  the recorded `logical_engine` + `transport_provider` + `transport_model` provenance.

## B5 — Postgres-queue audit execution + worker + state machine [after B3, B4]
- Models `Audit`, `AuditPromptSnapshot`, `AuditEngineSnapshot`, `AuditTask` (queue+lease fields:
  `lease_owner`, `lease_expires_at`, `heartbeat_at`, `attempt_count`, `max_attempts`, `idempotency_key`
  unique, unique `(audit_id, prompt_index, repetition)`), `ProviderAttempt`, `RawResponseArtifact`,
  `AuditEvent` (all UUID); migration. State machine `app/orchestration/audit_state.py` adapting
  `crawl_domain.py` transition table to `DRAFT→VALIDATING→QUEUED→RUNNING→ANALYZING→REPORTING→COMPLETED`
  + `PARTIALLY_COMPLETED`/`FAILED`/`CANCELLED`.
- Queue `app/orchestration/task_queue.py` (`TaskQueue` Protocol) + `postgres_task_queue.py`
  (`FOR UPDATE SKIP LOCKED` claim, heartbeat, succeed/retry/fail/cancel, `release_expired` sweeper).
  Commit the claim **before** any network I/O.
- Planner `app/domain/audits/planner.py`: freeze prompt/engine/scoring snapshots; generate slots;
  **deterministic shuffle from the stored 64-bit seed**; enqueue one `AuditTask` per slot with an
  idempotency key (adapt `service.create_run`). Cooperative `cancel_run` semantics (adapt
  `service.cancel_run`).
- Worker `app/workers/audit_worker.py`: **separate process**; claims tasks; runs the adapter with
  pacing + `max_call_seconds` ceiling + bounded retries + `max_run_seconds` deadline; persists
  `RawResponseArtifact` + `ProviderAttempt`; heartbeats; drives state transitions; **cooperative cancel
  at the boundary** (adapt `runner.py`).
- Routers `app/api/audits.py`: `POST /audits`, `GET /audits`, `GET /audits/{id}`,
  `POST /audits/{id}/cancel`, `GET /audits/{id}/events` (SSE), `GET /audits/{id}/executions`.
- **Test:** planner reproduces the shuffle for a fixed seed; two concurrent workers never double-claim
  (SKIP LOCKED); sweeper recovers an expired lease; cancel stops at the boundary + terminalizes
  executions; state-transition unit tests (valid + invalid raise); component tests adapted from
  `tests/component/test_ai_visibility_{runner,run_planner}.py` + `tests/unit/test_ai_visibility_
  {retry,guardrails}.py`.

## B6 — Deterministic analysis + metrics + dashboard endpoint + exports [after B5]
- Port `app/analysis/{normalization,scoring}.py` (logic unchanged). Models `ResponseAnalysis`,
  `BrandMention`, `CompetitorMention`, `Citation`, `MetricSnapshot`; migration. Wire the worker
  `ANALYZING→REPORTING` to score each execution on persist and aggregate `MetricSnapshot` at finalize
  (adapt `aggregate_run`/`_finalize_run`); resolve `COMPLETED`/`PARTIALLY_COMPLETED`/`FAILED`.
- **Port reference metrics as-is** (mention/citation/fanout/SOV/stability) into normalized rows;
  **sentiment + avg position are NOT computed** — expose them as nullable/absent and mark roadmap (B-2).
- **Dashboard/metrics endpoint** (Q4=A): `GET /audits/{id}/metrics` (single-run `MetricSnapshot`
  projection) **and** a project-level `GET /projects/{id}/visibility?audit_id=<id>` returning the
  **selected-run** dashboard projection — Visibility Score, **per-engine comparison** for that run, and
  the **brand-vs-competitor rankings table** — computed server-side from persisted analysis (Visibility%
  + SOV populated; sentiment + avg-position fields present but null until the roadmap adds them).
  **No cross-run trend history at MVP** (that is roadmap); defaults to the project's latest completed
  audit when `audit_id` is omitted. `GET /executions/{id}` single-execution evidence. Exports
  `app/analysis/exports.py` + `GET /audits/{id}/export.{csv,md}` (adapt `exports.py`).
- **Test:** scoring parity with reference fixtures (adapt `tests/unit/test_ai_visibility_scoring.py`);
  citation classification (owned/competitor/unintended/third-party); aggregate matches per-execution
  signals; metrics + `/visibility` endpoints are projections (no provider calls); CSV/MD exports
  download; provenance columns populated.

### Backend integration verification
Per-task tests above cover each slice; the end-to-end full-stack loop + both operational gotchas are
verified by task **V1** (below).

### Full backend API surface (all `/api/v1`, workspace-scoped)
```
auth/register|login|logout|me
workspaces (GET, POST)
projects (GET, POST, GET/{id}, PATCH/{id}, DELETE/{id})
projects/{id}/visibility?audit_id=       # selected-run dashboard projection + rankings (B6, Q4=A)
prompt-sets, prompts (CRUD; /prompt-sets/{id}/import = MVP CSV bulk-create; /generate = AI-suggest stub)  # B3, Q3=A
provider-connections (GET, POST, PATCH/{id}, DELETE/{id}, POST/{id}/test)  # B4, Q2=A
provider-catalog (GET)
audits (POST, GET, GET/{id}, POST/{id}/cancel, GET/{id}/events SSE, GET/{id}/executions)
audits/{id}/metrics, executions/{id}, audits/{id}/export.{csv,md}
```

---

# Frontend (Next.js App Router, TypeScript, Vercel)

Stack: Next.js App Router + TS; TanStack Query v5; Tailwind v4 semantic tokens (light/dark); zod;
react-hook-form; Radix + lucide. Frontend conventions (typed API client with
`ApiError`+request-id+abort, per-domain endpoint modules with zod `strictValidate`, `queryKeys` module,
React Query retry policy, CVA primitives, single-`globals.css` token bridge, `data-theme` toggle) built on
App Router, not Vite. **All zod `id` fields are string UUIDs; the client consumes the workspace-scoped
`/api/v1` contract from B2–B6 — no int-id / `user_id` / msw-only fallback.**

## F1 — App scaffold + design tokens + theme [after D1]
Implements `app/globals.css` from the token values already committed in D1's `docs/design.md` (D1 wrote
`frontend-architecture.md` + `design.md`; F1 turns them into code — no doc-writing here).
- Scaffold `frontend/`: App Router + TS, Tailwind v4, Vitest + Testing Library + jsdom + msw, Playwright,
  `cn()`, `next/font` sans+mono, root `layout.tsx` with **pre-hydration `data-theme` bootstrap** +
  `<QueryProvider>`. Author `app/globals.css` as the single token source (light `:root` + dark
  `html[data-theme='dark']`, `@theme inline` bridge, semantic status + sentiment + citation-
  classification + run-status + score-band tokens, motion/forced-colors/print rules) — **values taken
  verbatim from `docs/design.md`**. `theme-toggle` primitive. Architecture-guard scripts (line budgets,
  required API owners, token-escape / no-raw-hex).
- **Test:** theme toggle sets/persists `data-theme`; token-escape guard passes on `globals.css`;
  `next build` succeeds; the `globals.css` token set matches `docs/design.md`.

## F2 — API contract layer + same-origin proxy [after F1]
- `lib/api/client.ts` (transport: `ApiError`, `X-Request-ID`, abort, `credentials:'include'`,
  `cache:'no-store'`, bounded network retry for GET/idempotent, JSON enforcement, **relative base**),
  `errors.ts`, `query-client.ts` (`shouldRetryQuery` 408/429/5xx/network max 2, staleTime 15s,
  `refetchOnWindowFocus:false`), `query-keys.ts` (auth/workspaces/projects/prompts/providers/runs/
  visibility namespaces), `schemas.ts` + `types.ts` (zod, all ids **string UUID**; §schemas below),
  per-domain modules `auth|projects|prompts|providers|runs|visibility.ts` calling client +
  `strictValidate`, `index.ts` compat facade (spreads modules; owns no transport).
- `next.config.ts` `rewrites()` same-origin `/api/:path*` → `BACKEND_ORIGIN` (server-only). Wire
  `<QueryProvider>`.
- **Test:** client throws `ApiError` on 4xx/5xx with request-id, retries GET on 5xx not 4xx, aborts via
  signal; `strictValidate` throws on mismatch; `shouldRetryQuery` matrix; guard: required API owners
  exist, `index.ts` owns no transport.

## F3 — UI primitives library [after F1]
CVA token-driven primitives in `components/ui/*`: `button`(+variants), `badge` (status/sentiment/
classification variants), `card`, `table` (compact dense: sticky header, row/header heights),
`input`+`field`, `dialog`, `dropdown`, `tooltip`, `skeleton`, `typography`, `alert`, `history-drawer`,
plus data-viz `score-ring` + `donut` (MVP: score ring + per-engine/citation-share donut). A `trend-chart`
primitive is **built but unused in MVP UI** (cross-run trend is roadmap) — kept in the library so the
roadmap trend view has it ready; not wired into any MVP screen. Radix where relevant, lucide icons,
bridged tokens only (no raw hex).
- **Test:** button variant/size + `asChild`; badge maps status→token class; table header/rows;
  score-ring/donut render with ARIA labels (trend-chart has a render+ARIA unit test but no MVP-screen
  wiring); token-escape guard passes on `components/`.

## F4 — Auth pages + session guard [after F2, F3]
`(auth)` layout + `/login` + `/register` (react-hook-form + zod, inline `ApiError`), `auth.ts`
mutations (login/register/me/logout), `session-guard.tsx` (user context; unauth → `/login`; 401 →
clear+redirect). `(app)/page.tsx` redirects to `/visibility` or `/setup` when no project.
- **Test:** login validation + success redirect + error surfacing (msw); guard redirects unauthenticated;
  401 clears session.

## F5 — App shell + nav + project switcher [after F2, F3, F4]
`(app)/layout.tsx` composing `app-shell` = `sidebar-nav` (grouped Analytics/Prompts/Actions/On Page;
MVP-live = Visibility + Your Prompts + Runs + Providers + Setup; all other items disabled "soon") +
`top-bar` (search placeholder, Export hook, Learn link, `project-switcher`, theme toggle, `user-menu`) +
Getting-Started card. Project context + `projects.ts` list/get.
- **Test:** nav renders live vs disabled items; project switcher changes active project; app-shell within
  line budget. Playwright: login → shell renders.

## F6 — Brand/Project setup [after F5, B3]
`/setup` form (brand name, project name, website, `country_code`/`language_code`, aliases, owned domains,
unintended domains, repeatable competitor rows [name/aliases/domains], `benchmark_mode`, default
repetitions) with create + edit via `projects.ts`; create sets the active project and routes to
`/visibility`. react-hook-form + zod. **Depends on B3** (project + normalized brand + prompt resource).
- **Test:** form validates all fields incl. competitor/domain rows; create → active project + route; edit
  prefills + patches.

## F7 — Prompt library [after F6, B3]
`/prompts` — prompt table (text/theme/intent/branded/enabled) with add/edit/delete, enable/disable,
filter/search, **manual entry** and **CSV import** (parsed in-browser, persisted via `/prompt-sets`).
The **AI-suggest** panel renders against the B3 `/generate` stub and shows a "coming soon" state (B-4).
Uses `prompts.ts`. **Depends on B3** (`/prompt-sets`).
- **Test:** table CRUD + enable/disable + filter; CSV import parses + previews + persists; AI-suggest
  panel renders its not-yet-enabled state; empty state.

## F8 — BYOK Provider Settings [after F5, B4]
`/providers` — per-engine cards for all three engines: **Gemini** and **Claude** offer a direct/OpenRouter
route toggle; **ChatGPT** offers the **OpenRouter route only** at MVP with a disabled "direct OpenAI —
coming soon" option (B-3). Each card: API-key entry, **connection test**, and `configured` status; plus
a separate discovery/analysis model selection (plumbing). Uses `providers.ts` against
`/provider-connections` + `/test` + `/provider-catalog`. **Depends on B4.**
- **Test:** cards render all three engines; Gemini/Claude route toggle works and ChatGPT is OpenRouter-
  only with the direct option disabled; key entry submits; test surfaces success/error; unconfigured
  state.

## F9 — Visibility dashboard [after F5, B6]
`/visibility` — a **selected-run** projection: Visibility Score header, **per-engine comparison** for the
selected run, and a **Rankings table** (brand vs competitors: Visibility% / SOV / Sentiment / Avg
Position), with a **run selector** (defaults to the latest completed audit) + engine/prompt-type filters.
**No cross-run trend chart or date/run-range history at MVP** (roadmap). Consumes the B6
`GET /projects/{id}/visibility?audit_id=` endpoint. Sentiment + avg-position columns render but show "—"
until the roadmap computes them (B-2). **Depends on B6.**
- **Test:** score + per-engine comparison render from data; rankings table sorts brand+competitors; run
  selector + filters change query keys; empty (no runs) state; sentiment/avg-position render the
  not-yet-computed placeholder.

## F10 — Run/Executions evidence explorer [after F6, F8, B5, B6]
`/runs` (list + launch dialog: select prompts + engines/providers + repetitions → `POST /audits`),
`/runs/[runId]` (progress panel: requested/completed/failed + status + **cancel** via
`POST /audits/{id}/cancel`; executions table; **polling while active**, SSE via `/events` optional;
CSV/MD export links), `/runs/[runId]/executions/[executionId]` evidence card (answer text, `search_used`,
citations classified owned/competitor/third-party, brand & competitor mentions, per-response `score`
dict; sentiment shows placeholder). Uses `runs.ts`. **Depends on B5** (audit queue + SSE) **and B6**
(analysis/exports).
- **Test:** launch dialog builds payload; progress panel reflects counts/status; cancel mutation;
  executions table renders; evidence card renders answer/grounding/classified citations/mentions/score;
  export links resolve. Playwright: shell → Visibility → open run → open execution.

### Frontend zod schemas (all ids string UUID, workspace-scoped)
`sessionUserSchema {id,email,role,is_active,created_at,updated_at}`;
`competitorSchema {id,name,aliases[],domains[]}`;
`promptSchema {id,prompt_set_id,text,theme,intent,branded,enabled,origin}`;
`projectSchema {id,workspace_id,name,brand_name,website_url,country_code,language_code,benchmark_mode,
default_repetitions,brand{aliases[]},owned_domains[],unintended_domains[],competitors[],prompt_sets[],
created_at,updated_at}` (benchmark_mode enum);
`providerConnectionSchema {id,workspace_id,transport_provider,base_url,active,...}` (secret never
present); `providerRouteSchema {id,logical_engine,transport_provider,transport_model,is_default}`;
`providerCatalogSchema`; `auditSchema {id,workspace_id,project_id,status,random_seed,configuration,
summary,requested_count,completed_count,failed_count,error_message,created_at,updated_at,completed_at}`;
`executionSchema {id,audit_id,prompt_index,repetition,randomized_position,status,answer_text,search_used,
search_events[],citations[],score,provider_metadata,error_code,error_message,latency_ms}`;
`citationSchema {ordinal,url,title,domain,cited_text,classification}` (owned|competitor|third_party);
`visibilitySchema` (selected-run: score + per-engine comparison + rankings rows; sentiment/avg_position
nullable). **Every `id` and `*_id` field is `z.string().uuid()`; no numeric ids; no `user_id`.** All
validated via `strictValidate`.

### Frontend integration verification
Per-task tests above cover each screen; `next build` + lint + architecture-guard scripts + full Vitest
suite + Playwright smoke run in task **V1** (below), which also verifies the same-origin proxy and both
themes against `design.md` (no visual reference needed).

---

## V1 — Full-stack MVP verification [after B6, F10]
Explicit final task closing the dependency graph (subsumes the two integration-verification sections
above). Stand up Postgres + FastAPI web + the separate worker + the Next.js dev server together and:
- Run the full loop **through the UI**: register → workspace auto-created → create project (brand +
  competitors + domains + benchmark_mode) → add prompts (manual + CSV import) → configure a BYOK
  provider connection + connection-test → launch a **multi-engine** audit (provider calls mocked/stubbed
  so no real spend) → worker claims via `FOR UPDATE SKIP LOCKED` and executes → run reaches
  COMPLETED/PARTIALLY_COMPLETED → Visibility dashboard (selected-run projection + rankings) +
  Run/Executions evidence + `/audits/{id}/export.{csv,md}` all populated, reproducible for a fixed seed.
- **Verify gotcha 1:** `alembic upgrade head` from empty + `docker compose up` boots via the
  `env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL … ` workaround.
- **Verify gotcha 2 in a real browser:** all frontend network calls hit `/api/*` (relative, same-origin
  via `rewrites()`), never a cross-origin backend URL, and no duplicate `Access-Control-Allow-Origin`
  header (curl cannot reproduce this — use the browser).
- No secret in any Response DTO or log; brand list never in `request_snapshot`. `ruff check .` clean;
  full pytest + Vitest suites green; Playwright smoke green.

## Cross-layer ordering summary
**D1 (docs) is first and precedes all implementation:** `D1 → B1` and `D1 → F1`.
Backend: `B1 → B2 → {B3, B4} → B5 → B6` (`B1 [after D1]`).
Frontend: `F1 [after D1]` (∥ B1) → `{F2, F3} → F4 → F5`; then `F6 [after F5,B3]`, `F7 [after F6,B3]`,
`F8 [after F5,B4]`, `F9 [after F5,B6]`, `F10 [after F6,F8,B5,B6]`.
Final: `V1 [after B6, F10]`.

## Out of scope (documented as roadmap, not coded)
Site Health + Issues (simple HTTP/Screaming-Frog crawler, no browser), open-ended Issue catalog, LLM
Analytics, Traffic, Content, Opportunities, Competitors/E-E-A-T, Topics, GSC/GA4/Bing integrations,
Agent, MCP, Settings/white-labelling, HTML/JSON renderers, S3 artifacts, Redis queue, sentiment +
avg-position computation, AI prompt-suggestion + LLM adjudication, direct OpenAI adapter (fast-follow).

# Searchify

<p align="center">
  <strong>Own how your brand appears in AI answers — and strengthen the pages those answers rely on.</strong>
</p>

<p align="center">
  Searchify is an open-source AI visibility and site intelligence platform for measuring brand presence across answer engines, inspecting the evidence behind every result, and improving on-page AEO readiness.
</p>

<p align="center">
  <a href="https://github.com/abhij1306/Searchify/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/abhij1306/Searchify?style=flat-square"></a>
  <a href="https://github.com/abhij1306/Searchify/issues"><img alt="GitHub issues" src="https://img.shields.io/github/issues/abhij1306/Searchify?style=flat-square"></a>
  <a href="https://github.com/abhij1306/Searchify/blob/main/LICENSE"><img alt="MIT License" src="https://img.shields.io/github/license/abhij1306/Searchify?style=flat-square"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white&style=flat-square">
  <img alt="Next.js 15" src="https://img.shields.io/badge/Next.js-15-000000?logo=nextdotjs&logoColor=white&style=flat-square">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-15%2B-4169E1?logo=postgresql&logoColor=white&style=flat-square">
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white&style=flat-square">
</p>

<p align="center">
  <code>AEO</code> · <code>GEO</code> · <code>AI Visibility</code> · <code>Brand Monitoring</code> · <code>Site Health</code> · <code>Technical SEO</code> · <code>Open Source</code>
</p>

---

## What Searchify does

Searchify connects two workflows that are usually fragmented across separate tools:

1. **Measure AI visibility.** Run repeatable audits across ChatGPT, Claude, and Gemini using your own provider keys. Compare your brand with competitors, track visibility and share of voice over time, and inspect persisted mention, citation, and query-fanout evidence.
2. **Improve answer readiness.** Crawl your site with a first-party, security-bounded HTTP crawler. Choose the URLs that matter, score Technical and AEO health, investigate grouped issues, and drill into evidence and remediation for each page.

Every report is built from persisted, versioned evidence. Searchify does not silently re-run providers, re-fetch pages, or invent missing metrics while rendering a dashboard.

## Product highlights

### Visibility Intelligence

- **Multi-engine audits** across ChatGPT/OpenAI, Claude/Anthropic, and Gemini/Google.
- **Bring your own keys** with encrypted provider credentials and explicit transport routes.
- **Four-part visibility workspace** covering overview, trends, mentions and citations, and query-fanout evidence.
- **Competitive benchmarking** for brand mentions, citation ownership, share of voice, and rankings.
- **Cross-run trends** with engine, time-range, and granularity controls.
- **Evidence-first exploration** from a headline metric down to the exact persisted execution and source.
- **Deterministic headline scoring** with analyzer and scoring-rule versions attached to every projection.

### Site Health & AEO Auditing

- **Progressive URL discovery** through an in-house HTTP crawler with SSRF and resource-bound protections.
- **Free sample and Starter monitoring modes** with privacy-aware count disclosure and quota-controlled URL selection.
- **Technical, AEO, and combined scores** with transparent rule outcomes and no fabricated zeros.
- **Live crawl and analysis progress** with resilient polling plus credentialed SSE invalidation.
- **Grouped issue intelligence** with severity, dimension, remediation, and affected-page navigation.
- **Per-URL diagnostics** including delivery facts, normalized page facts, evidence, links, and issue history.
- **Authenticated CSV and Markdown exports** scoped to the active workspace.

### Built for trustworthy operations

- Strict workspace isolation with UUID identifiers throughout.
- Immutable artifacts and provenance-carrying analyses.
- PostgreSQL-backed durable queues using `FOR UPDATE SKIP LOCKED`, leases, retries, and idempotency.
- Same-origin frontend API proxying so backend origins and credentials stay server-side.
- Typed API contracts validated at runtime with Zod and Pydantic.
- Light and dark themes, responsive application shell, and reusable design tokens.

> Searchify currently ships Visibility Intelligence, audit evidence, provider management, Site Health, grouped Issues, and per-URL diagnostics. Additional content, traffic, topic, integration, agent, and MCP capabilities remain documented in the [roadmap](docs/roadmap/README.md).

---

## Table of contents

- [What Searchify does](#what-searchify-does)
- [Product highlights](#product-highlights)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start-docker-compose)
- [Local development](#local-development-without-docker)
- [Configuration](#configuration)
- [Database migrations](#database-migrations)
- [Testing](#testing)
- [Key concepts](#key-concepts)
- [Documentation map](#documentation-map)
- [Known gotchas](#known-gotchas)
- [Contributing](#contributing)
- [License](#license)

---

## Architecture

```text
Browser ── /api/* ──> Next.js same-origin proxy ──> FastAPI ──> PostgreSQL
                                                        │             │
                                                        │             ├─ durable tasks
                                                        │             ├─ evidence
                                                        │             └─ metrics
                                                        │
                                                        ├─ Audit worker ──> AI providers (BYOK)
                                                        └─ Site Health worker ──> public website pages
```

- **Same-origin proxying.** The browser calls relative `/api/*` routes. Next.js forwards them to the server-only `BACKEND_ORIGIN`, keeping backend topology out of the client bundle.
- **Deterministic, versioned analysis.** Headline visibility metrics and Site Health scores come from explicit rules over persisted evidence. Metrics that are not supported remain nullable and render as `—`.
- **PostgreSQL-backed orchestration.** Workers claim tasks with `FOR UPDATE SKIP LOCKED`, maintain leases and heartbeats, retry safely, and reconcile terminal state without a Redis dependency.
- **Immutable evidence and provenance.** Provider responses and crawl artifacts are written once. Derived analyses reference their sources and formula versions; dashboard reads are projections, not hidden recomputation.
- **Security boundaries by default.** Workspaces are resolved server-side, provider secrets are encrypted at rest, crawler requests are SSRF-bounded, and raw fetched HTML is never retained by Site Health.

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Backend   | Python 3.12, FastAPI, SQLAlchemy (async) + asyncpg, Pydantic v2 / pydantic-settings, Alembic, httpx, argon2-cffi, joserfc (JWT), cryptography (Fernet), structlog |
| Frontend  | Next.js (App Router) + TypeScript, Tailwind CSS v4, TanStack Query, react-hook-form + zod, Radix UI |
| Database  | PostgreSQL |
| Tooling   | `uv` (backend deps), `pnpm` (frontend), Docker Compose, Ruff, pytest / pytest-asyncio, Vitest + Testing Library + MSW, Playwright |

## Repository layout

```
Searchify/
├── Agents.md                 # Coding-agent bootstrap; unified contract + rules
├── README.md                 # This file
├── LICENSE                   # MIT
├── CONTRIBUTING.md           # Workflow, conventions, review checklist
├── backend/                  # FastAPI service (uv project)
│   └── app/
│       ├── api/              # Routers (/api/v1/*)
│       ├── core/            # config, database, security, telemetry
│       ├── models/          # SQLAlchemy models (UUID PKs)
│       ├── schemas/         # Pydantic DTOs
│       ├── domain/          # Business logic per subsystem
│       ├── connectors/      # BYOK answer-engine adapters
│       ├── orchestration/   # Audit state machine + task queue
│       ├── analysis/        # Deterministic scoring, normalization, exports
│       └── workers/         # Audit and Site Health workers
├── frontend/                 # Next.js App Router app
│   ├── app/                  # Routes (auth, app shell, screens)
│   └── lib/api/             # Typed API-contract layer (zod schemas)
├── migrations/               # Alembic migrations (hand-written; see gotcha)
├── infra/docker/             # docker-compose.yml + env template
└── docs/                     # Architecture, invariants, design, plans
    ├── DEVELOPMENT.md        # Environment setup + full gotchas runbook
    ├── backend-architecture.md
    ├── frontend-architecture.md
    ├── invariants.md         # The 12 hard rules (review-blocking)
    ├── design.md             # Design tokens, theme, per-screen layout
    └── plans/                # Approved implementation plan
```

## Prerequisites

- **Docker + Docker Compose** (for the quick start), or for bare-metal dev:
- **Python 3.12** and [`uv`](https://docs.astral.sh/uv/)
- **Node.js 22+** and **pnpm 11+**
- **PostgreSQL 15+** (only if running the backend outside Docker)

## Quick start (Docker Compose)

> **Important:** this machine (and CI-like shells) may export `POSTGRES_*` and
> `DATABASE_URL` into every shell, which Compose resolves *before* `.env`. Use the
> `env -u …` workaround verbatim — see [gotcha 1](#known-gotchas).

```bash
# 1. Copy the env template
cp infra/docker/.env.example infra/docker/.env    # then edit secrets for anything non-local

# 2. Bring the stack up (Postgres + backend web + audit worker)
env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL \
  POSTGRES_PASSWORD=searchify_dev_password \
  docker compose -f infra/docker/docker-compose.yml up -d --force-recreate

# 3. Apply migrations (from backend/)
cd backend && uv run alembic upgrade head

# 4. Start the frontend (from frontend/)
cd ../frontend
echo "BACKEND_ORIGIN=http://localhost:8000" > .env.local
pnpm install
pnpm dev            # http://localhost:3000
```

Open <http://localhost:3000>, register a user (a workspace is created automatically), then
create a project. Connect a BYOK provider to run Visibility audits, or open Site Health to
discover and analyze the project website.

## Local development (without Docker)

```bash
# Backend
cd backend
uv sync
# point DATABASE_URL at your Postgres, e.g.
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/searchify"
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000     # web API
uv run python -m app.workers.audit_worker            # audit worker (separate shell)

# Frontend
cd frontend
echo "BACKEND_ORIGIN=http://localhost:8000" > .env.local
pnpm install
pnpm dev
```

## Configuration

Backend settings are read from the environment (see `infra/docker/.env.example`):

| Variable | Purpose |
|----------|---------|
| `APP_ENV` | `development` / `test` / `production` (controls cookie `Secure`, CORS) |
| `DATABASE_URL` | Async SQLAlchemy DSN (`postgresql+asyncpg://…`) |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Compose Postgres credentials |
| `JWT_SECRET_KEY` / `JWT_ALGORITHM` / `JWT_EXPIRE_HOURS` | Session JWT (HttpOnly cookie) |
| `ENCRYPTION_KEY` | Fernet key for BYOK provider secrets at rest |
| `FRONTEND_URL` / `FRONTEND_ORIGINS` | Allowed origins |
| `LOGFIRE_ENABLED` / `LOGFIRE_TOKEN` | Optional observability |

Frontend: `BACKEND_ORIGIN` (server-only) is the backend the Next.js `rewrites()` proxy
forwards `/api/*` to. Never expose it to the browser.

## Database migrations

Migrations live in `migrations/` and are applied with Alembic:

```bash
cd backend
uv run alembic upgrade head          # apply
uv run alembic downgrade -1          # roll back one
uv run alembic check                 # should report "No new upgrade operations detected"
```

> **Migrations are hand-written.** Alembic autogenerate is disabled in this repo (the
> `script_location` layout breaks the mako template path). Write the migration by hand and
> verify with `alembic check`. See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## Testing

```bash
# Backend (from backend/) — needs a Postgres reachable via TEST_DATABASE_URL
cd backend
uv run pytest -q
uv run ruff check .

# Frontend (from frontend/)
cd frontend
pnpm test             # Vitest unit/component tests (MSW-mocked network)
pnpm check:policy     # architecture / token guards
pnpm build            # next build
pnpm exec tsc --noEmit # type check
pnpm test:e2e         # Playwright (requires a browser + running stack)
```

## Key concepts

- **Unified contract.** All ids are string UUIDs, workspace-scoped (no `user_id` scoping, no
  integer PKs). API prefix is `/api/v1`.
- **Engines vs transports.** Logical engines are `chatgpt | gemini | claude`; active
  transports are exactly `openai | anthropic | google`, with one approved route per engine
  (`chatgpt → openai → gpt-5.4`, `claude → anthropic → claude-sonnet-4-6`, `gemini → google
  → gemini-flash-latest`). ChatGPT runs through the **direct OpenAI Responses API**.
  `openrouter` is retired as an active transport and kept only as a read-only historical token
  so legacy rows still render (never an active/approved route). Every route/attempt records all
  three identities: logical engine + transport provider + exact transport model.
- **Benchmark modes.** `consumer_like | controlled_localized | forced_grounded`.
- **Prompt intents.** `discovery | comparison | purchase | service | local`.
- **BYOK security.** Provider API keys are Fernet-encrypted at rest, resolved only at
  execution time, and never returned in a DTO, logged, or sent as part of a prompt.
- **Site Health capabilities.** Site Health is capability-gated per workspace
  (`free | starter`). Free runs a deterministic, read-only **sample** crawl and never
  discloses the discovered/full-site total; Starter runs the full progressive inventory and
  lets the user pick a monitored URL set (quota-limited) that is analyzed and dashboarded.
  See [`docs/site-health.md`](docs/site-health.md).

## Documentation map

| Doc | What it covers |
|-----|----------------|
| [`Agents.md`](Agents.md) | Coding-agent bootstrap, unified contract, always-on rules |
| [`docs/architecture.md`](docs/architecture.md) | Whole-product architecture (authoritative high-level reference) |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Environment setup + full gotchas runbook |
| [`docs/backend-architecture.md`](docs/backend-architecture.md) | API, models, queue, state machine, analysis |
| [`docs/frontend-architecture.md`](docs/frontend-architecture.md) | Routes, API-contract layer, data flow |
| [`docs/invariants.md`](docs/invariants.md) | The 12 hard rules (review-blocking) |
| [`docs/design.md`](docs/design.md) | Design tokens, theme, per-screen layout |
| [`docs/site-health.md`](docs/site-health.md) | Site Health: entitlements, statuses, API endpoints, routes, exports |
| [`docs/plans/`](docs/plans/) | Historical implementation plans and task graphs |
| [`docs/roadmap/`](docs/roadmap/) | Shipped design records and specifications for future product surfaces |

## Known gotchas

Two environment-specific gotchas will bite you if you don't know them (full runbooks in
[`docs/invariants.md`](docs/invariants.md) §11–12 and [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)):

1. **Shell secrets override Docker Compose `${VAR}`.** The shell exports
   `POSTGRES_*`/`DATABASE_URL`; Compose interpolates `${VAR}` from the shell before `.env`,
   so the stack boots with the wrong credentials. Fix: unset them for the Compose invocation
   with `env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL POSTGRES_PASSWORD=<value> docker compose …`.
2. **Tunnel double CORS header.** The preview/tunnel proxy injects its own
   `Access-Control-Allow-Origin: *`; a backend that also sets ACAO yields two ACAO headers
   browsers reject. `curl` cannot reproduce it. Fix: the browser only calls relative
   `/api/*`, proxied same-origin via Next.js `rewrites()`. **Always test this in a real
   browser, not curl.**

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch/commit conventions, the review
checklist, and how the 12 invariants gate a change.

## License

Released under the [MIT License](LICENSE).

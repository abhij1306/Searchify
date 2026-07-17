# Searchify

> **AEO / AI-visibility SaaS** — measure how your brand appears inside answer-engine
> responses (ChatGPT, Gemini, Claude), score every response deterministically, and see the
> result in a Visibility dashboard with a full run/executions evidence trail.

Searchify lets a workspace define a **brand + competitors + prompts**, run a one-time
**audit** (prompt × engine × repetition executions across bring-your-own-key answer
engines), score each response **deterministically** (mentions, citations, share-of-voice),
and surface the outcome in a **Visibility dashboard** and a **Run / Executions evidence
explorer**.

This repository is the **MVP visibility slice** built on the full target architecture from
day one — workspaces, workspace-scoped auth, UUID primary keys everywhere, BYOK provider
settings, a Postgres `FOR UPDATE SKIP LOCKED` task queue (no Redis), and a complete audit
state machine. The **Site Health** surface (an in-house HTTP crawler + on-page/AEO
analysis with a discovery → selection → analysis → dashboard → issues flow) is also
implemented — see [`docs/site-health.md`](docs/site-health.md). The remaining AEO surfaces
(LLM Analytics, Traffic, Content, Topics, integrations, Agent, MCP) are documented as
**roadmap** and not yet coded.

---

## Table of contents

- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Quick start (Docker Compose)](#quick-start-docker-compose)
- [Local development (without Docker)](#local-development-without-docker)
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

```
Browser ──/api/:path*── Next.js (rewrites, same-origin proxy) ──> FastAPI backend ──> Postgres
                                                                        │
                                                        Postgres task queue (SKIP LOCKED)
                                                                        │
                                                                  Audit worker ──> Answer engines
                                                                                   (BYOK: Anthropic,
                                                                                    Google, OpenRouter)
```

- **Same-origin proxying.** The browser only ever calls relative `/api/*`; Next.js
  `rewrites()` forward to the server-only `BACKEND_ORIGIN`. The browser never sees a
  cross-origin backend URL (see [gotcha 2](#known-gotchas)).
- **Deterministic scoring.** Headline metrics (mentions, citations, share-of-voice) are
  computed with deterministic alias/domain matching — **no LLM is used for headline
  metrics**. Sentiment and average-position require contextual judgement and are therefore
  **not computed at MVP** (present in the schema as nullable, rendered as `—`).
- **Postgres-backed queue.** Audit tasks are claimed with `FOR UPDATE SKIP LOCKED`, leased
  with heartbeats, and swept when leases expire. No Redis. Orchestration depends on a
  `TaskQueue` protocol so a future Redis implementation needs no domain rewrite.
- **Immutable, provenance-carrying data.** Raw response artifacts are written once; every
  derived analysis/metric row references the artifact it was computed from plus the
  analyzer/formula version. Reports are pure projections — they never re-call a provider.

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
│       └── workers/         # Audit worker
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
set up a project, add prompts, connect a BYOK provider, and launch an audit.

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
- **Engines vs transports.** Logical engines are `chatgpt | gemini | claude`; MVP transports
  are `anthropic | google | openrouter`. Direct `openai` is reserved (disabled at MVP);
  `chatgpt` reaches MVP via OpenRouter. Every route/attempt records all three identities:
  logical engine + transport provider + exact transport model.
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
| [`docs/plans/`](docs/plans/) | Approved implementation plan + task graph |
| [`docs/roadmap/`](docs/roadmap/) | Design specs for roadmap surfaces (e.g. the [Technical Audit crawler](docs/roadmap/technical-audit.md)) |

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

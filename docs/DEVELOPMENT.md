# Development guide — Searchify

Everything you need to run, test, and troubleshoot Searchify locally, plus the two
environment gotchas that will otherwise cost you an afternoon. Pair this with
[`../Agents.md`](../Agents.md) (contract + rules) and [`invariants.md`](invariants.md)
(the review-blocking hard rules, including the canonical gotcha runbooks §11–12).

## Toolchain

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.12+ | Backend |
| [`uv`](https://docs.astral.sh/uv/) | latest | Backend dependency + venv manager |
| Node.js | 22+ | Frontend |
| pnpm | 11.9+ | Frontend package manager; pinned in `frontend/package.json` |
| PostgreSQL | 15+ | Via Docker or local |
| Docker + Compose | latest | Local stack |

## Backend setup

```bash
cd backend
uv sync                     # creates backend/.venv and installs deps from uv.lock
export DATABASE_URL="postgresql+asyncpg://postgres:<password>@localhost:5432/searchify"
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

Run the audit worker in a **separate** process (it claims and executes queued audit tasks):

```bash
cd backend
uv run python -m app.workers.audit_worker
```

The web API and the worker are separate processes on purpose — the web process never
executes provider calls; it only enqueues tasks (invariant 8).

## Frontend setup

```bash
cd frontend
echo "BACKEND_ORIGIN=http://localhost:8000" > .env.local
pnpm install
pnpm dev                    # http://localhost:3000
```

`BACKEND_ORIGIN` is **server-only**. The browser calls relative `/api/*`; Next.js
`rewrites()` proxy those to `BACKEND_ORIGIN` (see gotcha 2 below).

## Running the full stack with Docker Compose

```bash
cp infra/docker/.env.example infra/docker/.env

# Use the env -u workaround (gotcha 1) — verbatim:
env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL \
  POSTGRES_PASSWORD=searchify_dev_password \
  docker compose -f infra/docker/docker-compose.yml up -d --force-recreate

cd backend && uv run alembic upgrade head
```

The compose file defines three services: `db` (Postgres), `web` (FastAPI), `worker` (audit
worker). See `infra/docker/README.md` for details.

## Testing

### Backend

Backend tests use a real Postgres (each test runs against an isolated schema). Point
`TEST_DATABASE_URL` at a reachable Postgres:

```bash
cd backend
export TEST_DATABASE_URL="postgresql+asyncpg://postgres:<password>@localhost:5432/test_db"
uv run pytest -q
uv run ruff check .
```

A quick disposable Postgres for tests:

```bash
docker run -d --name searchify_pg -p 55432:5432 \
  -e POSTGRES_PASSWORD=searchify_dev_password -e POSTGRES_DB=test_db \
  postgres:16
export TEST_DATABASE_URL="postgresql+asyncpg://postgres:searchify_dev_password@localhost:55432/test_db"
```

### Frontend

```bash
cd frontend
pnpm test             # Vitest (network mocked with MSW)
pnpm check:policy     # architecture + design-token guards
pnpm exec tsc --noEmit # type check
pnpm build            # next build
pnpm test:e2e         # Playwright (needs a browser + a running stack)
```

## Migrations (hand-written)

**Alembic autogenerate is disabled in this repo.** The `script_location` layout makes the
mako template path "relative outside root", so autogenerate fails. Write every migration by
hand, keep it in the numbered chain, and verify:

```bash
cd backend
uv run alembic upgrade head          # applies cleanly on a fresh DB
uv run alembic downgrade -1          # roll back one revision
uv run alembic check                 # must report "No new upgrade operations detected"
```

> `alembic check` may print one `SAWarning` about the intentional
> `audit_tasks` / `raw_response_artifacts` FK cycle (which now also lists the
> `site_crawl_tasks` / `site_fetch_artifacts` cycle) — that is expected, not an error.

Migration chain (as of the MVP):
`0001_initial_empty → 0002_auth_workspace → 0003_projects_prompts → 0004_provider_settings →
0005_audit_queue → 0006_analysis_metrics → 0007_snapshot_provenance → 0008_site_health`.

Keep new `alembic_version` revision ids short — the column is `varchar(32)`.

---

## Site Health entitlements (development)

Site Health entitlements are **capability-based** (`free` / `starter`), stored one row
per workspace in `workspace_site_health_entitlements`. There is no billing-provider
integration in this codebase — production billing may call the same domain service
(`app.domain.site_health.entitlements.set_entitlement`) later.

A workspace with no explicit entitlement resolves to **Free** (fail-closed to the most
restrictive capability). To grant a workspace the **Starter** capability locally (or reset
it to Free), use the operator/dev command, run from `backend/` with `DATABASE_URL` pointing
at the target database:

```bash
cd backend
uv run python -m scripts.set_site_health_entitlement <workspace_uuid> starter
uv run python -m scripts.set_site_health_entitlement <workspace_uuid> free
```

The command freezes the resolved capability profile (discovery mode, discovery/sample
caps, monitored-URL limit, count-disclosure flag) onto the row, bumps
`capability_revision`, and emits a single audit-safe log line recording the change (no
secrets).

---

## Gotchas runbook

These two are environment-specific and will silently break the stack. The canonical
versions live in [`invariants.md`](invariants.md) §11–12.

### Gotcha 1 — shell secrets override Docker Compose `${VAR}`

**Symptom:** `docker compose up` connects Postgres/backend with the wrong
credentials/database even though `.env` looks correct.

**Cause:** this machine exports `POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB`, and
`DATABASE_URL` into **every shell**. Compose resolves `${VAR}` in `docker-compose.yml` from
the **shell environment before `.env`** (`env_file:` only injects vars *inside* the
container, not into `${VAR}` interpolation). The shell values win and silently override the
repo values.

**Workaround (verbatim):**

```bash
env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL \
  POSTGRES_PASSWORD=<repo-.env-value> \
  docker compose -f infra/docker/docker-compose.yml up -d --force-recreate
```

Unset the four inherited vars for the Compose invocation and re-supply the repo `.env` value
explicitly. `docker-compose.yml` carries this note as a baked-in comment.

There is also **no local Postgres listening** on this machine by default — the exported
`DATABASE_URL`/`POSTGRES_*` point at `localhost:5432` but nothing listens there. Use a Docker
Postgres container for all migration/integration work.

### Gotcha 2 — tunnel double CORS header → same-origin rewrites

**Symptom:** frontend network calls fail in the browser with a CORS error about **duplicate**
`Access-Control-Allow-Origin` headers — but `curl` against the same backend succeeds.

**Cause:** the preview/tunnel proxy injects its own `Access-Control-Allow-Origin: *`. A
FastAPI backend that also sets a specific ACAO (required when `allow_credentials=True`)
produces **two** ACAO headers, which browsers reject. `curl` does not enforce CORS, so it
cannot reproduce the failure.

**Fix:** the browser never talks cross-origin to the backend. Next.js `rewrites()` proxy
`/api/:path*` → the server-only `BACKEND_ORIGIN`, so all browser calls are **same-origin**
(`/api/...` relative). The API client uses a relative base (`/api/v1`), `cache: 'no-store'`,
and `credentials: 'include'`.

```ts
// frontend/next.config.ts
async rewrites() {
  return [{ source: '/api/:path*', destination: `${process.env.BACKEND_ORIGIN}/api/:path*` }];
}
```

**Always test this in a real browser, not curl.**

## Web preview (running the stack behind a tunnel)

When previewing the app behind a tunnel/proxy:

1. Point the frontend's `BACKEND_ORIGIN` at the running backend.
2. Ensure the dev server accepts the proxied host (Next.js `allowedDevOrigins` / equivalent
   blocked-host config) so the preview host isn't rejected.
3. Confirm every browser network call hits relative `/api/*` (same-origin) — not a
   cross-origin backend URL. This is what avoids the gotcha-2 double-CORS failure.

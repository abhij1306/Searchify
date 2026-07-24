> NOTE (v1 integrations branch): this is the MVP-era base-stack doc kept for context. For the integrations/Traffic/LLM-Analytics stack, follow `README.md` in this directory and `docs/integrations-traffic-analytics.md` — they supersede the details below.

# Searchify -- Setup & Seed Instructions

Repo: `abhij1306/Searchify` at `/code/abhij1306/Searchify` (branch `main`).
Stack: FastAPI backend + background audit worker (`/backend`), Next.js frontend
(`/frontend`), Postgres 16 (`/infra/docker`).

## Prerequisites

1. **Postgres 16** running via docker compose, with `infra/docker/.env` present
   (copy from `.env.example` if missing). Key vars: `POSTGRES_DB=searchify`,
   `POSTGRES_USER=postgres`, `POSTGRES_PASSWORD=searchify_dev_password`.

   ```bash
   cd /code/abhij1306/Searchify/infra/docker
   [ -f .env ] || cp .env.example .env
   env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL \
     docker compose up -d db
   ```

   GOTCHA: if `POSTGRES_*`/`DATABASE_URL` are exported in your shell, they
   silently override the compose `.env` file. Always run with `env -u ...` as
   above, or `unset` those vars first.

2. **Backend `.env`** at `backend/.env` (gitignored). Must contain a
   `DATABASE_URL` pointing at the same Postgres instance/credentials as step 1,
   plus `JWT_SECRET_KEY` / `ENCRYPTION_KEY` (any non-default random string is
   fine for local dev -- the backend logs a security warning if you leave the
   placeholder defaults from `.env.example`). Example:

   ```
   APP_ENV=development
   DATABASE_URL=postgresql+asyncpg://postgres:searchify_dev_password@localhost:5432/searchify
   JWT_SECRET_KEY=<any long random string>
   JWT_ALGORITHM=HS256
   JWT_EXPIRE_HOURS=24
   ENCRYPTION_KEY=<any 32+ byte random string>
   FRONTEND_URL=http://localhost:3000
   FRONTEND_ORIGINS=http://127.0.0.1:3000,http://localhost:3000
   LOGFIRE_ENABLED=false
   LOGFIRE_TOKEN=
   ```

3. **Frontend `.env.local`** at `frontend/.env.local` (gitignored):

   ```
   BACKEND_ORIGIN=http://localhost:8000
   ```

   The Next.js `rewrites()` config proxies `/api/:path*` to `${BACKEND_ORIGIN}/api/:path*`
   so the browser only ever talks to `localhost:3000` (same-origin, cookie-based auth
   works cleanly, no CORS).

4. **Dependencies installed**:
   ```bash
   cd /code/abhij1306/Searchify/backend && uv sync
   cd /code/abhij1306/Searchify/frontend && pnpm install
   ```

5. **Database migrations applied**:
   ```bash
   cd /code/abhij1306/Searchify/backend && uv run alembic upgrade head
   ```

## Starting each component

```bash
# 1. Postgres (if not already running)
cd /code/abhij1306/Searchify/infra/docker && env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL docker compose up -d db

# 2. Backend API (port 8000)
cd /code/abhij1306/Searchify/backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. Background audit worker (polls the Postgres task queue)
cd /code/abhij1306/Searchify/backend && uv run python -m app.workers.audit_worker

# 4. Frontend (port 3000)
cd /code/abhij1306/Searchify/frontend && pnpm dev
```

URLs:
- Frontend: http://localhost:3000
- Backend direct: http://localhost:8000 (health: `GET /health` -- NOT proxied
  through the frontend, since Next.js only rewrites `/api/:path*` and all
  backend routers are mounted at `/api/v1/*`)
- Backend via frontend proxy: http://localhost:3000/api/v1/...

## Running the seed script

The seed script and its audit-fabrication helper live outside the repo (as
instructed) at:

- `/memory/testing/Searchify/seed.sh`
- `/memory/testing/Searchify/seed_audits.py`

Both are idempotent -- safe to re-run. `seed.sh` creates (or reuses, if already
present):
- 1 demo user: **`demo@searchify.dev` / `DemoPass123!`** (personal workspace
  auto-created on register)
- 1 project "Acme Running Shoes" (brand Acme, 3 competitors, owned/unintended
  domains, `benchmark_mode=controlled_localized`)
- 1 prompt set "Core Benchmark Prompts" with 6 prompts covering every intent
  (discovery/comparison/purchase/service/local/unspecified)
- 3 BYOK provider connections (openai/anthropic/google) with **fake** API keys
  (`sk-fake-demo-key-...`) -- these cannot make real provider calls; see
  Known Issues.
- 4 audits covering every lifecycle state (via `seed_audits.py`, run
  automatically at the end of `seed.sh`):
  - `completed` -- 18/18 tasks succeeded, full analysis + citations + mentions
    + one MetricSnapshot (visibility_score 83.0)
  - `partially_completed` -- 6/9 succeeded, 3 failed (rate_limit)
  - `failed` -- 0/6 succeeded, all failed (auth_failure)
  - `running` -- in-flight, 1 task "running" + 17 "queued", no results yet

Run it:

```bash
bash /memory/testing/Searchify/seed.sh
```

Requires the backend (port 8000) and frontend (port 3000, for the proxied
calls) to already be running.

### IMPORTANT: the live audit_worker must not touch the "running" demo audit

`seed_audits.py` pins `available_at` / `lease_expires_at` on the "running"
audit's queued/running `AuditTask` rows far in the future (year ~2099). This is
required because the real `app.workers.audit_worker` polls the Postgres task
queue and will claim any `queued`/`retry_wait` task whose `available_at <= now`
-- if you seed a "running" demo audit with `available_at=now`, the live worker
(which has no real provider credentials, only the fake BYOK keys above) will
claim those tasks within seconds and flip the whole audit to `failed`
(401/400 provider errors), corrupting the demo data. If this happens, delete
the corrupted audit (`DELETE FROM audits WHERE id = '<id>'` cascades to
tasks/events, or use SQLAlchemy `session.delete(audit)`) and re-run
`seed_audits.py`.

## Troubleshooting

- **`/health` returns 404 via `localhost:3000/health`**: expected. Query
  `localhost:8000/health` directly -- the frontend only proxies `/api/:path*`.
- **Auth uses HttpOnly cookies, not bearer tokens.** `POST /api/v1/auth/login`
  sets a `searchify_session` cookie; there is no `access_token` field in the
  JSON response. Use `curl -c cookies.txt ... -b cookies.txt ...` (or a real
  browser) rather than trying to extract a bearer token.
- **Seeding prompts**: the create-prompt endpoint is nested,
  `POST /api/v1/prompt-sets/{prompt_set_id}/prompts` (prompt_set_id is a path
  param) -- there is no flat `POST /api/v1/prompts`.
- **Provider connections use fake keys** and cannot be "tested" for real
  (`POST /api/v1/provider-connections/{id}/test` will fail against
  `sk-fake-demo-key-...`). This is expected in this sandboxed environment.
- **Shell-exported `POSTGRES_*`/`DATABASE_URL` env vars silently override**
  `infra/docker/.env` when starting docker compose. Always `env -u ...` or
  `unset` them first.

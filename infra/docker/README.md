# Searchify Docker stack

Services: `db` (Postgres 16), `migrate` (one-shot `alembic upgrade head`),
`web` (FastAPI/uvicorn), `worker` (audit worker — real impl in B5).

## Bring the stack up (gotcha 1 workaround — use verbatim)

This machine exports `POSTGRES_PASSWORD` / `POSTGRES_USER` / `POSTGRES_DB` /
`DATABASE_URL` into **every shell**, and Docker Compose resolves `${VAR}` from
the shell environment **before** `.env`. Those inherited values silently
override the repo `.env`, so you must unset them for the Compose invocation and
re-supply the repo password explicitly (see `docs/invariants.md` invariant 11):

```bash
cd /path/to/Searchify
cp infra/docker/.env.example infra/docker/.env   # first time only

env -u POSTGRES_PASSWORD -u POSTGRES_USER -u POSTGRES_DB -u DATABASE_URL \
  POSTGRES_PASSWORD=searchify_dev_password \
  docker compose -f infra/docker/docker-compose.yml up -d --force-recreate
```

`POSTGRES_PASSWORD` must match the value in `infra/docker/.env`.

Check health:

```bash
curl -fsS http://localhost:8000/health   # {"status":"ok"}
```

Tear down:

```bash
docker compose -f infra/docker/docker-compose.yml down        # keep volume
docker compose -f infra/docker/docker-compose.yml down -v     # drop DB volume
```

## Local (no Docker)

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

# Contributing to Searchify

Thanks for contributing. This guide covers the workflow, conventions, and the review bar
for changes to Searchify. Read [`Agents.md`](Agents.md) and
[`docs/invariants.md`](docs/invariants.md) first — they define the contract every change
must respect.

## Before you start

1. **Read the one companion doc for the subsystem you're touching** (see the table in
   [`Agents.md`](Agents.md)). Don't read the whole `docs/` tree up front.
2. **Grep before you add.** Search for the resource / function / schema / token / component
   first. Duplication is a review failure (invariant 2). One concept → one owner.
3. **Set up your environment** per [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md), including
   the two environment gotchas (Docker `${VAR}` override, tunnel double-CORS).

## Development workflow

1. Create a feature branch off the base branch:
   ```bash
   git checkout -b feat/<short-description>
   ```
2. Make the **minimal scoped change**. Put code in the owning subsystem, not wherever is
   convenient:
   - Backend: `api / core / models / schemas / domain / connectors / orchestration / analysis / workers`
   - Frontend: `shell+auth / API-contract / setup / prompts / providers / visibility / runs / UI+tokens`
3. Add or adjust tests in the existing framework (pytest for backend, Vitest for frontend).
4. Run the verify commands below for what you changed.
5. Open a pull request with a clear description and a **detailed `## Testing` section**
   (what you ran, what passed, evidence). A PR without a Testing section is not ready for
   review.

## Branch & commit conventions

- **Branches:** `feat/…`, `fix/…`, `docs/…`, `refactor/…`, `chore/…` with a short kebab-case
  description.
- **Commits:** conventional-commit style, e.g.
  `fix(frontend): unwrap auth response { user } to match backend DTO`. Reference the
  invariant number when a change enforces one (e.g. "enforces invariant 5").
- Keep commits scoped to one subsystem where possible; when multiple agents/people work the
  same tree, stage explicit pathspecs rather than `git add -A`.

## Configuration rule (invariant 1)

Tokens, thresholds, model ids, transport catalogs, guardrail knobs, timeouts, and rate
limits live **only** in `backend/app/core/config/*`. Service / domain / worker / analysis /
API code *reads* config — it never hard-codes these values inline. On the frontend, no magic
endpoints or feature flags scattered in components; they belong in the API-contract layer or
env.

## Database migrations

Migrations are **hand-written** (Alembic autogenerate is disabled in this repo). Write the
migration by hand, keep it in the numbered chain, and verify:

```bash
cd backend
uv run alembic upgrade head    # applies cleanly on a fresh DB
uv run alembic check           # "No new upgrade operations detected"
```

## Verify commands

Run the **focused** subset for what you changed; run the full suite before opening the PR.

```bash
# Backend (from backend/)
uv run pytest -q
uv run ruff check .
uv run alembic upgrade head

# Frontend (from frontend/)
pnpm test              # Vitest
pnpm check:policy      # architecture + token guards
pnpm exec tsc --noEmit # type check
pnpm build             # next build
```

## Frontend/backend contract discipline

The frontend `lib/api/` layer mirrors the backend DTOs (`backend/app/domain/*/schemas.py`)
with zod schemas. **The backend is the source of truth.** When you change a DTO:

- Update the matching zod schema in `frontend/lib/api/schemas.ts` and any MSW fixtures.
- Prefer `.strict()` response schemas so contract drift fails loud — but only when the
  schema fully models the backend response. A `.strict()` schema that omits a field the
  backend actually returns will fail validation at runtime (fixtures won't catch it — test
  against the real backend).

## The review bar (the 12 invariants)

A change that violates any invariant in [`docs/invariants.md`](docs/invariants.md) is a
review failure regardless of whether it "works". The most commonly hit:

- **Workspace auth on every query** (5) — every project-owned read/write goes through
  `require_workspace_member`; never scope by `user_id`; all ids are string UUIDs.
- **BYOK secrets never returned/logged** (6) — Fernet-encrypted at rest; never in a DTO,
  log, or prompt.
- **Provenance + version on every derived row** (4) and **reports are projections** (7) —
  metrics/reports render persisted evidence; they never re-call a provider.
- **Immutable artifacts / single-writer** (3) and **queue leasing rules** (8).
- **Determinism** (9) — headline metrics use deterministic matching; no LLM. Don't back-fill
  sentiment/avg-position with a fake-deterministic heuristic.
- **Logical vs transport identity** (10) — every result records logical engine + transport
  provider + exact model.

## Reporting issues

Open an issue with reproduction steps, expected vs actual behavior, and the relevant logs or
screenshots. For security-sensitive reports (e.g. secret handling), please disclose
privately rather than in a public issue.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).

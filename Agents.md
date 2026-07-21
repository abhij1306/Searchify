# Agents.md — Searchify

> Session bootstrap for coding agents. Terse. Read this first, then read only the
> companion doc your task touches. Do not read the whole `docs/` tree up front.

## What Searchify is

Searchify is a greenfield **AEO / AI-visibility SaaS** — an original "Searchable"-class
product that measures how brands appear inside answer-engine responses (ChatGPT, Gemini,
Claude). A workspace defines a **brand + competitors + prompts**, runs a one-time **audit**
(prompt × engine × repetition executions across BYOK answer engines), scores each response
**deterministically** (mentions, citations, share-of-voice), and surfaces the result in a
**Visibility dashboard** + **Run/Executions evidence explorer**.

**The full product** (documented, mostly roadmap) is a broader AEO suite: LLM Analytics,
Traffic, Content, Opportunities, Site Health + Issues, Brand/Competitors/E-E-A-T, Topics,
integrations (GSC/GA4/Bing), Agent, and MCP. See [`docs/architecture.md`](docs/architecture.md)
for that full surface.

The current product centers on the **visibility slice** — seven frontend screens (Auth, App
Shell, Brand/Project setup, Prompt library, Provider Settings, Visibility dashboard,
Run/Executions explorer) on the full greenfield architecture: workspaces + workspace-scoped
auth, **UUID PKs everywhere**, BYOK provider settings, a **Postgres `FOR UPDATE SKIP LOCKED`
task queue** (no Redis), and a full audit state machine. Additional AEO surfaces are
documented in the architecture and roadmap references as they are designed and delivered.

## Unified contract (memorize this — every doc agrees)

- **All ids are string UUIDs.** Workspace-scoped. **No `user_id` scoping. No integer PKs.**
- **API prefix `/api/v1`** (the reference `/api/ai-visibility` prefix is dropped).
- **Logical engines**: `chatgpt | gemini | claude` (what the user asked for). **Active
  transports** (how we physically reach an engine) are **exactly** `openai | anthropic |
  google`, one approved route per engine: `chatgpt → openai → gpt-5.4`, `claude → anthropic
  → claude-sonnet-4-6`, `gemini → google → gemini-flash-latest`. ChatGPT runs through the
  **direct OpenAI Responses API** (`backend/app/connectors/answer_engines/openai.py`). See
  `backend/app/core/config/provider_catalog.py` (`ACTIVE_TRANSPORTS` / `APPROVED_ROUTES`).
- `benchmark_mode`: `consumer_like | controlled_localized | forced_grounded`.
- Prompt `intent`: `discovery | comparison | purchase | service | local`.
- Browser → backend is **same-origin** via Next.js `rewrites()` (`/api/:path*` → server-only
  `BACKEND_ORIGIN`); the browser never sees a cross-origin backend URL.
- `/visibility` is a **four-tab workspace** with a shared filter bar: **Overview** (default;
  selected-run score / share-of-voice / provider comparison / rankings), **Trends** (cross-run
  metrics + charts from persisted `MetricSnapshot` rows), **Mentions & Citations** and **Query
  Fanout** (both read the shared persisted evidence dataset `GET
  /api/v1/projects/{project_id}/visibility/evidence` →
  `VisibilityEvidenceResponse{items, truncated}`). Only ONE panel renders at a time; the active
  tab is mirrored in `?tab=`; the tablist is WAI-ARIA compliant. There are NO Sources / Topics /
  Sentiment tabs and no disabled / "coming soon" tabs. **Sentiment + avg-position are still NOT
  computed** and render as an em-dash (`—`). Query Fanout has three states:
  `queries_available | count_only | no_search`.

## Read-on-demand doc guide

| If your task touches… | Read |
|---|---|
| repo bootstrap, rules, verify commands | this file |
| backend API/models/queue/state machine/analysis | `docs/backend-architecture.md` |
| the hard rules you must never break + the two ops gotchas | `docs/invariants.md` |
| any frontend route, API contract layer, data flow | `docs/frontend-architecture.md` |
| tokens, theme, per-screen layout, component primitives | `docs/design.md` |
| the approved plan / task graph | `docs/plans/` |
| whole-product architecture rationale | `docs/architecture.md` |

## Default startup flow (every task)

1. **Grep before you add.** Search for the resource/function/token first. Duplication is a
   review failure. (invariant 2)
2. **Identify the owning subsystem** for what you are changing (backend: `api / core /
   models / schemas / domain / connectors / orchestration / analysis / workers`; frontend:
   `shell+auth / API-contract / setup / prompts / providers / visibility / runs / UI+tokens`).
   Put code in the owner, not wherever is convenient.
3. Read the one companion doc for that subsystem (table above).
4. Make the **minimal scoped change**. Add/adjust tests in the project's existing framework.
5. Run the focused verify commands below before reporting done.

## Frontend package manager

The frontend uses **pnpm only**, pinned to `pnpm@11.9.0` in
`frontend/package.json`. Use `pnpm install`, `pnpm <script>`, and the checked-in
`frontend/pnpm-lock.yaml`. Do not use npm or yarn, do not run `npm install`, and do not
create or regenerate a `package-lock.json`.

## Always-on rules (full list in `docs/invariants.md`)

- **Config never lives in service code.** Tokens, thresholds, model ids, transport catalogs,
  guardrail knobs live in `app/core/config/*`. (invariant 1)
- **Workspace auth on every query.** Every project-owned read/write goes through the
  `require_workspace_member` dependency. Never scope by `user_id` or an admin shortcut.
  (invariant 5)
- **Secrets are never returned.** BYOK keys are Fernet-encrypted at rest, never in any
  Response DTO, never logged, and the brand list is never sent to a provider. (invariant 6)
- **Provenance on every derived row.** Each analysis/metric row references its
  `RawResponseArtifact` + `analyzer_version` (+ formula/rule version). Reports/metrics are
  **projections** — never re-call a provider. (invariants 4, 7)
- **Immutable artifacts, single-writer.** Raw artifacts and executions are written once.
  (invariant 3)

## Verify commands

Run the **focused** subset for what you changed, not the whole suite (other agents may have
in-progress work that breaks the global build).

```bash
# Backend — focused tests + lint (from backend/)
# Tests need only a running local Postgres (creds come from the repo .env
# DATABASE_URL) — no env vars, no Docker. The suite creates and drops a
# throwaway searchify_tests_<runid> database automatically.
uv run pytest tests/unit/test_<area>.py tests/component/test_<area>.py -q
uv run ruff check .

# Migrations — single squashed bootstrap revision (0001_initial, built from
# Base.metadata). GREENFIELD POLICY: until production, schema changes are made
# by editing the models and recreating the DB — do NOT add new revision files.
uv run alembic upgrade head

# Frontend — focused (from frontend/)
pnpm test -- <file>          # Vitest
pnpm build                   # next build
pnpm lint
```

Web preview / same-origin proxying has its own gotcha — the browser must hit `/api/*`
relative, never a cross-origin backend URL (curl cannot reproduce the double-CORS failure).
See `docs/invariants.md` invariant 12.

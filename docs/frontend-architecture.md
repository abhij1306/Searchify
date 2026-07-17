# Frontend Architecture — Searchify

> Next.js App Router frontend for the visibility slice. Consumes the live workspace-scoped
> `/api/v1` backend contract (B2–B6) through a same-origin proxy. No mock-only fallback, no
> int-id / `user_id` contract.
> Companion docs: [`../Agents.md`](../Agents.md), [`invariants.md`](invariants.md),
> [`backend-architecture.md`](backend-architecture.md), [`design.md`](design.md).

## 1. Stack & role

- **Next.js App Router** + **TypeScript**, deployed on **Vercel** (root = `frontend/`).
- **TanStack Query v5** for server state; **react-hook-form** + **zod** for forms/validation.
- **Tailwind v4** semantic tokens (light/dark) — single `app/globals.css` token source authored
  from [`design.md`](design.md). **Radix** primitives + **lucide** icons; **CVA** for variants.
- Frontend conventions: typed API client with `ApiError` + request-id + abort, per-domain
  endpoint modules with zod `strictValidate`, a `queryKeys` module, a React Query retry policy,
  CVA primitives, a single-`globals.css` token bridge, and a `data-theme` toggle — on
  **App Router**.
- **Role**: render the seven MVP screens and orchestrate calls to the FastAPI backend. It owns
  no business logic and no source of truth — **the backend is the source of truth** (§7).

## 2. Full-product route map (every surface tagged)

| Route | Screen | Status |
|---|---|---|
| `/login`, `/register` | Auth | **MVP** |
| `(app)/layout.tsx` | App shell (sidebar + top bar + project switcher) | **MVP** |
| `/setup` | Brand/Project setup | **MVP** |
| `/prompts` | Prompt library (manual + CSV import; AI-suggest = coming-soon) | **MVP** |
| `/providers` | BYOK Provider Settings | **MVP** |
| `/visibility` | Visibility dashboard (selected-run projection) | **MVP** |
| `/runs`, `/runs/[runId]`, `/runs/[runId]/executions/[executionId]` | Run/Executions explorer | **MVP** |
| `/analytics` (AEO Insights beyond single-run), trend history | LLM Analytics / trend | Roadmap |
| `/traffic` | Traffic | Roadmap |
| `/content` | Content writer | Roadmap |
| `/opportunities` | Opportunities | Roadmap |
| `/site-health`, `/site-health/crawls/[crawlId]/pages/[siteUrlId]`, `/issues` | Site Health + Issues | **Implemented** — see [`site-health.md`](site-health.md) |
| `/brand` (Profile beyond setup, Competitors, E-E-A-T) | Brand suite | Roadmap |
| `/topics` | Topics | Roadmap |
| `/writing` (Tone/Style, Memory), Knowledge Base | Writing suite | Roadmap |
| Settings → Integrations (GSC/GA4/Bing), Agent, MCP | Integrations / Agent | Roadmap |

The sidebar renders roadmap items **disabled ("soon")**; only MVP items are live.

## 3. MVP-vs-roadmap boundary table

| Capability | MVP | Roadmap |
|---|---|---|
| Auth + workspace + project switch | ✅ | |
| Brand/project setup (aliases, owned/unintended domains, competitors, benchmark_mode) | ✅ | rich Brand/E-E-A-T profile |
| Prompts: manual entry + CSV import | ✅ | AI-suggested generation (`/generate` stub) |
| BYOK providers + connection test | ✅ | direct OpenAI adapter |
| Launch audit (multi-engine, repetitions) + cancel | ✅ | recurring schedules |
| Visibility dashboard | selected-run projection (score + per-engine + rankings) | cross-run trend history |
| Sentiment + avg-position columns | render `—` placeholder | computed |
| Run/Executions evidence + CSV/MD export | ✅ | HTML/JSON renderers |
| Run progress | polling (SSE optional) | full SSE streaming UI |

## 4. Frontend subsystems

| Subsystem | Files (target) | Owns |
|---|---|---|
| Shell + auth | `(auth)/*`, `(app)/layout.tsx`, `session-guard.tsx`, `app-shell`, `sidebar-nav`, `top-bar`, `project-switcher` | Session, guard, nav, project context |
| API contract layer | `lib/api/{client,errors,query-client,query-keys,schemas,types,index}.ts` + per-domain modules | Transport, zod contracts, retry policy |
| Setup | `/setup` + `lib/api/projects.ts` | Brand/project create + edit |
| Prompts | `/prompts` + `lib/api/prompts.ts` | Prompt CRUD, CSV import, AI-suggest coming-soon |
| Providers | `/providers` + `lib/api/providers.ts` | BYOK cards, route toggle, connection test |
| Visibility | `/visibility` + `lib/api/visibility.ts` | Selected-run dashboard projection |
| Runs / executions | `/runs/*` + `lib/api/runs.ts` | Launch, progress, cancel, evidence, export |
| UI + token policy | `components/ui/*`, `app/globals.css` | CVA primitives, bridged tokens only (no raw hex) |

## 5. Live backend API usage

- **Same-origin proxy**: `next.config.ts` `rewrites()` maps `/api/:path*` → the server-only
  `BACKEND_ORIGIN`. The browser **only ever** calls `/api/...` relative (invariant 12).
- **API client** (`lib/api/client.ts`): relative base (`/api/v1`), `ApiError` with
  `X-Request-ID`, `AbortSignal` support, `credentials:'include'`, `cache:'no-store'`, bounded
  network retry for GET/idempotent only, JSON enforcement.
- **Endpoints per screen**:
  - Auth → `/auth/register|login|logout|me`
  - Shell/switcher → `/workspaces`, `/projects`
  - Setup → `/projects` (+ `/projects/{id}`)
  - Prompts → `/prompt-sets`, `/prompts/{id}`, `/prompt-sets/{id}/import` (CSV),
    `/prompt-sets/{id}/generate` (stub → coming-soon UI)
  - Providers → `/provider-connections`, `/provider-connections/{id}/test`, `/provider-catalog`
  - Visibility → `GET /projects/{id}/visibility?audit_id=`
  - Runs → `POST /audits`, `GET /audits`, `GET /audits/{id}`, `POST /audits/{id}/cancel`,
    `GET /audits/{id}/executions`, `GET /executions/{id}`, `GET /audits/{id}/export.{csv,md}`,
    `GET /audits/{id}/events` (SSE, optional)
- **Workspace scoping**: the active workspace + project are carried in context; the backend
  enforces workspace auth on every query (invariant 5). No `user_id` anywhere.
- **Cookie session**: JWT in a secure HttpOnly cookie; the client sends `credentials:'include'`.
  A 401 clears the session and redirects to `/login`.
- **Run progress = polling first**: `/runs/[runId]` **polls** `GET /audits/{id}` while active
  (requested/completed/failed + status). The backend SSE `/events` endpoint is MVP but the UI
  **consumes SSE optionally** — polling is the baseline so a dropped stream never blocks
  progress.

## 6. Drift policy

- Every response is validated with **zod `strictValidate`** — it **fails loud** on any field
  mismatch, extra key, or type drift rather than silently coercing. A validation failure is a
  bug to fix, not to swallow.
- **The backend is the source of truth.** The frontend never invents fields, never keeps a
  parallel schema, and never falls back to mock data in production paths. If the contract
  changes, update `schemas.ts` to match the backend, not the other way around.

## 7. zod data contracts (all ids `z.string().uuid()`, workspace-scoped)

- `sessionUserSchema {id,email,role,is_active,created_at,updated_at}`
- `competitorSchema {id,name,aliases[],domains[]}`
- `promptSchema {id,prompt_set_id,text,theme,intent,branded,enabled,origin}`
- `projectSchema {id,workspace_id,name,brand_name,website_url,country_code,language_code,
  benchmark_mode,default_repetitions,brand{aliases[]},owned_domains[],unintended_domains[],
  competitors[],prompt_sets[],created_at,updated_at}` (benchmark_mode enum)
- `providerConnectionSchema {id,workspace_id,transport_provider,base_url,active,...}` — **secret
  never present**
- `providerRouteSchema {id,logical_engine,transport_provider,transport_model,is_default}`
- `providerCatalogSchema`
- `auditSchema {id,workspace_id,project_id,status,random_seed,configuration,summary,
  requested_count,completed_count,failed_count,error_message,created_at,updated_at,completed_at}`
- `executionSchema {id,audit_id,prompt_index,repetition,randomized_position,status,answer_text,
  search_used,search_events[],citations[],score,provider_metadata,error_code,error_message,
  latency_ms}`
- `citationSchema {ordinal,url,title,domain,cited_text,classification}` (`owned|competitor|third_party`)
- `visibilitySchema` — selected-run: score + per-engine comparison + rankings rows;
  `sentiment`/`avg_position` nullable.

**Every `id` and `*_id` field is `z.string().uuid()`; no numeric ids; no `user_id`.** All
responses pass through `strictValidate`.

## 8. Testing surface

- **Vitest + Testing Library + jsdom + msw** for unit/component (client throws `ApiError`,
  `strictValidate` throws on mismatch, `shouldRetryQuery` matrix, form validation, table CRUD,
  dashboard rendering from data, theme toggle).
- **Playwright** smoke: login → shell → Visibility → open run → open execution.
- **Architecture-guard scripts**: line budgets, required API owners exist, `index.ts` owns no
  transport, token-escape / **no-raw-hex** guard over `globals.css` + `components/`.
- Full-suite + build + guards run in task **V1** (final full-stack verification).

## 9. Architectural notes

- **Pre-hydration `data-theme` bootstrap** in root `layout.tsx` avoids a theme flash; the
  `theme-toggle` primitive persists `data-theme`.
- **Retry policy** (`shouldRetryQuery`): retry 408/429/5xx/network up to 2×; `staleTime` 15s;
  `refetchOnWindowFocus:false`.
- **`index.ts` is a compat facade** — it spreads the per-domain modules and owns no transport.
- **`trend-chart` primitive is built but unused in MVP** — kept ready for the roadmap trend
  view; not wired into any MVP screen (the MVP dashboard is single-run).
- **No raw hex in components** — only bridged Tailwind semantic tokens (see [`design.md`](design.md)).

## 10. Companion docs

- Repo bootstrap + rules: [`../Agents.md`](../Agents.md)
- Hard rules + ops gotchas: [`invariants.md`](invariants.md)
- Backend contract this frontend consumes: [`backend-architecture.md`](backend-architecture.md)
- Design tokens + per-screen layout: [`design.md`](design.md)

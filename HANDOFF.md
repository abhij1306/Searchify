# Site Health — Authoritative Handoff (Slices 6–9)

> **Updated 2026-07-17. This section supersedes stale status text below.** The older
> Task 5 notes are retained as implementation history only. Branch:
> `vorflux/site-health`, based on completed Task 5 commit `6f8bfb1`.
>
> **Delivery rule:** finish, verify, commit, and push each slice separately to this
> same branch. Do not create another feature branch. The checkpoint committed with
> this handoff contains the current Slice 6 work in progress; independent review and
> verification findings listed below still need a follow-up commit.

## Current state

- Slices 1–5 are implemented and were verified before Slice 6 began.
- Slice 5 is complete at `6f8bfb1` (`feat(site-health): complete analysis worker pipeline`).
- **Slices 6–9 are implemented on this branch** (final *independent* verification by
  the external tester is still pending — see the note at the end of this section):
  - **Slice 6** (API/router/service/DTO/cursor/export + frontend schema alignment)
    committed, with the reconciliation follow-up (`0c021e3`) proving handoff items 1–7.
  - **Slice 7** (`04c7c6a`) — discovery / inventory selection / live analysis /
    dashboard UI, Site Health nav enabled.
  - **Slice 8** (`bd0470f`) — grouped Issues catalog + per-URL detail UI, dashboard
    View links + Issues nav activated.
  - **Slice 9** (this commit) — integration & documentation closure: docs reconciled
    to the shipped endpoint fields/status vocabulary (new `docs/site-health.md`; README,
    backend/frontend architecture, design, roadmap docs updated) and broad deterministic
    backend E2E coverage added (`backend/tests/component/test_site_health_e2e.py`):
    full create→discover→select→analyze→dashboard→issues→URL-detail→export read journey,
    create/cancel lifecycle, stale-selection 409, partial/error page projection, Free
    redaction end to end, and the same journey in a non-default workspace.
- **Verification state at the Slice 9 commit:** backend `ruff` clean and the full
  backend suite green (**373 passed**, DB-backed); frontend `vitest` green
  (**275 passed**). The live SSE / real-worker browser dry run against a migrated live
  server remains for the external testing agent (it needs a live DB/server because the
  stream opens independent `SessionLocal()` sessions, not the component-test override).
- Prioritize direct reuse and speed; the hard crawler, selection, analysis, issues,
  scoring, event, and snapshot logic already exists.

### Current Slice 6 files

- `backend/app/api/site_health.py`
- `backend/app/domain/site_health/api_schemas.py`
- `backend/app/domain/site_health/service.py`
- `backend/app/domain/site_health/normalization.py`
- `backend/app/analysis/site_health/exports.py`
- `backend/app/core/config/site_health.py`
- `backend/app/main.py`
- `backend/tests/unit/test_site_health_exports.py`
- `backend/tests/unit/test_site_health_service_pure.py`
- `backend/tests/component/test_site_health_api.py`
- `backend/tests/component/test_health.py`
- `frontend/lib/api/schemas.ts`

### Outstanding Slice 6 review/verification work

Do not assume these are fixed merely because this checkpoint is pushed:

1. Scope inventory/pages/page-detail/exports to URLs admitted to the selected crawl,
   not the project's historical URL catalog; test Free-after-Starter downgrade leakage.
2. Finish minimal frontend transport support: create uses `seed` (not `random_seed`),
   page params/query keys include `monitored`, SSE uses abortable credentialed fetch
   streaming with `X-Workspace-Id`, and exports use authenticated `getBlob` plus
   object-URL cleanup instead of plain navigation.
3. Page detail must include all persisted rule evaluations and deduplicated link
   references, with current rule-label fallback and bounded crawl/workspace scoping.
4. Keep grouped issue canonical identity stable across filters (earliest unfiltered
   `(created_at, id)`), paginate grouped SQL results without splitting groups, and
   reject/canonicalize non-representative detail IDs.
5. Bound issue history to crawls at or before the selected crawl chronology.
6. Fix sparse status-filter pagination, typed cursor errors (400, not 500), and CSV
   spreadsheet-formula neutralization for cells beginning `=`, `+`, `-`, or `@`.
7. Independently verify create/cancel/selection/dashboard, successful non-default
   workspace SSE/export, stream resume/timeout/disconnect, and full endpoint isolation.

## Owner mockups (committed in this repository)

These images are the source of truth. Keep the existing Searchify shell, tokens,
components, typography, and light/dark behavior; do not invent a new design system.

| Mockup | File | Slice | Required state |
|---|---|---:|---|
| 708 | `docs/mockups/site-health/708.png` | 7 | Free URL discovery in progress, sample/upgrade notice, preview table |
| 709 | `docs/mockups/site-health/709.png` | 7 | Completed discovery, inventory, staged monitored selection, quota |
| 712 | `docs/mockups/site-health/712.png` | 7 | Live analysis progress and per-page queued/running/completed states |
| 713 | `docs/mockups/site-health/713.png` | 7 | Completed dashboard, scores, coverage, tabs, page rows and View actions |
| 710 | `docs/mockups/site-health/710.png` | 8 | Grouped Issues catalog, severity summaries/filters/remediation |
| 711 | `docs/mockups/site-health/711.png` | 8 | Per-URL detail, scores, delivery facts, ordered issues |

Session-only design translations (if the continuation runs on the same machine) are
under `/code/.plans/designs/`: `discovery-live-*`, `inventory-selection-*`,
`dashboard-analyzing-*`, `dashboard-completed-*`, `issues-catalog-*`, and
`url-detail-*`, with metadata in `/code/.plans/designs/design-plan.json`.
The detailed approved execution plan is
`/code/.plans/v1-searchify-site-health-slices-6-7.md` (its content covers Slices 6–8).

## Slice 7 — Site Health discovery, selection, analysis, dashboard

Implement `/site-health` from mockups 708, 709, 712, and 713.

- Reuse `useProjectContext`, Task 2 Site Health schemas/query factories/filter and
  staged-selection helpers, and existing card/table/badge/score/alert/skeleton primitives.
- Discovery: create/cancel crawl and show progressive admitted URLs without implying
  hidden Free totals. Free remains read-only and sample-scoped.
- Starter inventory: cursor pagination, search/status filters, staged IDs retained
  across pages, quota from server entitlement, full-set versioned commit, and stale
  conflict refetch/rebase followed by explicit resubmission.
- Analysis: poll crawl/dashboard/pages while active. SSE is only an invalidation
  accelerator; dropped streams must not stop polling. Invalidate all page queries so
  rows move queued → running → completed/error/blocked without reload.
- Dashboard tabs must be server-backed and cursor-safe: monitored, all discovered,
  and errors/blocked. Never filter only the current client page.
- Missing/failed scores render `—`, never fabricated zeroes.
- Exports must be authenticated blob downloads so a selected non-default workspace's
  `X-Workspace-Id` is preserved.
- Enable Site Health navigation in Slice 7. Keep View actions disabled until Slice 8
  lands so the Slice 7 commit contains no broken route.
- Verify focused frontend tests, lint/build, then the real app/backend/Postgres in a
  browser. Capture evidence matching all four Slice 7 mockups. Commit and push Slice 7.

Expected route/components:

- `frontend/app/(app)/site-health/page.tsx`
- `frontend/components/site-health/site-health-screen.tsx`
- `discovery-progress.tsx`, `inventory-selection.tsx`, `analysis-progress.tsx`
- `health-dashboard.tsx`, `pages-table.tsx`
- credentialed `frontend/lib/site-health/use-crawl-events.ts`
- authenticated `frontend/lib/site-health/download.ts`

## Slice 8 — Issues catalog and per-URL detail

The owner explicitly chose to include Slice 8 so mockup 713's View actions have a
real destination. Implement mockups 710 and 711.

- `/issues`: current crawl, API-owned occurrence/severity/affected-page summaries,
  grouped issue search/filters/pagination, current catalog title with `rule_id`
  fallback, persisted remediation, affected URL navigation, and client-only copy.
- Do not add unsupported “mark reviewed/resolved” persistence merely because the
  mockup displays an action.
- Per-URL route:
  `/site-health/crawls/[crawlId]/pages/[siteUrlId]`.
  Render URL metadata, overall/Technical/AEO scores, persisted delivery/normalized
  facts, all current evaluations/issues ordered by severity, bounded evidence and
  remediation, deduplicated link references, and paginated crawl-bounded history.
- Add strict frontend detail/history/issues schemas, API readers, and query keys in
  this slice (not earlier unless required by Slice 6 contract tests).
- Activate dashboard View links and Issues navigation only after both destinations work.
- Browser-check mockups 710/711 and navigation from 713 in a non-default workspace.
  Commit and push Slice 8.

## Slice 9 — integration, documentation, and end-to-end verification

> **Implemented on this branch (Slice 9 commit).** Docs reconciled to the shipped
> endpoint fields/status vocabulary and broad deterministic backend E2E coverage added
> (`backend/tests/component/test_site_health_e2e.py`, reusing the Slice 6 seed helpers —
> no fixture duplication). New reference: `docs/site-health.md`. Updated: `README.md`,
> `docs/backend-architecture.md`, `docs/frontend-architecture.md`, `docs/design.md`,
> `docs/roadmap/technical-audit.md`, `docs/roadmap/README.md`.
>
> **Still pending (external tester only):** the live SSE / real-worker browser dry run
> against a migrated live server, plus the accessibility/responsive browser smoke checks
> and final evidence capture. These are intentionally not duplicated here.

Use the repository's original roadmap/spec as authority for final closure. Keep this
slice focused on integration rather than redesigning Slices 1–8.

- Update README/product/API documentation for Site Health setup, entitlements,
  Free sample behavior, Starter monitored selection, statuses, exports, and routes.
- Add/finish broad create → discover → select → analyze → dashboard → issues → URL
  detail → export end-to-end coverage with deterministic fixtures; include cancellation,
  stale selection, partial/error handling, Free redaction, and non-default workspace.
- Run full backend and frontend suites, lint, build, migration checks, and browser
  accessibility/responsive smoke checks. Verify no raw HTML or forbidden totals leak.
- Reconcile docs/contracts with actual endpoint fields and status vocabulary.
- Capture final evidence, update the handoff/status, commit and push Slice 9.

## Environment and commands learned in this session

```bash
export PATH="$HOME/.local/bin:$PATH"
export TEST_DATABASE_URL="postgresql+asyncpg://postgres:searchify_dev_password@localhost:55432/test_db"
cd infra/docker && docker compose up -d db
cd ../../backend && uv sync --extra dev
uv run ruff check .
uv run pytest -q
cd ../frontend && pnpm install --frozen-lockfile
pnpm lint
pnpm build
```

- PostgreSQL container: `searchify_pg`, port `55432`.
- Tests create isolated schemas in `test_db`.
- Real SSE verification needs a migrated live database/server because the stream opens
  independent `SessionLocal()` sessions rather than the component-test schema override.
- A live test DB named `searchify_live` was prepared during this session, but a future
  machine should recreate it rather than assume it exists.

## Guardrails and continuation checklist

- Do not modify `selection.py`, `entitlements.py`, migration `0008_site_health`, shared
  queue claim ordering, or unrelated `backend/app/analysis/scoring.py` without a proven need.
- Keep workspace resolution on `require_active_workspace`; foreign/missing IDs must be
  indistinguishable 404s. Preserve Free count/event/export redaction.
- Prefer existing helpers/contracts and direct components. Avoid speculative layers,
  migrations, worker redesign, PageSpeed/CrUX, raw HTML storage, or unrelated cleanup.
- Before coding: pull `origin/vorflux/site-health`, read this top section, inspect the
  latest commit/diff, and rerun focused tests. Treat later text below as historical when
  it conflicts with this authoritative section.
- After each slice: simplify/review/test, fix findings, commit, and push to the same branch.

---

## Historical handoff (superseded where inconsistent)

> This branch (`vorflux/site-health`) is a **work-in-progress checkpoint**. The
> tip commit (`a8bd8a8`) intentionally contains **non-importable WIP**: the
> worker module references three methods that are not yet defined. Do NOT merge
> the branch as-is and do NOT expect `pytest` to even collect until Task 5 is
> finished (see "CRITICAL: current broken state" below).

This document is a self-contained prompt for an agent to finish the Site Health
feature from Task 5 onward. Tasks 1–4 are complete, committed, and pushed.

---

## 1. Repo, branch, environment

- **Repo:** `abhij1306/Searchify` (GitHub, public), cloned at `/code/abhij1306/Searchify`.
- **Branch:** `vorflux/site-health` (tracking `origin/vorflux/site-health`). Base branch is `main`.
- **Project is greenfield** (owner-confirmed) — you may extend freely, but keep changes additive and do not break the existing audit/ai-visibility features.
- **Backend dir:** `/code/abhij1306/Searchify/backend`.
- **uv** is at `/home/ubuntu/.local/bin/uv` and is NOT on the default PATH. Before every uv call:
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  ```
- **Postgres** for tests: Docker container `searchify_pg` on `localhost:55432`
  (image `postgres:16-alpine`, password `searchify_dev_password`, DB `test_db`).
  If it is not running, start it (the repo has compose/docs; the container name is `searchify_pg`).
- **Test DB env** (conftest creates an isolated schema per test):
  ```bash
  export TEST_DATABASE_URL="postgresql+asyncpg://postgres:searchify_dev_password@localhost:55432/test_db"
  ```
- **pytest:** `asyncio_mode="auto"`. A benign `SECURITY WARNING` about default
  `jwt_secret_key`/`encryption_key` prints on import — ignore it.
- Parsing deps are installed and importable: `lxml`, `defusedxml`, `protego`, `tldextract`.
- **Run the suite from `backend/`:**
  ```bash
  cd backend && export PATH="$HOME/.local/bin:$PATH" \
    && export TEST_DATABASE_URL="postgresql+asyncpg://postgres:searchify_dev_password@localhost:55432/test_db" \
    && uv run pytest -q
  ```
  Baseline before Task 5 WIP: **254 passed** (~95s). Lint: `uv run ruff check .`
  (one pre-existing line-length error in `app/domain/auth/service.py`, unrelated).

## 2. Git state

```
a8bd8a8  WIP(site-health): Task 5 analysis modules + partial worker (INCOMPLETE)  <- HEAD
acd707f  Task 3: secure HTTP connector + progressive URL inventory + discover worker
b89ec14  Task 4: atomic monitored-set lifecycle + worker guards
5374274  Task 1: entitlements, generic queue, models + migration (0008_site_health)
9c7f0ff  Task 2: frontend contracts + pure helpers
243341b  (origin/main baseline)
```

Per-task cadence: commit + push to `vorflux/site-health` after **each** task.
Keep the branch non-empty ahead of base at all times (never `reset --hard` +
force-push to empty — it can trip automation that closes the PR).

## 3. Plan status

- Task 1 — entitlements, generic queue, models, migration `0008_site_health` — **DONE** (`5374274`).
- Task 2 — frontend contracts, zod schemas, query keys, staged-selection helpers — **DONE** (`9c7f0ff`).
- Task 3 — secure HTTP connector + progressive URL inventory + discover worker — **DONE** (`acd707f`).
- Task 4 — atomic monitored-set lifecycle + pure worker guards — **DONE** (`b89ec14`).
- Task 5 — deep analysis, link checks, issues, scores; extend worker with analyze/link_check — **PARTIAL WIP** (`a8bd8a8`, does not import — finish this first).
- Task 6 — workspace-safe APIs, keyset cursors, events/SSE, CSV/MD exports — **NOT STARTED**.
- Task 7 — Site Health discovery/selection/dashboard UI — **NOT STARTED** (needs owner's frontend mockups).
- Task 8 — Issues + per-URL detail UI — **NOT STARTED** (needs mockups).
- Task 9 — integrate, document, end-to-end verify — **NOT STARTED**.

## 4. CRITICAL: current broken state (fix before anything else)

The Task 5 build was cancelled mid-implementation. On disk / at the branch tip:

**Present and compile-clean (but ruff not clean, ~37 unused-import errors):**
- `backend/app/analysis/site_health/__init__.py`
- `backend/app/analysis/site_health/parser.py` — `extract_page_facts(...)` + helpers (title, meta description, robots meta, canonical, OG/Twitter, headings/h1, images/missing-alt, body text + word count, JSON-LD/microdata, links/assets, delivery/security facts).
- `backend/app/analysis/site_health/rules.py` — `RuleEvaluation`, `evaluate_rule`, `evaluate_all`, `rule_for`, and `_check_*` functions for each rule id.
- `backend/app/analysis/site_health/scoring.py` — `score_dimension`, `overall_score`, `score_analysis`, `aggregate_scores` (+ dataclasses).
- `backend/app/analysis/site_health/structured_data.py` — JSON-LD/microdata validation against `STRUCTURED_DATA_REQUIRED_PROPERTIES`.
- `backend/tests/unit/test_site_health_parser.py`, `test_site_health_rules.py`, `test_site_health_scoring.py`.

**Broken:** `backend/app/workers/site_health_worker.py`
- `run_once()` claim widened to `[TASK_KIND_DISCOVER, TASK_KIND_ANALYZE, TASK_KIND_LINK_CHECK]`.
- `_execute_task` dispatch now routes ANALYZE/LINK_CHECK and, in `finally`, calls `self._reconcile_crawl_status(crawl_id)` (instead of `_finalize_discovery`).
- **BUT `_run_analyze`, `_run_link_check`, and `_reconcile_crawl_status` are NOT DEFINED.** The module references undefined methods → importing the worker fails → **the entire pytest suite fails at collection** on this branch.

**No alembic migration was added — correct.** All analysis tables already exist
in `0008_site_health`. If you find yourself wanting a new migration for Task 5,
stop: the tables are already there; adding one is a red flag.

**First action for the continuing agent:** either finish the three worker
methods (below) so the module imports, or, if you prefer a clean slate for the
worker, `git checkout acd707f -- backend/app/workers/site_health_worker.py` to
restore the last-good worker and re-apply the Task 5 worker changes yourself.
The analysis modules and unit tests are worth keeping (review, don't discard).

## 5. Guardrails (do NOT violate)

- Do **not** modify `backend/app/domain/site_health/selection.py` or `entitlements.py` (Task 4 — done).
- Keep `backend/app/orchestration/postgres_task_queue.py` changes strictly **additive**. In particular, do **not** change the `claim()` ordering (`priority DESC, available_at ASC, ...`) — it is shared with the audit queue and must stay untouched.
- Do **not** modify the pre-existing `backend/app/analysis/scoring.py` — it belongs to the unrelated ai-visibility/audit "B6" feature. New site-health scoring lives in `backend/app/analysis/site_health/scoring.py`.
- Config lives in `backend/app/core/config/site_health.py` — rules, weights, versions, limits, statuses, error tokens are all owned there. Read from it; do not hardcode.
- State transitions live in `backend/app/domain/site_health/state_events.py` (there is no `app/orchestration/site_crawl_state.py`).

---

## 6. TASK 5 — Deep analysis, link checks, issues, scores (finish this first)

**Goal:** execute `analyze` and `link_check` queue tasks in the worker, persist
page analyses / rule evaluations / issues / scores, and reconcile the crawl's
overall status from BOTH discovery and analysis substates.

### 6.1 Analysis modules (review the existing WIP, then finish/correct)

Under `backend/app/analysis/site_health/`:

- **`parser.py`** — deterministic lxml HTML fact extraction. PURE + deterministic;
  wrap parsing in try/except so partial facts never crash. Facts to extract:
  title, meta description, robots meta directives (noindex/nofollow), canonical,
  OG/Twitter metadata, headings + h1 count, images + missing-alt count, body text
  + word count bounded by `max_text_chars`, JSON-LD + microdata bounded by
  `max_structured_data_blocks`, links/assets bounded by `max_links_per_page`, and
  delivery/security facts from `redacted_headers` + timing/bytes: TTFB, wire/decoded
  bytes, content-encoding/compression, cache-control, HTTP version, HSTS, CSP,
  X-Content-Type-Options, X-Frame-Options. Use `defusedxml` for any XML parsing.
- **`structured_data.py`** — validate JSON-LD/microdata objects against
  `STRUCTURED_DATA_REQUIRED_PROPERTIES` (Organization, WebSite, WebPage, Article,
  Product, FAQPage, BreadcrumbList).
- **`rules.py`** — evaluate config-owned `SITE_HEALTH_RULES` /
  `SITE_HEALTH_RULES_BY_ID`. Each evaluation returns an outcome in
  `{RULE_OUTCOME_PASS, RULE_OUTCOME_FAIL, RULE_OUTCOME_NOT_APPLICABLE, RULE_OUTCOME_ERROR}`
  plus bounded evidence and the rule's dimension/category/severity/weight.
  Applicability via `applicability_key`: `"always"` vs `"has_html"`. A rule that
  raises → `ERROR`. Concrete checks:
  - `technical.title_present`, `technical.meta_description_present`,
    `technical.canonical_present`
  - `technical.indexable` (noindex → FAIL)
  - `technical.https` (final_url scheme)
  - `technical.single_h1`
  - `aeo.structured_data_present`, `aeo.open_graph_present`, `aeo.sufficient_text`
- **`scoring.py`** — EXACT formula (do not paraphrase):
  `score = 100 × passed_weight / (passed_weight + failed_weight + error_weight)`
  over **applicable** evaluations. Exclude `not_applicable`. `error` gets **zero
  credit** but is preserved as a distinct outcome. Round **once** to one decimal
  using `SCORE_ROUNDING_DECIMALS` (=1); stamp `SCORING_VERSION`. Overall score
  from `DIMENSION_WEIGHT_TECHNICAL` (0.5) and `DIMENSION_WEIGHT_AEO` (0.5).
  Aggregation (`aggregate_scores`) uses only the **latest completed** analyses for
  **ACTIVE** monitored URLs; do **not** turn missing/error URLs into zero scores.

Fix the ~37 ruff errors (unused imports etc.): `uv run ruff check --fix ...`.

### 6.2 Worker extension — `backend/app/workers/site_health_worker.py`

Define the three missing methods and keep the file a single owner (no parallel
edits from another agent).

**`_run_analyze(task_id, crawl_id)`** — one transaction per task:
1. `_lock_owned_running_task` (task FOR UPDATE + crawl FOR UPDATE; re-check lease
   ownership + status RUNNING + crawl active) — reuse the discover-flow idiom.
2. Fetch the URL via `SecureFetcher` with `FETCH_PURPOSE_ANALYZE` and
   `allowed_content_types=HTML_CONTENT_TYPES`.
3. Parse facts (`extract_page_facts`).
4. Write ONE immutable `SiteFetchArtifact` (populate `normalized_facts` JSONB;
   there is NO raw-body column). Append a `SiteFetchAttempt`.
5. Create `SitePageAnalysis` (unique `artifact_id`).
6. Evaluate rules → `SiteRuleEvaluation` rows (unique `(analysis_id, rule_id)`).
7. For each FAIL, snapshot → `SiteIssue` (unique `evaluation_id`).
8. Compute + store technical/aeo/overall scores + versions on the analysis.
9. succeed/retry/fail the queue row **outside** the txn (heartbeat, `_record_crash`,
   `mark_running`, cooperative cancel — all reused from the discover flow).

**`_run_link_check(task_id, crawl_id)`** — deduped HEAD-first + bounded GET
fallback, bounded by `max_link_checks_per_page` and `link_check_timeout_seconds`.
Write `SiteLinkReference` rows (dedupe on unique
`(source_artifact_id, kind, target_hash, evidence_fingerprint)`). Must not block
the discovery fast path. Cover with ≥1 component test.

**`_reconcile_crawl_status(crawl_id)`** — the highest-risk interaction. It
replaces the unconditional `_finalize_discovery` call in `_execute_task`'s
`finally`. Requirements:
- Introduce an independent **analysis lifecycle**: `analysis_status` driven via
  `apply_analysis_status` (transitions pending→running→completed/partially_completed/failed/cancelled).
- Reconcile the crawl's OVERALL status from BOTH discovery AND analysis substates.
  Terminalize the crawl ONLY when EVERY non-terminal `SiteCrawlTask` (ALL kinds)
  is drained:
  - (a) discovery draining alone must NOT complete the crawl while analyze/link_check are non-terminal;
  - (b) `CRAWL_STATUS_COMPLETED` only when discovery is terminal AND all analyze tasks are terminal AND applicable analyses succeeded;
  - (c) partial coverage → `CRAWL_STATUS_PARTIALLY_COMPLETED` + `ANALYSIS_STATUS_PARTIALLY_COMPLETED`, with NO fabricated zero scores;
  - (d) preserve the Task 3 fully-failed-root behavior (fully_failed → FAILED, partial → PARTIALLY_COMPLETED).
  - Keep the crawl row FOR UPDATE.
- **Why this matters:** all terminal crawl states are empty sets in
  `_CRAWL_TRANSITIONS`. If a completing discover task drives the crawl to
  `completed` while analyze tasks are still queued, a later analysis finalize
  calling `apply_crawl_status()` from a terminal state raises
  `InvalidSiteCrawlTransition`. The single shared reconcile prevents this.
- When analysis terminalizes, compute + persist `SiteHealthSnapshot` (aggregate
  scores/coverage/issue rollups over latest completed analyses for ACTIVE
  monitored URLs, ignoring missing/error), and write `SiteCrawlEvent`
  (`EVENT_ANALYSIS_PROGRESS` / `EVENT_CRAWL_COMPLETED`) via `record_crawl_event`,
  honoring `count_disclosure`.

**Claim ordering note:** Free auto-enqueues analyze tasks at `priority=1` (above
discover's `priority=0`), so with `worker_concurrency` batching a crawl's queued
analyze tasks may be claimed ahead of not-yet-enqueued child discover tasks. This
is fine for correctness — each analyze task independently re-fetches its own URL,
and discovery admission enqueues children transactionally before analyze can act.
Do NOT "fix" this by mutating the shared `claim()` order (it affects the audit
queue too).

### 6.3 Free-sample test to update (don't just make it pass)

`test_free_sample_stops_at_ten_across_two_projects` currently asserts 10 analyze
tasks stay QUEUED (count == 10). Once analyze tasks are claimable and executed,
update the expectations correctly: preserve the workspace-wide free-sample cap of
10 (10 monitored URLs / 10 analyze tasks total), but reflect that the analyze
tasks now get **executed** rather than sitting queued.

### 6.4 Tests required (Task 5)

- **Unit** (`backend/tests/unit/`, local HTML, NO live internet):
  - parser: fact extraction incl. malformed/partial HTML, bounded limits, structured-data validation.
  - rules: each outcome incl. NOT_APPLICABLE and ERROR.
  - scoring: exact formula (passed/failed/error weighting, not_applicable exclusion, error zero-credit, rounding, overall weighting, aggregation ignoring missing/error).
- **Component** (`backend/tests/component/`, real Postgres via `session_factory`,
  fake `DnsResolver` + `httpx.MockTransport`):
  - analyze task → `SitePageAnalysis` + evaluations + issues + scores;
  - crawl NOT completing while analyze queued;
  - crawl COMPLETED only after analysis terminalizes;
  - partial-coverage path (analyze failure → PARTIALLY_COMPLETED, no fabricated zero);
  - link_check → `SiteLinkReference`;
  - `SiteHealthSnapshot` aggregate.

**Component test helpers to reuse** (in `tests/component/test_site_health_worker.py`
and `tests/component/site_health_helpers.py`):
- `seed_site_crawl(session, *, task_count=0, email=None, root_url=...)` → `SiteSeed`.
  It creates a crawl with `status=running` but `discovery_status=pending` and NO
  configuration — your test must set `crawl.discovery_status = DISCOVERY_STATUS_RUNNING`
  and `crawl.configuration` (via `_configure_crawl`) before running the worker.
- `_FakeResolver` (returns public IP `93.184.216.34`), `_ByteStream`
  (MockTransport bodies MUST use `stream=_ByteStream(body)`, not `content=`, to
  avoid `StreamConsumed`), `_site_transport(pages)`, `_html(...)`,
  `_worker(session_factory, pages, *, owner=...)`.
- **Terminal-state gotcha for finalize tests:** a 404 is non-retryable
  (`ERROR_HTTP_4XX` is only retryable on 429), so a 404 task reaches a terminal
  state and reconcile runs. 5xx/timeout are retryable → RETRY_WAIT (non-terminal)
  → reconcile early-returns. Use 404 (not 5xx/timeout) to force terminal-state tests.

### 6.5 Definition of done (Task 5)

- `uv run ruff check .` clean on all new/changed files.
- Full suite `uv run pytest -q` green, count > 254 (all prior tests still pass —
  especially discover-worker component tests, the audit-queue regression subset
  `-k "audit_queue or audit_worker or site_health_queue or concurrent_claims"`,
  and the updated free-sample test).
- Alembic chain still consistent (no new migration).
- Commit + push to `vorflux/site-health`.

---

## 7. TASK 6 — Workspace-safe APIs, keyset cursors, events/SSE, exports

**New file:** `backend/app/api/site_health.py` (does not exist yet). Register the
router in `backend/app/main.py`.

Every endpoint MUST be workspace-scoped and enforce the Task 4 entitlements
(Starter vs Free capability profile). Never leak cross-workspace rows.

Endpoints to provide (align names/paths to the existing API conventions in
`backend/app/api/` — read a sibling router first, e.g. the audit API, and match
its auth dependency, error envelope, and pagination style):
- **Crawl lifecycle:** create/start a crawl (returns crawl id + status), get crawl
  status/summary (statuses, discovery/analysis substates, counts, score summary).
  Enforce `CODE_CRAWL_ALREADY_ACTIVE`, `CODE_STARTER_REQUIRED`,
  `CODE_QUOTA_EXCEEDED`, `CODE_STALE_SELECTION_VERSION` as coded failures.
- **URL inventory (progressive):** list discovered `SiteUrl`s for a project with
  **keyset (cursor) pagination** using the existing `ix_site_urls_project_keyset`
  index `(project_id, normalized_url, id)` — do NOT use OFFSET. Honor
  `count_disclosure`: for Free, strip total-bearing fields (see
  `redact_event_payload` / `_TOTAL_BEARING_KEYS` in `state_events.py`).
- **Selection / monitored set:** stage/commit monitored-URL selection with the
  staged-selection helpers and version guard (`CODE_STALE_SELECTION_VERSION`).
  (Selection domain logic is in `selection.py` — call it, do not modify it.)
- **Analysis results:** per-URL latest `SitePageAnalysis` (scores + rule
  evaluations), issues list filtered by severity/category/rule_id (backed by
  `ix_site_issues_filter`), per-URL issue history (`ix_site_issues_url_created`).
- **Events / SSE:** stream `SiteCrawlEvent`s for a crawl using the config knobs
  `sse_poll_interval_seconds` (2.0) and `sse_max_duration_seconds` (300.0). Redact
  payloads via `redact_event_payload` honoring `count_disclosure`.
- **Exports:** CSV and Markdown export of issues / analysis summary for a crawl.

**Tests:** component tests hitting the API with a real app + Postgres (reuse the
existing API test harness/fixtures). Cover: workspace isolation (a second
workspace cannot read the first's crawl), Free vs Starter disclosure differences,
keyset pagination correctness (stable ordering, no dupes/gaps across pages),
SSE emits events then terminates at max duration, CSV/MD export shape.

**Done when:** ruff clean, full suite green, commit + push.

---

## 8. PAUSE — frontend mockups

Tasks 7 and 8 are UI. The repo owner will provide frontend mockups. **Do not
start Tasks 7–8 until the mockups are in hand.** When they arrive, follow the
existing frontend design system/tokens/components rather than inventing a new look.

## 9. TASK 7 — Site Health discovery / selection / dashboard UI

Build on the Task 2 frontend contracts (zod schemas, query keys,
staged-selection helpers) that already exist. Screens: discovery progress,
URL-inventory browse + monitored-set selection (staged, with version guard), and
the crawl dashboard (scores by dimension, coverage, issue rollups, live progress
via the SSE endpoint). Wire to the Task 6 endpoints. Match the owner's mockups.

## 10. TASK 8 — Issues + per-URL detail UI

Issues list (filter by severity/category/rule) and a per-URL detail view (facts,
rule evaluations with evidence + remediation, link references, score breakdown).
Match the owner's mockups.

## 11. TASK 9 — Integrate, document, end-to-end verify

Wire everything end-to-end, update docs/README for the Site Health feature, and
run a full end-to-end verification (create crawl → discover → select → analyze →
dashboard → issues → export). Final full suite green, commit + push, and open/
update the PR to `main` with a detailed `## Testing` section.

---

## 12. Per-task process (apply to Tasks 5, 6, 9)

For each non-trivial task, after implementation + unit/component tests:
1. Run `ruff` + full `pytest` locally (env as in §1); confirm the count grows and
   nothing regresses.
2. Run a **simplify** pass and a **review** pass over the changes; apply the
   feedback (or explain why a specific item doesn't apply).
3. Run a **full-execution testing** pass (real Postgres); capture evidence.
4. Commit + push to `vorflux/site-health` with a descriptive message.

## 13. Key API surfaces (quick reference)

- **Config:** `backend/app/core/config/site_health.py` — `SiteHealthSettings`
  (env prefix `SITE_HEALTH_`), rules (`SITE_HEALTH_RULES`, `SITE_HEALTH_RULES_BY_ID`,
  `class SiteHealthRule`), `STRUCTURED_DATA_REQUIRED_PROPERTIES`, weights
  (`DIMENSION_WEIGHT_TECHNICAL=0.5`, `DIMENSION_WEIGHT_AEO=0.5`,
  `SCORE_ROUNDING_DECIMALS=1`), versions (`EXTRACTOR_VERSION`, `ANALYZER_VERSION`,
  `RULE_CATALOG_VERSION`, `SCORING_VERSION`), statuses, task kinds, fetch purposes,
  outcomes, severities, categories, error tokens, coded failures, events,
  `HTML_CONTENT_TYPES`, `PERSISTED_RESPONSE_HEADERS`, limits (max_links_per_page=2000,
  max_structured_data_blocks=100, max_text_chars=200000, max_link_checks_per_page=200,
  link_check_timeout_seconds=10.0, sse_poll_interval_seconds=2.0,
  sse_max_duration_seconds=300.0, etc.).
- **Models:** `backend/app/models/site_health.py` — `SiteCrawl`, `SiteUrl`,
  `SiteUrlObservation`, `MonitoredSiteUrl`, `SiteCrawlTask`, `SiteFetchAttempt`,
  `SiteFetchArtifact` (unique `task_id`; `normalized_facts` JSONB; `redacted_headers`;
  `redirect_chain`), `SitePageAnalysis` (unique `artifact_id`; scores nullable),
  `SiteLinkReference` (unique `(source_artifact_id, kind, target_hash, evidence_fingerprint)`),
  `SiteRuleEvaluation` (unique `(analysis_id, rule_id)`), `SiteIssue`
  (unique `evaluation_id`; `ix_site_issues_filter`, `ix_site_issues_url_created`),
  `SiteHealthSnapshot` (unique `crawl_id`), `SiteCrawlEvent`,
  `WorkspaceSiteHealthEntitlement`, `SiteHealthProfile`.
- **State transitions:** `backend/app/domain/site_health/state_events.py` —
  `apply_crawl_status` / `apply_discovery_status` / `apply_analysis_status`,
  `InvalidSiteCrawlTransition`, `redact_event_payload(payload, *, count_disclosure)`,
  `record_crawl_event(session, *, crawl_id, event_type, message="", payload=None, count_disclosure=True)`
  (caller owns commit). All terminal states are empty sets in the transition tables.
- **Discovery/admission:** `backend/app/domain/site_health/discovery.py` —
  `extract_discovery_links`, `build_frontier_candidates`, `admit_candidates`,
  `_enqueue_task` (ON CONFLICT DO NOTHING), `_add_free_sample` (inserts
  `MonitoredSiteUrl(selection_source=free_sample)` + enqueues analyze task at
  `priority=1`), `_task_idempotency_key(crawl_id, task_kind, url_hash, generation)`
  = `f"{crawl_id}:{task_kind}:{url_hash}:{generation}"`.
- **Planner:** `backend/app/domain/site_health/planner.py` — `create_crawl(...)`
  builds the frozen configuration, seeds the root discover task, calls
  `seed_monitored_targets`, drives draft→validating→queued + discovery→running.
- **Schemas:** `backend/app/domain/site_health/schemas.py` — `FrontierCandidate`,
  `DiscoveredLink`, `DiscoveryOutput`, `AdmissionResult`.
- **Queue:** `backend/app/orchestration/postgres_task_queue.py` —
  `PostgresTaskQueue[T]` with `claim(*, owner, limit=1, kinds=None)`
  (adds `task_kind IN (...)` when kinds given), `release_expired`, `heartbeat`,
  `mark_running`, `succeed(*, result_artifact_id=None)`,
  `fail(*, error_code, error_detail)`, `retry(*, delay_seconds, error_code, error_detail)`,
  `cancel(*, task_id)`. **Ordering is shared with the audit queue — do not change it.**
- **Connectors:** `backend/app/connectors/web_evidence/fetcher.py` —
  `SecureFetcher(*, resolver, transport=None, settings=site_health_settings, user_agent=...)`,
  `async fetch(request, *, root_registrable_domain=None, include_globs=None, exclude_globs=None, enforce_scope=False) -> FetchResult`.
  `contracts.py` — `FetchRequest`, `FetchResult`, `RedirectHop`, `FetchError`,
  `DnsResolver` (Protocol), `ResolvedTarget`.
- **Do NOT touch:** `app/analysis/scoring.py` (ai-visibility "B6"), `selection.py`,
  `entitlements.py`, and the `claim()` ordering.



# Searchify — Content (AI generation workspace) + Settings hub — Detailed Plan (v3)

## Summary
Replace the superseded Content CRUD/scratchpad with a real, cancellable AI
content-generation workspace: a project-scoped, immutable `content_generations`
table that doubles as the shared-queue row (the `AuditTask` pattern) plus an
append-only `ContentGenerationAttempt` log, a provider-agnostic
`connectors/discovery_models/*` client (env-driven `SecretStr` key, Mistral
default), a `content_worker` process that commits the claim before the provider
call and discards results for cancelled/lost leases, a default-on deterministic
**Website context** tool projected from Site Health evidence, and a
prompt-box-first `/content` frontend (empty/generating/result/error) rendering
sanitised Markdown with Copy, Regenerate, Cancel, and Try-again. Settings is
aligned to the approved account-hub design. Provider swap = config + one factory
branch; no domain/API/UI change.

## Approved designs (all present; implement against them)
`/code/.plans/designs/design-plan.json` — "AI Content Workspace · Settings (MVP)":
- `content-empty-light.html` / `content-empty-dark.html` (recommended) — prompt
  box, single "Website page" output-type chip, Website-context toggle, Generate.
- `content-generating-light.html` / `content-generating-dark.html` (recommended)
  — indeterminate progress + **Cancel**.
- `content-error-light.html` / `content-error-dark.html` — error state with an
  **editable prompt** + **Try again**.
- `content-result-light.html` / `content-result-dark.html` (recommended) —
  rendered result (h1/h2/h3), **Copy**, **Regenerate**, Website-context
  provenance, review notice.
- `settings-account-light.html` (recommended) / `settings-account-dark.html`.
- `user-dropdown-settings-light.html` (recommended) / `-dark.html` — Settings
  above Sign out.
No design-subagent handoff is needed; these mockups cover the whole surface.

## File structure map

### Backend — new files
- `backend/app/core/config/content.py` — content config (invariant 1): one
  `ContentSettings(BaseSettings)` owner (`env_prefix="CONTENT_"`) with provider
  token, model, endpoint URL, timeouts, `max_output_tokens`; `MISTRAL_API_KEY`
  as a `SecretStr` field aliased under this one owner; output-type frozenset +
  default; prompt length cap; website-context caps; `CONTENT_GENERATOR_VERSION`;
  `CONTENT_MAX_ATTEMPTS`; `_content_claim_order(model)`; `CONTENT_QUEUE_SPEC =
  PostgresQueueSpec[ContentGeneration](...)`.
- `backend/app/models/content.py` — `ContentGeneration` (immutable record + queue-
  lease columns + single-writer result fields) and append-only
  `ContentGenerationAttempt` (unique `(content_generation_id, attempt_number)`).
- `backend/app/domain/content/__init__.py` — package marker.
- `backend/app/domain/content/schemas.py` — `ContentGenerationCreate`,
  `ContentGenerationListItem` (bounded), `ContentGenerationDetail` (full), plus
  `WebsiteContextSummary`. No key/secret fields.
- `backend/app/domain/content/service.py` — workspace-scoped enqueue (with
  Idempotency-Key), list, get, cancel, regenerate, try-again;
  `ContentGenerationNotFoundError`, `ProviderNotConfiguredError`,
  `IdempotencyConflictError`, `CancelNotAllowedError`.
- `backend/app/domain/content/website_context.py` — deterministic, bounded,
  sanitised Website-context projection (pure, testable).
- `backend/app/domain/content/message_builder.py` — fixed system prompt + user
  instruction + separately serialised untrusted context block; returns the
  provider `messages`, a stable `message_digest`, and a safe truncated snapshot.
- `backend/app/connectors/discovery_models/__init__.py` — package marker.
- `backend/app/connectors/discovery_models/contracts.py` — `DiscoveryModelClient`
  protocol + `DiscoveryRequest`/`DiscoveryResponse` dataclasses.
- `backend/app/connectors/discovery_models/factory.py` —
  `build_discovery_client()` (per-attempt; reads provider token; resolves key).
- `backend/app/connectors/discovery_models/mistral.py` — Mistral httpx client.
- `backend/app/workers/content_worker.py` — claim/lease execution loop.
- `backend/app/api/content.py` — flat router `prefix="/content"`.
- `backend/tests/unit/test_content_website_context.py` — projection tests.
- `backend/tests/unit/test_content_message_builder.py` — message-builder +
  adversarial prompt-injection tests.
- `backend/tests/unit/test_discovery_models_mistral.py` — client tests
  (`httpx.MockTransport`; no live network).
- `backend/tests/component/test_content_api.py` — API + worker component tests
  (mock httpx transport).

### Backend — changed files
- `backend/app/orchestration/postgres_task_queue.py` — extend the type union to
  `T: ("AuditTask", "SiteCrawlTask", "ContentGeneration")` (line 51). **No
  `succeed()` refactor** (see decision below): `succeed()` keeps its
  `result_artifact_id` kwarg and audit/site-health behavior unchanged; the content
  worker never calls it (it owns its own atomic finalize helper).
- `backend/app/orchestration/task_queue.py` — mirror the union in the `TaskQueue`
  protocol type/docstring only (signatures unchanged).
- `backend/app/core/config/task_queue.py` — extend `PostgresQueueSpec[T]` union to
  include `ContentGeneration` (line 56).
- `backend/app/models/__init__.py` — import + re-export `ContentGeneration` and
  `ContentGenerationAttempt` (register on `Base.metadata`; add to `__all__`).
- `backend/app/models/project.py` — add `content_generations` relationship
  (`cascade="all, delete-orphan"`, `passive_deletes=True`,
  `order_by="ContentGeneration.created_at.desc()"`) + end-of-module import.
- `backend/app/main.py` — import `content_router`; add to `_ROUTERS`.
- `infra/docker/docker-compose.yml` — add a **single** `content-worker` service
  (`command: ["python", "-m", "app.workers.content_worker"]`) mirroring `worker`,
  reusing the existing `*backend_env` anchor + `.env` `env_file` (no new env
  block).
- `infra/docker/.env.example` — add `CONTENT_PROVIDER`, `CONTENT_MODEL`,
  `MISTRAL_API_KEY`, and provider endpoint/timeout/token-cap keys **once**.

### Frontend — new files
- `frontend/lib/api/content.ts` — `contentApi` owner + exported prompt-length +
  output-type constants; `strictValidate` on every response.
- `frontend/lib/content/use-content-generations.ts` — list query + selected-detail
  poll + enqueue/regenerate/try-again/cancel mutations.
- `frontend/lib/content/markdown.tsx` — the sanitised Markdown renderer wrapper.
- `frontend/components/content/content-screen.tsx` — prompt-box-first screen with
  empty/generating/result/error states.
- `frontend/app/(app)/content/page.tsx` — thin `'use client'` wrapper.
- `frontend/lib/api/content.test.ts`,
  `frontend/lib/content/use-content-generations.test.tsx`,
  `frontend/lib/content/markdown.test.tsx`,
  `frontend/components/content/content-screen.test.tsx` — vitest/MSW tests.
- `frontend/e2e/content.spec.ts` — fast stubbed-UI Playwright spec.
- `frontend/e2e/content-integration.spec.ts` — real-stack spec (see Task 8).

### Frontend — changed files
- `frontend/package.json` — add `react-markdown` + `remark-gfm` dependencies.
- `frontend/lib/api/schemas.ts` — add `contentGenerationListItemSchema` +
  `contentGenerationDetailSchema` + `websiteContextSummarySchema` (`.strict()`;
  ids `uuid()`; status/website-context-status/output-type as `z.enum(...)`).
- `frontend/lib/api/types.ts` — add `ContentGenerationListItem`,
  `ContentGenerationDetail` via `z.infer`.
- `frontend/lib/api/query-keys/core.ts` — add `contentKeys`;
  `frontend/lib/api/query-keys.ts` — wire `content: contentKeys`.
- `frontend/lib/api/index.ts` — spread + re-export `contentApi`.
- `frontend/scripts/check-frontend-architecture.mjs` — add `content.ts` to
  `requiredApiOwners`.
- `frontend/components/layout/nav-items.ts` — flip `Content` to `live: true`.
- `frontend/components/layout/sidebar-nav.test.tsx` — assert Content is a link.
- `frontend/components/settings/settings-screen.tsx` +
  `settings-screen.test.tsx` — align to `settings-account-*.html`.
- `frontend/components/layout/user-menu.tsx` (confirm) + `user-menu.test.tsx`.

### Referenced (not changed)
- `backend/app/models/audit.py` (`AuditTask` queue-row + `ProviderAttempt`
  append-only templates), `backend/app/workers/audit_worker.py` (worker +
  `httpx.MockTransport` test injection template),
  `backend/app/connectors/answer_engines/openai.py` + `errors.py` (httpx adapter +
  neutral error module), `backend/app/analysis/site_health/parser.py`
  (`normalized_facts` shape), `backend/app/models/site_health.py`
  (`SiteCrawl`/`SiteFetchArtifact`/`SitePageAnalysis`/`SiteUrl`/`MonitoredSiteUrl`/
  `SiteHealthProfile`), `backend/app/api/deps.py` (`require_active_workspace`,
  `WorkspaceContext`, `get_db`), `frontend/lib/api/runs.ts` (polling-first),
  `frontend/lib/api/auth.ts` (`/auth/me` `SessionUser`),
  `frontend/lib/project/project-context.tsx` (`useActiveProject`).

## Product / spec detail

### Persona & goal
An authenticated workspace member on the active project wants to generate website
content from a prompt, grounded (by default) in their own crawled pages, keep a
provenance-stamped history, cancel a slow run, retry a failure with the exact
inputs, and regenerate with refreshed context.

### Data model (wire)
`ContentGenerationListItem` (bounded, for the history list):
`{ id, project_id, status, output_type, website_context_status,
requested_model, returned_model | null, provider | null, created_at, updated_at,
completed_at | null, error_code, prompt_preview }` (`prompt_preview` = truncated
prompt; no `output_text`).

`ContentGenerationDetail` (full, for the selected record):
list-item fields **plus** `prompt`, `website_context_enabled`,
`website_context_summary { crawl_id, crawl_completed_at | null, extractor_version,
analyzer_version, page_count, char_count, site_url_ids[], artifact_ids[],
content_hashes[] } | null`, `finish_reason | null`, `output_truncated` (bool),
`output_text | null`, `usage { prompt_tokens?, completion_tokens?, total_tokens? }
| null`, `latency_ms | null`, `error_detail`, `generator_version`.
**Never** includes the API key or a raw request body with the key.

**Model-field consistency + nullability by status:**
- `requested_model` is the config-resolved model, always set at enqueue (never
  null, all statuses).
- `returned_model` is the model the provider echoed; **null** until a successful
  call, non-null only on `succeeded`.
- `provider` is set at enqueue from config (e.g. `"mistral"`) and stays set; it is
  nullable in the wire schema only to tolerate a not-yet-started record, but in
  practice non-null for all statuses. There is **no separate `model` field** — the
  DTO exposes `requested_model` + `returned_model`, not a generic `model`.
- `finish_reason` is **null** until a terminal provider outcome; on `succeeded` it
  is the provider's reason. When `finish_reason == "length"` (Mistral truncation),
  set `output_truncated = true`; the status stays `succeeded` (we keep the visible
  truncated text) and the UI shows a "truncated — may be incomplete" warning.
  `output_truncated` is `false` for all other finish reasons.

`ContentGenerationAttempt` is internal (not exposed in the wire DTO in v1).

### History title / preview
A deterministic history label is derived from the **truncated prompt** (first
line trimmed to a config char cap, single-line). No model call for titles.

### Enqueue behavior (`POST /content/generations`)
- Header `Idempotency-Key` (optional but recommended). Body:
  `{ project_id, prompt, output_type?, website_context_enabled? }`
  (`website_context_enabled` **defaults true**).
- Authorize the project in the active workspace first (invariant 5); unknown/
  cross-workspace → 404 "Project not found".
- **Idempotency** (workspace-scoped, race-safe):
  - Uniqueness is a **composite** `UniqueConstraint(workspace_id,
    idempotency_key)` (`uq_content_generation_ws_idem`) — **not** globally unique —
    so two workspaces may reuse the same client key.
  - Compute a **normalized request fingerprint** = a stable hash over
    `(project_id, trimmed prompt, output_type, website_context_enabled)`; store it
    on the row (`request_fingerprint: String`).
  - When `Idempotency-Key` is present: `SELECT` an existing row for
    `(workspace_id, idempotency_key)`. If found and its `request_fingerprint`
    matches → **replay** (return that record, 200). If found and the fingerprint
    differs → **409** `idempotency_conflict`.
  - Otherwise `INSERT`. Handle the **concurrent insert race**: on
    `IntegrityError` from the composite constraint, roll back, re-`SELECT` the
    now-committed row, and compare its `request_fingerprint` — match → replay,
    mismatch → 409. This makes two simultaneous same-key requests converge on one
    record.
  - When `Idempotency-Key` is absent, generate a server-side unique key
    (`f"{uuid4()}"`) so the composite constraint is always satisfied and no two
    keyless requests collide.
- If the provider is not configured (empty `MISTRAL_API_KEY`) → **409**
  `provider_not_configured`.
- `prompt` trimmed, non-empty after trim, `<= CONTENT_PROMPT_MAX_LEN` (422 else).
- `output_type` defaults to `CONTENT_DEFAULT_OUTPUT_TYPE`; must be in
  `CONTENT_OUTPUT_TYPES` (422 else).
- If `website_context_enabled`, build the context snapshot **synchronously in the
  request** (pure DB projection, no network) and freeze it on the row; set
  `website_context_status` = `included` or `unavailable`. If disabled →
  `disabled`, empty snapshot.
- Build the message digest + safe snapshot and freeze them; insert
  `ContentGeneration` (status `queued`) as the queue row with unique
  `idempotency_key`; return 201 with the queued detail.

### Cancel behavior (`POST /content/generations/{id}/cancel`)
- Workspace-authorize the record (404 if not owned).
- Allowed only when status ∈ `{queued, leased, running, retry_wait}`; otherwise
  **409** `cancel_not_allowed`. On success set `cancelled`, clear lease, set
  `completed_at`, `error_code="cancelled"`.
- The worker, before its terminal write, re-checks owner + status under
  `FOR UPDATE`; if the row is `cancelled` (or the lease was lost), it **discards**
  the provider response and writes nothing but the terminal state (invariant 3 +
  9). An in-flight provider call is not force-aborted; its result is dropped.

### Generation lifecycle (worker)
`queued → (claim, COMMIT) → mark_running → provider call → succeeded | failed |
(cancelled: attempt recorded, output discarded)`.
- Commit the claim before the provider call (invariant 8); heartbeat the lease
  during the call; cooperative cancel at the task boundary.

**Atomic attempt accounting (concrete).** A single worker-owned helper
`finalize_attempt(session, *, generation_id, owner, outcome)` runs **one locked
DB transaction per actual HTTP call** and is the only writer of attempt/terminal
state:
1. `SELECT ... FOR UPDATE` the `content_generations` row by id.
2. **Owner + status re-check**: if `lease_owner != owner` (lease lost) or status is
   already terminal → do nothing but commit (protects immutability, invariant 3).
   If status is `cancelled` → still **append the attempt row** (record the real
   provider-call outcome — success or failure) but **discard `output_text`** and
   keep the row `cancelled`; write no result fields.
3. Otherwise allocate `attempt_number = row.attempt_count + 1`, **increment
   `attempt_count` by exactly one** (one increment per real HTTP call, never per
   poll/retry-scheduling), and `INSERT` a `ContentGenerationAttempt`
   (`(generation_id, attempt_number)` unique) with requested + returned model,
   status, error tokens, `finish_reason`, usage, latency.
4. In the **same transaction**, apply the retry budget and write the matching
   fields together:
   - success → set result fields (`output_text`, `provider`, `requested_model`,
     `returned_model`, `finish_reason`, `output_truncated`, `usage`, `latency_ms`),
     `status=succeeded`, `completed_at`, clear the lease.
   - retryable failure with `attempt_count < CONTENT_MAX_ATTEMPTS` → set
     `status=retry_wait`, `available_at = now + backoff`, `error_code`/
     `error_detail`, clear the lease.
   - non-retryable, or budget exhausted → set `status=failed`,
     `error_code`/`error_detail` (budget exhaustion uses `max_attempts_exceeded`),
     `completed_at`, clear the lease.
5. `COMMIT`. The attempt append + `attempt_count` increment + retry/terminal
   fields are one atomic unit, so a crash mid-write leaves no half-counted attempt.

The content worker calls this helper directly and **does not** use
`PostgresTaskQueue.succeed()` (which only exists to write the audit
`result_artifact_id`); it still uses the queue for `claim`/`heartbeat`/
`mark_running`/`retry`/`fail`/`release_expired`. `retry`/`fail` here are invoked
**through** `finalize_attempt` writing the fields directly, so there is no
mutate-callback indirection and no ambiguity about who writes what.

- **Validate non-empty output**: an empty/whitespace-only `output_text` on an
  otherwise-successful response is treated in step 4 as an `ERROR_PARSE`-class
  failure (retryable per budget), not a success.
- **Cancelled in-flight**: the HTTP call is not force-aborted; when it returns, its
  outcome **is recorded as an attempt** (per step 2) so the provider call is
  auditable, but any generated output is dropped and the row stays `cancelled`.
- **Regenerate** = new `ContentGeneration` from an existing record's prompt +
  output_type, with Website context **rebuilt from the newest eligible crawl**.
- **Try again** = new `ContentGeneration` re-using the failed record's prompt +
  output_type **and its exact frozen `website_context_snapshot`** (reproducible;
  no rebuild).

### Website context (bounded, deterministic, sanitised, default on)
`build_website_context(session, *, workspace_id, project_id) -> WebsiteContext`:
- Select the **newest terminal `SiteCrawl` with usable artifacts** for the
  project (workspace-scoped). "Usable" = has ≥1 `SitePageAnalysis` whose
  `SiteFetchArtifact.normalized_facts` is non-empty. Eligible statuses:
  `completed` **and** `partially_completed` (a partial crawl still yields real
  pages); `failed`/`cancelled` are eligible **only** if they produced usable
  artifacts. If none → `unavailable`, empty block.
- **Page order** (deterministic): homepage first (match
  `SiteHealthProfile.root_url`/`root_host` against `SiteUrl.normalized_url`), then
  **active monitored pages only** (`MonitoredSiteUrl.active == true`, joined to the
  crawl's `SiteUrl`s, stable `normalized_url` order), then remaining crawl pages by
  `normalized_url` asc; cap `CONTENT_CONTEXT_MAX_PAGES`. Ties broken by
  `SiteUrl.normalized_url`, then `SiteUrl.id` — fully deterministic. Inactive
  monitored rows (`active == false`) are ignored.
- For each page emit an **allowlist only** from `normalized_facts`: `title`,
  `meta_description`, `headings.h1_texts[:CONTEXT_MAX_H1]`,
  `headings.h2_texts[:CONTEXT_MAX_H2]`, `body.text[:CONTEXT_PER_PAGE_BODY_CHARS]`,
  plus the page `final_url`. **No** raw HTML, headers, scripts, links, or
  structured-data blobs. (Headings are nested under `facts["headings"]`; body
  under `facts["body"]` — confirmed in `parser.py`.)
- Sanitise: strip control/non-printable chars, collapse whitespace, enforce a
  per-field cap and a total `CONTENT_CONTEXT_MAX_CHARS` budget (truncate in the
  deterministic order; drop trailing pages when the budget is hit).
- Record provenance on the row (`website_context_summary`): `crawl_id`,
  `crawl_completed_at` (`SiteCrawl.completed_at`), `extractor_version` +
  `analyzer_version` (from the source `SiteFetchArtifact`/`SitePageAnalysis`),
  included `site_url_ids`, source `artifact_ids`, per-page `content_hashes` +
  artifact `fetched_at` times, `page_count`, `char_count`. All of this is exposed
  in `ContentGenerationDetail.website_context_summary` so the **design/result UI**
  can show which crawl (and how fresh) grounded the content. Pure and reproducible
  from persisted evidence (invariant 7) — no fetch/extraction.

### Message builder (fixed structure, injection-safe)
`build_messages(*, prompt, output_type, website_context) -> (messages, digest,
snapshot)`:
- `messages[0]` = **fixed system prompt**: role, output-type intent, and an
  explicit directive to treat any reference material as untrusted data and ignore
  instructions embedded in it.
- `messages[1]` = **user instruction** = the user's prompt only.
- `messages[2]` (when context present) = a **separately JSON-serialised**
  untrusted reference block, clearly delimited, never concatenated into the system
  prompt or the user instruction.
- Returns a stable `message_digest` (hash over the serialised messages) and a
  `snapshot` (safe truncated copy for provenance; never the key).

### Provider client (provider-agnostic, env-driven, per-attempt)
- `contracts.py`: `DiscoveryModelClient` Protocol with
  `async generate(request: DiscoveryRequest) -> DiscoveryResponse`.
  `DiscoveryRequest { messages, model, timeout_seconds, max_output_tokens }`.
  `DiscoveryResponse { provider, requested_model, returned_model, output_text,
  finish_reason, usage, latency_ms }`.
- `factory.build_discovery_client()` reads `content_settings.provider` (default
  `"mistral"`); for `"mistral"` returns `MistralDiscoveryClient(...)` resolving the
  key from the `SecretStr` `content_settings.mistral_api_key.get_secret_value()`
  **at call time**. A fresh client is built per attempt. Unknown provider →
  `ProviderError(ERROR_INVALID_SURFACE)`.
- `mistral.py`: httpx `AsyncClient` POST to `content_settings.endpoint` (default
  `https://api.mistral.ai/v1/chat/completions`), body `{model, messages,
  max_tokens, stream:false}`, `Authorization: Bearer <key>`. Reuse `errors.py`
  `classify_provider_status`/`parse_retry_after` + `provider_catalog` `ERROR_*`
  tokens (invariant 2). Parse `choices[0].message.content`, `finish_reason`, the
  returned `model`, and `usage`. Never log the body or key; non-JSON/parse
  failure → `ProviderError(ERROR_PARSE)`.

### Settings (`/settings`)
- Align `settings-screen.tsx` to `settings-account-*.html`: **account role,
  account status, account created, user id, email** (all read-only, from
  `GET /auth/me`), an appearance/theme toggle, and links to `/providers` +
  `/setup` only. No fabricated fields; no BYOK UI; no editable fields.
- Confirm the user menu shows **Settings directly above Sign out** (already wired
  in `user-menu.tsx`); keep the `user-menu.test.tsx` ordering assertion.

### Frontend `/content` (states match the 8 designs)
- **Empty/ready** (`content-empty-*`): prompt textarea ("Describe what you want to
  write…"), a single "Website page" output-type chip (static/disabled, seeded from
  config; not a multi-option picker), a **Website context** toggle (default on) as
  the **only** tool/connector, Generate. No GitHub/Notion/CMS affordances.
- **Generating** (`content-generating-*`): an **indeterminate** progress
  indicator (no fake percentage) while the selected record is
  `queued|leased|running|retry_wait`, and a **Cancel** button that calls the
  cancel mutation. The composer is **locked** (prompt + Generate disabled) until
  the selected record is terminal or cancelled.
- **Result** (`content-result-*`): render `output_text` as **sanitised Markdown**
  (see below) with an explicit **"AI-generated, untrusted — review before use"**
  notice, `requested_model`/`returned_model` + usage/latency provenance, the
  Website-context provenance summary (which crawl + `crawl_completed_at` +
  extractor/analyzer versions), a **truncation warning** when `output_truncated`
  is true ("Output was truncated by the model — may be incomplete"), **Copy**
  (clipboard), and **Regenerate**.
- **Error** (`content-error-*`): show `error_code`/`error_detail`, an **editable
  prompt textarea** pre-filled from the failed record, **Try again** (re-enqueue
  from the exact frozen snapshot; if the user edited the prompt, that becomes a
  normal new enqueue), and a **Dismiss** action (see below).
- **Dismiss behavior**: Dismiss clears the **locally displayed mutation error**
  (calls the enqueue/cancel mutation `reset()` and clears any local error state)
  and returns the composer to the **editable ready** state, **preserving the
  current prompt text and the Website-context toggle value**. It does not delete
  or mutate any persisted record; it only dismisses the transient error surface.
- Generate is disabled when prompt is empty-after-trim, during an active-project
  transition, while an enqueue mutation is pending, or while the selected record
  is non-terminal. On 409 `provider_not_configured`, show an inline message (no
  key entry UI); on 409 `idempotency_conflict`, surface a retry hint.
- **Polling** (exact intervals + stop conditions):
  - History **list** query: `refetchInterval` = `CONTENT_LIST_POLL_MS` (3000ms)
    **only while** at least one visible item is non-terminal (status ∈
    `queued|leased|running|retry_wait`); returns `false` (stops) when every item
    is terminal.
  - **Selected-detail** query: `refetchInterval` = `CONTENT_DETAIL_POLL_MS`
    (2000ms) while the selected record is non-terminal; returns `false` (stops) as
    soon as it is terminal (`succeeded|failed|cancelled`). Both constants live in
    the `content.ts` API owner (invariant 1).
- **List limit**: the list request sends `?limit=` (default `CONTENT_LIST_DEFAULT_LIMIT`
  = 50, clamped server-side to `CONTENT_LIST_MAX_LIMIT` = 100); the query key
  includes `limit`; the response is a bounded array (newest-first, capped). Both
  limit constants live in `config/content.py`; the API owner re-exports the default.
- No-project state links to `/setup`; project switch resets composer + clears
  errors; the history query key includes `projectId` (and `limit`).
- **Accessibility**: Cancel/Generate/Copy/Regenerate/Try-again are focusable
  buttons with labels; the generating indicator uses `role="status"`
  `aria-live="polite"`; tests cover keyboard activation + focus of the error
  textarea.

### Sanitised Markdown rendering
- Add `react-markdown` + `remark-gfm`. **Do not** add `rehype-raw` — raw HTML
  stays disabled (default), so embedded `<script>`/HTML never renders.
- `frontend/lib/content/markdown.tsx` wraps `<ReactMarkdown>` with: a restricted
  `components`/allowlist (headings, paragraphs, lists, emphasis, code, blockquote,
  links, tables via gfm) and a `urlTransform` that permits only
  `http`/`https`/`mailto` schemes (drops `javascript:`/`data:`), with
  `rel="noopener noreferrer"` on links. Token-only classes.
- Tests assert: markdown renders headings/lists; a `javascript:` link is
  neutralised; raw `<script>`/`<img onerror>` HTML is not executed/rendered.

## Acceptance criteria
1. `content_generations` + `content_generation_attempts` are created from
   `Base.metadata` (registered in `models/__init__.py`); recreating a
   **disposable** DB picks them up with no new revision file.
2. `POST /api/v1/content/generations {project_id, prompt}` returns 201 with a
   `queued` detail scoped to the caller's active workspace project;
   `website_context_enabled` defaults true; missing provider config → 409
   `provider_not_configured`.
3. Idempotency: uniqueness is composite `(workspace_id, idempotency_key)` (two
   workspaces may reuse a key); repeating the same key + matching
   `request_fingerprint` returns the same record (replay); the same key with a
   different fingerprint → 409 `idempotency_conflict`; two concurrent same-key
   inserts converge on one record via the `IntegrityError` reload/compare path.
4. Prompt validation: empty/whitespace → 422; over `CONTENT_PROMPT_MAX_LEN` → 422;
   unknown `output_type` → 422; default output type applied when omitted.
5. List/get: `GET /content/generations?project_id=&limit=` authorizes the project
   first, returns only that project's records newest-first as **bounded list items**
   (no `output_text`), capped at `min(limit, CONTENT_LIST_MAX_LIMIT)` with default
   `CONTENT_LIST_DEFAULT_LIMIT`; unknown/cross-workspace `project_id` → 404.
   `GET /content/generations/{id}` returns the **full detail**; cross-workspace/
   random id → 404. No response ever contains the API key.
6. Cancel: `POST /content/generations/{id}/cancel` succeeds for
   `queued|leased|running|retry_wait` (→ `cancelled`), returns 409
   `cancel_not_allowed` for terminal records, and enforces workspace auth (404
   otherwise). A worker whose HTTP call returns after the row was cancelled
   **records the attempt** (provider outcome auditable) but discards the generated
   output and leaves the row `cancelled`.
7. Worker (mock httpx transport): claims a queued record, commits the claim before
   the call, and via the atomic `finalize_attempt` helper appends exactly one
   `ContentGenerationAttempt` per HTTP call and increments `attempt_count` by one;
   on success writes `output_text` + `provider` + `requested_model` +
   `returned_model` + `finish_reason` + `output_truncated` + `usage` + `latency_ms`
   + `status=succeeded` exactly once (attempt + counter + terminal fields in one
   transaction); a provider failure records `status=failed` + tokens; an empty
   output → parse failure (retry per budget); a `finish_reason=="length"` success
   sets `output_truncated=true` and stays `succeeded`; a retryable error retries up
   to `CONTENT_MAX_ATTEMPTS` then fails `max_attempts_exceeded`; a lost lease writes
   no result fields (immutability).
8. Website context: with an eligible crawl, the snapshot contains only allowlisted
   fields (title, meta, `headings.h1/h2`, `body.text`, final_url), respects page +
   char caps, orders homepage → **active** monitored → stable URL (inactive
   monitored rows ignored), is deterministic across runs, strips control chars, and
   records `crawl_id`/`crawl_completed_at`/`extractor_version`/`analyzer_version`/
   `site_url_ids`/`artifact_ids`/`content_hashes`/`page_count`/`char_count`;
   `partially_completed` with usable artifacts is eligible; with no usable crawl →
   `unavailable` and generation runs prompt-only; disabled toggle → `disabled`, empty.
9. Message builder: system prompt, user instruction, and untrusted context are
   separate messages; an adversarial "ignore previous instructions" string
   embedded in page text stays inside the reference block and never merges into
   the system/user messages; `message_digest` is stable for identical inputs.
10. Regenerate creates a new record with context rebuilt from the newest eligible
    crawl; Try again creates a new record re-using the failed record's exact frozen
    context snapshot; the originals are never mutated.
11. Provider swap: changing `CONTENT_PROVIDER` routes through the factory with no
    domain/API/UI edit (factory test: unknown provider → invalid_surface; `mistral`
    builds the Mistral client).
12. Security: the key is a `SecretStr`, never returned in any DTO, never logged,
    and never in the frozen request/message snapshot; a test asserts the serialized
    record + attempt contain no key substring.
13. Queue: the generic queue serves three task types
    (`AuditTask`/`SiteCrawlTask`/`ContentGeneration`) via the extended type union;
    `succeed()` is unchanged and audit/site-health suites stay green; the content
    worker finalizes via its own `finalize_attempt` helper (no artifact id needed).
14. `/content` renders in the app shell matching the 8 designs; nav Content is a
    live link; enqueue → poll → sanitised-Markdown output with provenance + Copy +
    Regenerate work; Cancel stops a running generation; Try again re-runs a failed
    one; Dismiss clears the error and returns to the ready composer preserving the
    prompt; Settings renders the approved layout and sits above Sign out.
15. Markdown safety: `javascript:`/`data:` URLs are neutralised and raw HTML is not
    rendered; `output_truncated` renders a visible truncation warning.
16. Polling: list polls at 3000ms only while an item is non-terminal and stops when
    all are terminal; selected detail polls at 2000ms and stops on terminal.
17. `cd backend && uv run ruff check .` and `cd frontend && pnpm lint`/
    `pnpm check:policy` pass.

## Tasks

### Task 1 — Queue generalization + config + data model [parallel]
Extend the shared queue to a third task type and lay the config + persistence
foundation. Files: `backend/app/orchestration/postgres_task_queue.py`,
`backend/app/orchestration/task_queue.py`, `backend/app/core/config/task_queue.py`,
`backend/app/core/config/content.py`,
`backend/app/core/config/__init__.py`, `backend/app/models/content.py`,
`backend/app/models/__init__.py`, `backend/app/models/project.py`; tests
`backend/tests/unit/test_content_config.py`,
`backend/tests/component/test_task_queue_content.py`.

- **Queue union**: extend `T: ("AuditTask", "SiteCrawlTask", "ContentGeneration")`
  in `postgres_task_queue.py` (line 51), the `task_queue.py` protocol type, and
  `config/task_queue.py` `PostgresQueueSpec` (line 56). This is a **type-only**
  change — every method signature (incl. `succeed()`) is unchanged.
- **No `succeed()` refactor** (decision): the content worker never calls
  `succeed()` (the only method that writes the audit `result_artifact_id`); it
  owns an atomic `finalize_attempt` helper (Task 3) that writes attempt + counter +
  terminal fields in one transaction. This avoids mutate-callback ambiguity and
  leaves audit/site-health untouched. `AUDIT_QUEUE_SPEC` and both existing workers
  are unchanged.
- **Config** (`config/content.py`, invariant 1): one `ContentSettings(BaseSettings,
  env_prefix="CONTENT_")` owner: `provider="mistral"`, `model="mistral-small-latest"`,
  `endpoint="https://api.mistral.ai/v1/chat/completions"` (env
  `CONTENT_PROVIDER_ENDPOINT`), `request_timeout_seconds`, `max_output_tokens`,
  `mistral_api_key: SecretStr = SecretStr("")` (validation alias `MISTRAL_API_KEY`),
  `lease_ttl_seconds`. Constants: `CONTENT_OUTPUT_TYPES`
  (`frozenset({"website_page"})`), `CONTENT_DEFAULT_OUTPUT_TYPE`,
  `CONTENT_PROMPT_MAX_LEN`, `CONTENT_HISTORY_TITLE_MAX_LEN`,
  `CONTENT_LIST_DEFAULT_LIMIT` (50), `CONTENT_LIST_MAX_LIMIT` (100), website-context
  caps (`CONTENT_CONTEXT_MAX_PAGES`, `CONTEXT_MAX_H1`, `CONTEXT_MAX_H2`,
  `CONTENT_CONTEXT_PER_PAGE_BODY_CHARS`, `CONTENT_CONTEXT_MAX_CHARS`),
  `CONTENT_GENERATOR_VERSION`, `CONTENT_MAX_ATTEMPTS`; reuse `TASK_STATUS_*` from
  `config/task_queue.py`; `_content_claim_order(model)` (priority desc,
  available_at asc, randomized_position asc, mirroring `_audit_claim_order`);
  `CONTENT_QUEUE_SPEC = PostgresQueueSpec[ContentGeneration](model_ref=…,
  lease_ttl=lambda: content_settings.lease_ttl_seconds, claim_order=…,
  max_attempts_error=ERROR_MAX_ATTEMPTS)`. Note: the only output cap is
  `max_output_tokens` (sent to the provider); there is **no** separate unused
  output-length cap.
- **Model** (`models/content.py`): `ContentGeneration(Base)` — UUID PK;
  `workspace_id` FK + `project_id` FK (`ondelete=CASCADE`, indexed); frozen inputs
  `prompt: Text`, `output_type: String`, `website_context_enabled: Boolean`,
  `website_context_status: String`, `website_context_snapshot: JSONB` (allowlisted
  fields + provenance ids + counts; **no key**), `request_fingerprint: String`
  (indexed), `message_digest: String`, `message_snapshot: JSONB`; shared
  queue-lease columns exactly as `AuditTask` (`idempotency_key` String(128),
  `status` indexed default queued, `priority`, `available_at` indexed,
  `lease_owner`, `lease_expires_at`, `heartbeat_at`, `attempt_count` default 0,
  `max_attempts`, `randomized_position` default 0, `error_code`, `error_detail`,
  `completed_at`); single-writer result fields `output_text: Text`,
  `provider: String`, `requested_model: String`, `returned_model: String`,
  `finish_reason: String`, `output_truncated: Boolean` default False, `usage: JSONB`,
  `latency_ms: Integer`, `request_snapshot: JSONB` (never the key),
  `generator_version: String`; `created_at`/`updated_at`; `attempts` relationship.
  `__table_args__` = **composite** `UniqueConstraint("workspace_id",
  "idempotency_key", name="uq_content_generation_ws_idem")` (NOT a global unique on
  `idempotency_key`).
  `ContentGenerationAttempt(Base)` — UUID PK, `content_generation_id` FK
  (`ondelete=CASCADE`, indexed), `attempt_number: Integer`, `status`,
  `requested_model`, `returned_model`, `finish_reason`, `error_code`,
  `error_detail`, `usage: JSONB`, `latency_ms`, `created_at`;
  `UniqueConstraint("content_generation_id", "attempt_number")`. Register both in
  `models/__init__.py` (+`__all__`); add the `content_generations` relationship on
  `Project` with the end-of-module import pattern.
- **Tests**: `test_content_config.py` — spec builds, claim order shape, output-type
  membership, list-limit constants, `SecretStr` default empty.
  `test_task_queue_content.py` — a `PostgresTaskQueue[ContentGeneration]`
  claims/heartbeats/retries/fails/cancels a content row against a disposable DB;
  the composite `(workspace_id, idempotency_key)` allows the same key in two
  workspaces and rejects a duplicate within one; existing audit/site-health queue
  suites still pass (unchanged `succeed()`).

**Verify:** `cd backend && uv run pytest tests/unit/test_content_config.py tests/component/test_task_queue_content.py tests/component/test_audit_queue*.py -q && uv run ruff check .`.

### Task 2 — Provider client + website context + message builder [after 1]
Files: `backend/app/connectors/discovery_models/{__init__,contracts,factory,mistral}.py`,
`backend/app/domain/content/{__init__,website_context,message_builder}.py`; tests
`backend/tests/unit/test_discovery_models_mistral.py`,
`backend/tests/unit/test_content_website_context.py`,
`backend/tests/unit/test_content_message_builder.py`.

- **Contracts/factory/mistral** as specified in the spec section. Template:
  `answer_engines/openai.py`; reuse `answer_engines/errors.py` + `provider_catalog`
  `ERROR_*` (invariant 2). Factory builds a fresh client per call, resolving the
  `SecretStr` at call time. No shared code with answer-engine adapters beyond
  `errors.py` (`content-writer.md` §10).
- **Website context** (`website_context.py`): pure `build_website_context(...)` —
  newest terminal crawl with usable artifacts; homepage → monitored → stable-URL
  ordering; allowlist from nested `normalized_facts` (`headings.h1_texts`/
  `headings.h2_texts`, `body.text`); sanitise + per-field/total caps; provenance
  (`crawl_id`/`site_url_ids`/`artifact_ids`/`content_hashes`/counts);
  `partially_completed` eligible when it has usable artifacts.
- **Message builder** (`message_builder.py`): fixed system prompt + user
  instruction + separately serialised untrusted context; returns `(messages,
  digest, snapshot)`.
- **Tests**: mistral parse/success (returned model + finish_reason + usage) + error
  classification + no-key-leak + factory unknown-provider (mock httpx transport, no
  network); website-context determinism/allowlist/caps/sanitise/ordering/
  partially_completed/unavailable; message-builder structure + **adversarial
  prompt-injection** (embedded "ignore instructions" stays in the reference block)
  + stable digest.

**Verify:** `cd backend && uv run pytest tests/unit/test_discovery_models_mistral.py tests/unit/test_content_website_context.py tests/unit/test_content_message_builder.py -q && uv run ruff check .`.

### Task 3 — Content service, worker, API + component tests [after 2]
Files: `backend/app/domain/content/{schemas,service}.py`,
`backend/app/workers/content_worker.py`, `backend/app/api/content.py`,
`backend/app/main.py`; tests `backend/tests/component/test_content_api.py`.

- **Schemas** (`schemas.py`): `ContentGenerationCreate` (trim+non-empty prompt,
  length cap, output-type membership, `website_context_enabled` default true),
  `ContentGenerationListItem` (bounded, `prompt_preview`, no `output_text`),
  `ContentGenerationDetail` (full, `from_attributes=True`, no key; incl.
  `output_truncated` + provenance fields), `WebsiteContextSummary`.
- **Service** (`service.py`): `_project_in_workspace(...)` guard;
  `enqueue_generation(..., idempotency_key)` — compute `request_fingerprint`;
  workspace-scoped idempotency `SELECT`/compare (replay vs `IdempotencyConflictError`),
  concurrent-insert `IntegrityError` reload/compare/replay-or-409;
  provider-config check → `ProviderNotConfiguredError`; build website context +
  message digest/snapshot; insert queued row (server-side key when header absent);
  `list_generations(..., limit)` (authorize project first, newest-first, bounded
  items, `limit` clamped to `CONTENT_LIST_MAX_LIMIT`); `get_generation(...)`
  (workspace-scoped detail); `cancel_generation(...)` (workspace-authorized queue
  `cancel`; `CancelNotAllowedError` for terminal); `regenerate(...)` (new row,
  rebuild context); `try_again(...)` (new row, reuse frozen snapshot). Errors:
  `ContentGenerationNotFoundError(LookupError)`,
  `ProviderNotConfiguredError(RuntimeError)`, `IdempotencyConflictError`,
  `CancelNotAllowedError`.
- **Worker** (`content_worker.py`): `ContentWorker` mirroring `AuditWorker` —
  `PostgresTaskQueue[ContentGeneration]` with `CONTENT_QUEUE_SPEC`, `claim(limit=1)`,
  commit claim before the call, `mark_running`, heartbeat loop, cooperative cancel,
  build client via factory per attempt. All attempt/terminal writes go through the
  worker-owned **`finalize_attempt(session, *, generation_id, owner, outcome)`**
  helper described in the lifecycle section: `SELECT ... FOR UPDATE`, owner+status
  re-check (lost lease → no-op; `cancelled` → record attempt, discard output),
  allocate `attempt_number`, increment `attempt_count` once, insert
  `ContentGenerationAttempt`, apply retry budget, and write retry/terminal fields
  in the **same transaction**. The worker does **not** call `queue.succeed()`.
  Non-empty-output validation + `output_truncated` from `finish_reason=="length"`.
  `def main()` entrypoint. Inject the discovery client (real from factory in prod;
  a client over `httpx.MockTransport` in tests).
- **Router** (`api/content.py`): `APIRouter(prefix="/content", tags=["content"])`;
  `GET /generations?project_id=&limit=` → list; `POST /generations` (201, reads
  `Idempotency-Key` header) → enqueue; `GET /generations/{id}` → detail;
  `POST /generations/{id}/regenerate` (201); `POST /generations/{id}/try-again`
  (201); `POST /generations/{id}/cancel`. Map errors:
  `ProviderNotConfiguredError`→409, `IdempotencyConflictError`→409,
  `CancelNotAllowedError`→409, `ContentGenerationNotFoundError`→404. Register in
  `main.py` `_ROUTERS`.
- **Tests** (`test_content_api.py`): enqueue 201/validation 422s/provider 409;
  idempotency replay (same key+fingerprint) + conflict 409 (same key, different
  body) + concurrent-insert convergence; list scoping (bounded items) + `limit`
  cap + 404; detail + cross-workspace 404; cancel allowed/terminal-409/auth-404;
  run the worker with a mock httpx transport and assert exactly-one attempt +
  one `attempt_count` increment per call, single-writer success (requested/returned
  model + finish_reason + `output_truncated` + usage + latency), failure tokens,
  empty-output→retry, `finish_reason=="length"`→`output_truncated`, retry budget →
  `max_attempts_exceeded`, cancelled-in-flight (attempt recorded, output discarded,
  row stays cancelled), lost-lease-writes-no-result; regenerate rebuilds context,
  try-again reuses the frozen snapshot; response + attempt never contain the key.

**Verify:** `cd backend && uv run pytest tests/component/test_content_api.py -q && uv run ruff check .`.

### Task 4 — Frontend content contract + API owner + query keys [after 3]
Files: `frontend/lib/api/schemas.ts`, `frontend/lib/api/types.ts`,
`frontend/lib/api/query-keys/core.ts`, `frontend/lib/api/query-keys.ts`,
`frontend/lib/api/content.ts`, `frontend/lib/api/index.ts`,
`frontend/scripts/check-frontend-architecture.mjs`,
`frontend/lib/api/content.test.ts`.

- `schemas.ts`: `websiteContextSummarySchema`, `contentGenerationListItemSchema`,
  `contentGenerationDetailSchema` — all `.strict()`, ids `uuid()`, `status` /
  `website_context_status` / `output_type` as `z.enum(...)` matching backend.
  List item includes `requested_model` (string), `returned_model` (nullable),
  `provider` (nullable). Detail adds `prompt`, `website_context_enabled`,
  nullable `website_context_summary` (via `websiteContextSummarySchema`:
  `crawl_id`, `crawl_completed_at` nullable, `extractor_version`,
  `analyzer_version`, `page_count`, `char_count`, `site_url_ids[]`,
  `artifact_ids[]`, `content_hashes[]`), `finish_reason` (nullable),
  `output_truncated` (boolean), nullable `output_text`/`usage`/`latency_ms`/
  `completed_at`, `error_detail`, `generator_version`. Match the pydantic DTOs
  exactly (no generic `model` field).
- `types.ts`: `ContentGenerationListItem` + `ContentGenerationDetail` +
  `WebsiteContextSummary` via `z.infer`.
- `query-keys/core.ts`: `contentKeys = { all, list(projectId, limit),
  detail(id) }` — `list` includes `limit` in the key so different caps cache
  separately; wire in `query-keys.ts`.
- `content.ts`: `contentApi` (`listGenerations(projectId, limit?)`,
  `enqueueGeneration(input, idempotencyKey)`, `getGeneration(id)`,
  `regenerate(id)`, `tryAgain(id)`, `cancel(id)`) each with `strictValidate`;
  `listGenerations` sends `?project_id=&limit=` (default `CONTENT_LIST_DEFAULT_LIMIT`);
  export `CONTENT_PROMPT_MAX_LEN`, output-type constant, `CONTENT_LIST_DEFAULT_LIMIT`,
  `CONTENT_LIST_POLL_MS` (3000), `CONTENT_DETAIL_POLL_MS` (2000) (invariant 1, one
  owner). Relative `/api/v1`, `credentials:'include'` via `apiClient`. Add to
  `index.ts` spread/re-export and to `requiredApiOwners`.
- `content.test.ts`: MSW list (bounded, asserts `limit` query param sent +
  default applied)/enqueue/detail/cancel; assert `strictValidate` throws on
  drift (numeric id, missing `output_truncated`/`requested_model`) — mirror
  `lib/api/site-health.test.ts`.

**Verify:** `cd frontend && pnpm test -- lib/api/content.test.ts lib/api/schemas.test.ts && pnpm check:policy`.

### Task 5 — Frontend content screen + sanitised Markdown + hook + route + nav flip [after 4]
Files: `frontend/package.json`, `frontend/lib/content/use-content-generations.ts`,
`frontend/lib/content/markdown.tsx`, `frontend/components/content/content-screen.tsx`,
`frontend/app/(app)/content/page.tsx`, `frontend/components/layout/nav-items.ts`,
`frontend/components/layout/sidebar-nav.test.tsx`,
`frontend/lib/content/use-content-generations.test.tsx`,
`frontend/lib/content/markdown.test.tsx`,
`frontend/components/content/content-screen.test.tsx`, `frontend/e2e/content.spec.ts`.

- **Deps**: add `react-markdown` + `remark-gfm` to `package.json`.
- **Markdown** (`markdown.tsx`): sanitised renderer — raw HTML disabled (no
  `rehype-raw`), restricted component allowlist, `urlTransform` allowing only
  http/https/mailto, `rel="noopener noreferrer"`, token-only classes.
- **Hook** (`use-content-generations.ts`): list query with
  `refetchInterval` = `CONTENT_LIST_POLL_MS` (3000ms) **only while** any visible
  item is non-terminal, returning `false` (stop) when all are terminal;
  selected-detail query with `refetchInterval` = `CONTENT_DETAIL_POLL_MS` (2000ms)
  while the selected record is non-terminal, `false` on terminal (like `runs.ts`);
  the list query key includes `projectId` + `limit`. Plus enqueue/regenerate/
  try-again/cancel mutations invalidating the list. Expose per-mutation
  pending/error/reset.
- **Screen** (`content-screen.tsx`): the four states matching the designs —
  empty/ready (prompt + output-type chip + default-on Website-context toggle +
  Generate), generating (indeterminate `role="status"` progress + Cancel, composer
  locked), result (sanitised-Markdown output + untrusted notice + `requested_model`/
  `returned_model` + Website-context provenance + a **truncation warning** rendered
  when `output_truncated` is true + Copy + Regenerate), error (editable prompt
  textarea + Try again + **Dismiss**). Disabled rules + 409 messages as specified;
  no-project → `/setup`; project-switch reset; token-only classes; no
  GitHub/Notion/CMS UI.
- **Dismiss action**: calls the enqueue/cancel mutation `reset()` and clears any
  local error state, returning the composer to the editable **ready** state while
  **preserving the current prompt text and the Website-context toggle value**. It
  mutates no persisted record — only the transient error surface.
- **Route**: `'use client'` wrapper rendering `<ContentScreen/>`.
- **Nav flip**: set Content `live: true` in `nav-items.ts` (visible to every
  authenticated workspace member — the feature is globally live, not gated);
  `sidebar-nav.test.tsx` asserts a link.
- **Tests**: markdown (renders md; neutralises `javascript:`; no raw HTML/script);
  hook (list load, polls queued→succeeded at 3000ms and stops when terminal,
  detail polls at 2000ms and stops on terminal, cancel transitions,
  enqueue/regenerate/try-again invalidate, error/refetch); screen (Generate
  disabled empty/while running, generating shows Cancel + locks composer, Cancel
  calls mutation, result renders Markdown + provenance + Copy clipboard,
  `output_truncated` renders the truncation warning, Regenerate + Try-again
  enqueue, **Dismiss clears the error and restores the ready composer with the
  prompt + toggle preserved**, 409 messages, toggle default on, no-project +
  project-switch reset, keyboard activation + error-textarea focus); fast stubbed
  `e2e/content.spec.ts` (stub `/auth/me`, `/projects`,
  `/content/generations` GET+POST+detail+cancel) confirming the live nav link +
  enqueue→output + cancel flows.

**Verify:** `cd frontend && pnpm test -- lib/content components/content components/layout && pnpm lint && pnpm build`.

### Task 6 — Settings layout alignment + tests [independent, after 4]
Files: `frontend/components/settings/settings-screen.tsx`,
`frontend/components/settings/settings-screen.test.tsx`,
`frontend/components/layout/user-menu.tsx` (confirm),
`frontend/components/layout/user-menu.test.tsx`.

- Align `settings-screen.tsx` to `settings-account-*.html`: read-only **email,
  account role, account status, account created, user id** (from `/auth/me`), an
  appearance/theme toggle, and links to `/providers` + `/setup` only. No fabricated
  fields, no BYOK UI, no editable fields; token-only.
- Keep/confirm Settings directly above Sign out in `user-menu.tsx`; keep the
  `user-menu.test.tsx` ordering assertion.
- Update `settings-screen.test.tsx` for the aligned structure + real links.

Independent of Content code (shares no files); can run in parallel with Task 5
once Task 4's contract types exist.

**Verify:** `cd frontend && pnpm test -- components/settings components/layout/user-menu.test.tsx && pnpm lint`.

### Task 7 — Compose service + env example [after 3]
Files: `infra/docker/docker-compose.yml`, `infra/docker/.env.example`.

- Add a **single** `content-worker` service to `docker-compose.yml` mirroring
  `worker` (reuse the `*backend_env` anchor + `.env` `env_file`, `depends_on` db +
  migrate; `command: ["python", "-m", "app.workers.content_worker"]`). Do **not**
  duplicate the environment block.
- Add to `.env.example` **once**: `CONTENT_PROVIDER=mistral`,
  `CONTENT_MODEL=mistral-small-latest`, `MISTRAL_API_KEY=` (empty; provider
  disabled until set), and the provider endpoint/timeout/token-cap keys.

**Verify:** `docker compose -f infra/docker/docker-compose.yml config` parses; the
`content-worker` service and env keys appear exactly once.

### Task 8 — Real-stack integration test [after 5, 6, 7]
Files: `frontend/e2e/content-integration.spec.ts`,
`frontend/e2e/content-integration.config.ts` (dedicated Playwright config/project),
`frontend/e2e/helpers/real-stack.ts` (disposable-DB + mock-provider + process
lifecycle helper).

- **Disposable Postgres lifecycle** (`real-stack.ts`, run in the spec's
  `beforeAll`/`afterAll`): derive a unique DB name `searchify_e2e_<runid>` (runid =
  short uuid); connect to the admin/maintenance DB from a base
  `E2E_ADMIN_DATABASE_URL` and `CREATE DATABASE searchify_e2e_<runid>`; build the
  worker/API `DATABASE_URL` pointing at it; run schema creation from
  `Base.metadata` against that DB (`cd backend && DATABASE_URL=… uv run python -m
  app.scripts.create_schema` or the existing greenfield create step — never
  alembic against a real DB); on teardown terminate connections and
  `DROP DATABASE searchify_e2e_<runid>` in a `finally` so a failed assertion still
  drops it.
- **Mock Mistral server** (`real-stack.ts`): start a local `http.createServer`
  (or a Playwright-managed Node script) on an ephemeral port that answers
  `POST /v1/chat/completions` with an OpenAI-compatible body (`choices[0].message.
  content` = deterministic Markdown, `finish_reason`, `model`, `usage`), supports a
  **slow/delay mode** (configurable delay, for the Cancel assertion), and records
  the received `Authorization` header. **No fake-provider branch exists in app
  code** — the swap is purely `CONTENT_PROVIDER_ENDPOINT` pointing at this server,
  so the real `MistralDiscoveryClient` + parsing run end to end.
- **Process env + startup** (`real-stack.ts`): boot the API server, the
  `content_worker` (`python -m app.workers.content_worker`), and the Next.js
  frontend, all sharing:
  - `DATABASE_URL` = the disposable DB URL.
  - `CONTENT_PROVIDER=mistral`, `CONTENT_MODEL=mistral-small-latest`,
    `MISTRAL_API_KEY=dummy-e2e-key` (non-empty so the provider is "configured"),
    `CONTENT_PROVIDER_ENDPOINT=http://127.0.0.1:<mockPort>/v1/chat/completions`.
  - frontend `BACKEND_ORIGIN=http://127.0.0.1:<apiPort>` (the server-only origin
    Next.js `rewrites()` proxies `/api/:path*` to — invariant 12; the browser
    still calls relative `/api/v1`).
  - **Readiness**: poll the API health route, the frontend `baseURL`, and the mock
    server root until each responds (bounded timeout) before running specs.
  - **Cleanup**: kill the worker, API, frontend, and mock server, then drop the DB,
    all in `finally`.
- **Playwright config ownership**: `content-integration.config.ts` defines its own
  `webServer`/global-setup pointing at `real-stack.ts` (it does **not** reuse the
  default `playwright.config.ts` single `pnpm dev` webServer, which assumes the
  dev backend). Run it explicitly with `--config e2e/content-integration.config.ts`.
- **Assertions** (`content-integration.spec.ts`): seed via public API (register →
  workspace auto-created; create a project; optionally seed a Site Health crawl
  artifact or accept `unavailable` context). Then: enqueue → appears queued →
  polls to succeeded with sanitised-Markdown output + provenance; reload persists
  it; Cancel a slow run (mock delay mode) → `cancelled` + no output but the attempt
  is recorded; Regenerate + Try-again create new records; cross-workspace isolation
  (a second workspace's active project never lists the first's generations); the
  mock server saw `Authorization: Bearer dummy-e2e-key` (key flows to the provider,
  never to any DTO).

**Verify:** real stack + disposable DB + local mock endpoint:
`cd frontend && pnpm test:e2e -- --config e2e/content-integration.config.ts`.

### Task 9 — Docs [after 8]
Files: `docs/backend-architecture.md`, `docs/frontend-architecture.md`,
`docs/roadmap/content-writer.md`, `README.md`, `infra/docker/README.md`.

- Backend arch: add `content_generations` + `content_generation_attempts` to the
  persistence-model table and `/content` router + `content_worker` +
  `connectors/discovery_models` to the surface map, marked **live/implemented (v1)**;
  note the env-driven `SecretStr` content model + its deliberate difference from BYOK
  measurement keys; note the type-only generic-queue extension to three task types
  (`succeed()` unchanged) + the content worker's own atomic `finalize_attempt`.
- Frontend arch: add `content.ts` owner + `/content` route + hook + sanitised-Markdown
  renderer as **live**.
- `content-writer.md`: top note that a **basic v1** (env-driven single output type +
  default-on Website-context tool, cancel, no briefs/revisions/CMS) shipped; the richer
  brief/draft/revision design remains roadmap.
- README + `infra/docker/README.md`: document `CONTENT_PROVIDER` (default `mistral`),
  `CONTENT_MODEL` (default `mistral-small-latest`), `MISTRAL_API_KEY`, provider
  endpoint/timeout/token caps, the `content-worker` compose service, and **Railway**
  deployment: the content worker is a **separate Railway service** with start command
  `python -m app.workers.content_worker`, sharing the same env (incl.
  `MISTRAL_API_KEY`) as the web + audit-worker services.
- **Do NOT** add a migration revision file — the tables come from `Base.metadata`
  (greenfield policy).

**Verify:** docs/env accurately describe the finished vertical; no stray migration
edits; `docker compose config` parses.

## Testing
Per task:
- Task 1: `cd backend && uv run pytest tests/unit/test_content_config.py tests/component/test_task_queue_content.py tests/component/test_audit_queue*.py -q && uv run ruff check .`.
- Task 2: `cd backend && uv run pytest tests/unit/test_discovery_models_mistral.py tests/unit/test_content_website_context.py tests/unit/test_content_message_builder.py -q && uv run ruff check .`.
- Task 3: `cd backend && uv run pytest tests/component/test_content_api.py -q && uv run ruff check .`.
- Task 4: `cd frontend && pnpm test -- lib/api/content.test.ts lib/api/schemas.test.ts && pnpm check:policy`.
- Task 5: `cd frontend && pnpm test -- lib/content components/content components/layout && pnpm lint && pnpm build`.
- Task 6: `cd frontend && pnpm test -- components/settings components/layout/user-menu.test.tsx && pnpm lint`.
- Task 7: `docker compose -f infra/docker/docker-compose.yml config`.
- Task 8: real stack + disposable DB + local mock endpoint: `pnpm test:e2e -- content-integration.spec.ts`.
- Task 9: manual doc review + `docker compose -f infra/docker/docker-compose.yml config`.

Schema recreation (greenfield) — **disposable DB only**:
```bash
cd backend
DATABASE_URL="postgresql+asyncpg://<user>:<pass>@<host>/searchify_scratch_<runid>" \
  uv run alembic downgrade base && \
DATABASE_URL="postgresql+asyncpg://<user>:<pass>@<host>/searchify_scratch_<runid>" \
  uv run alembic upgrade head
```
Never run downgrade/upgrade against the developer's real dev database.

Final integration verification:
- Backend: `cd backend && uv run pytest -q && uv run ruff check .`.
- Frontend: `cd frontend && pnpm lint && pnpm build && pnpm test && pnpm check:policy`.
- E2E: fast `pnpm test:e2e -- content.spec.ts`; real-stack
  `pnpm test:e2e -- content-integration.spec.ts` (disposable DB, local mock endpoint).

## Dependencies & ordering rationale
- **Task 1** (queue generalization + config + model) is the foundation; start
  immediately. The queue change is **type-only** (the union gains
  `ContentGeneration`); `succeed()` and both existing workers are untouched, so
  audit/site-health stay green.
- **Task 2** (client + context + message builder) depends on Task 1 (config + spec).
- **Task 3** (service + worker + API) depends on Task 2 (client, context, messages).
- **Task 4** (frontend contract) depends on Task 3 (zod must match the real DTOs).
- **Task 5** (screen + Markdown) depends on Task 4 (imports the API owner + keys).
- **Task 6** (Settings) is independent of Content; can run in parallel with Task 5
  after Task 4.
- **Task 7** (compose/env) depends on Task 3 (worker entrypoint exists).
- **Task 8** (real-stack E2E) depends on Tasks 5 + 6 + 7 (finished UI + worker + env).
- **Task 9** (docs) depends on Task 8 (describe the verified vertical).

## Risks
- **Queue extension blast radius.** The union gains a third type but every method
  signature (incl. `succeed()`) is unchanged, so audit + site-health workers are
  untouched; the mitigation is re-running the existing queue/worker suites in
  Task 1 to confirm the type-only change is inert. The content worker never calls
  `succeed()`; it writes attempt + counter + terminal fields through its own atomic
  `finalize_attempt` helper (`FOR UPDATE`, one transaction).
- **Env-vs-BYOK deviation.** The content key is env-driven (`MISTRAL_API_KEY`,
  `SecretStr`), unlike measurement BYOK (invariant 6). User-approved; mitigated by
  reading the key only at call time and asserting (test) it never appears in any
  DTO/attempt/log/snapshot. Do not route the content client through
  `ProviderConnection`/`build_adapter`.
- **Prompt injection via website context.** Untrusted site text is allowlisted,
  sanitised, capped, and kept in a separate serialised block with an
  ignore-embedded-instructions system directive; adversarial unit tests cover it.
- **Cancel/immutability race.** The worker's `FOR UPDATE` owner+status re-check before
  the terminal write discards results for cancelled/lost leases (invariants 3 + 9);
  covered by a component test.
- **Markdown safety.** Raw HTML stays disabled (no `rehype-raw`) and URL schemes are
  restricted; tests assert `javascript:`/raw-HTML neutralisation.
- **Greenfield migration confusion.** Register on `Base.metadata`; never add a
  revision file; recreate only against a disposable `DATABASE_URL`.
- **Frontend architecture guard / strict schemas.** Add `content.ts` to
  `requiredApiOwners`, keep `index.ts` transport-free, match zod to pydantic
  (`.strict()` throws on drift).

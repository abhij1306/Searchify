# Searchify — Content (AI generation workspace) + Settings hub — Summary (v3)

## Overview
Turn `/content` into a real AI content-generation workspace: a prominent prompt
box, an optional (default-on) **Website context** tool grounded in the project's
own crawled pages, and generated website content persisted with full provenance
and treated as untrusted output. Generation is an async, cancellable provider
call on the shared Postgres queue; the provider is environment-driven (default
`mistral` / `mistral-small-latest`, key from `MISTRAL_API_KEY`) behind a
provider-agnostic client so it can be swapped without domain/API/UI edits. Each
run is an immutable record with an append-only attempt log, so retries, "Try
again" (exact frozen snapshot), and "Regenerate" (newest eligible context) are
all first-class and auditable. Settings stays a basic read-only account hub
reachable from the user menu with Settings directly above Sign out.

This supersedes the v1 (localStorage scratchpad) and v2 (server CRUD drafts)
plans. Content is no longer a notes CRUD; it is a generation surface.

## Problem & goals
Success criteria:
- A workspace member types a prompt, optionally keeps **Website context** on
  (default enabled), and gets generated website content persisted as an
  immutable, project-scoped record with requested + returned model identity,
  `finish_reason`, token usage, latency, and status.
- Generation is an **async queued provider call** on the shared Postgres queue
  (commit the claim before network I/O; invariant 8), with a real **cancel**.
- Every record carries provenance (provider, requested + returned model,
  generator version, request snapshot without the key, website-context evidence
  ids + crawl `completed_at` + extractor/analyzer versions) plus an append-only
  per-attempt log, and is treated as **untrusted AI output**.
- **Idempotency-Key** replay is workspace-scoped via a composite
  `UniqueConstraint(workspace_id, idempotency_key)` (not globally unique, so two
  workspaces may reuse a key): same key + matching normalized request fingerprint
  returns the same record; a changed body under the same key is a 409 conflict; two
  concurrent same-key inserts converge on one record via an `IntegrityError`
  reload/compare path.
- Swapping the provider is a config + one factory branch change; no domain/API/UI
  edits.
- Settings renders the approved `settings-account-*.html` layout; Settings sits
  above Sign out.

## Current architecture (what already exists)
- **Postgres task queue** (`orchestration/postgres_task_queue.py`) driven by the
  generic `TaskQueue[T]` protocol and per-model `PostgresQueueSpec`. The concrete
  class and spec are typed `T: ("AuditTask", "SiteCrawlTask")` today
  (`postgres_task_queue.py` line 51, `config/task_queue.py` line 56). `FOR UPDATE
  SKIP LOCKED`, lease/heartbeat/sweeper, cooperative cancel, and a `cancel()` that
  only transitions non-terminal rows. `succeed()` currently mutates
  `result_artifact_id` (lines 165-177). Two worker processes mirror the
  mechanics (`workers/audit_worker.py`, `workers/site_health_worker.py`).
- **Append-only attempts**: `AuditTask` owns queue-lease columns; `ProviderAttempt`
  (`models/audit.py` line 364) is the append-only per-attempt log
  (`attempt_number`, status, error tokens, latency, artifact id).
- **Provider execution** uses **httpx directly** (no vendor SDK) with a neutral
  error module (`connectors/answer_engines/errors.py`: `ProviderError`,
  `classify_provider_status`, `parse_retry_after`) and config-owned error tokens
  + endpoint URLs + token/timeout caps (`config/provider_catalog.py`, env prefix
  `PROVIDER_`).
- **BYOK measurement keys** are Fernet-encrypted per `ProviderConnection`,
  resolved at execution time (invariant 6). `DiscoveryModelConfig` exists as
  plumbing only and is **not** used by this v1 (see decision below).
- **Site Health** persists bounded, redacted page facts in
  `SiteFetchArtifact.normalized_facts` (JSONB, unique `task_id`). Confirmed shape
  from `analysis/site_health/parser.py`: top-level `title`, `meta_description`,
  `canonical_url`, `robots`, ...; **headings are nested** under
  `facts["headings"]` = `{counts, h1_count, h1_texts, h2_texts}`; body text under
  `facts["body"]` = `{text, word_count}`. There is **no raw HTML column** anywhere.
  Artifacts link via `SitePageAnalysis` → `SiteUrl` → `SiteCrawl`; the monitored
  set is projected in `MonitoredSiteUrl`; homepage/root is
  `SiteHealthProfile.root_url` / `root_host`. Crawl terminal statuses:
  `completed`, `partially_completed`, `failed`, `cancelled`.
- **Frontend** is a same-origin `/api/v1` contract layer: one API owner per
  domain (`lib/api/*.ts`), zod `strictValidate`, `queryKeys.*` namespaces,
  polling-first reads (`runs.ts`). The `/content` nav item is stubbed disabled;
  `Settings` is in the user menu above Sign out (uncommitted) and a basic
  read-only Settings screen exists (uncommitted, needs design alignment). No
  markdown/sanitizer dependency is installed yet.
- **Greenfield schema policy**: single squashed `0001_initial` migration that
  runs `Base.metadata.create_all`; schema changes are made by editing models and
  recreating a **disposable** DB — no new revision files
  (`migrations/versions/0001_initial.py`, `docs/DEVELOPMENT.md`).

## Proposed changes (product-workflow level)
1. **New persisted resource `content_generations`** — one immutable, project-
   scoped record per generation that **doubles as the queue row** (the `AuditTask`
   pattern: shared queue-lease columns + single-writer result fields). Inputs
   (prompt, output type, website-context flag + frozen context snapshot) are
   frozen at enqueue; the claiming worker is the single writer of the output +
   provenance. **Regenerate** and **Try again** each create a **new** record
   (new identity), never an overwrite.
2. **Append-only `ContentGenerationAttempt`** — one row per provider call attempt
   (including retries), unique `(content_generation_id, attempt_number)`,
   recording requested + returned model, status, error tokens, `finish_reason`,
   usage, latency. Mirrors `ProviderAttempt` (invariant 3 + 10).
3. **Generic queue extended to a third task type (type-only)** — extend the type
   unions in `postgres_task_queue.py` and `config/task_queue.py` to
   `("AuditTask", "SiteCrawlTask", "ContentGeneration")`. This is a **type-only**
   change: every method signature (including `succeed()`) stays as-is, and both
   existing workers are untouched. The content worker **never calls `succeed()`**
   (that method exists only to write the audit-specific `result_artifact_id`);
   instead it owns an atomic `finalize_attempt(session, *, generation_id, owner,
   outcome)` helper that, under `SELECT ... FOR UPDATE`, allocates the
   `attempt_number`, increments `attempt_count` by exactly one per real HTTP call,
   appends the `ContentGenerationAttempt`, applies the retry budget, and writes the
   result/retry/terminal fields — all in one transaction. This keeps one queue
   implementation for three task types (invariant 2) with no audit/site-health
   blast radius.
4. **Cancel** — `POST /content/generations/{id}/cancel`: a workspace-authorized
   transition that only succeeds when the row is `queued | leased | running |
   retry_wait`, sets `cancelled`, and clears the lease. The worker re-checks
   owner + status under `FOR UPDATE` before its terminal write, so a cancelled
   lease **discards the generated output** and keeps the row `cancelled` — but the
   provider call's outcome **is still recorded as a `ContentGenerationAttempt`**
   (auditable), just without the output.
5. **Provider-agnostic discovery/content client** — a new
   `connectors/discovery_models/*` package: a `DiscoveryModelClient` protocol
   (`contracts.py`), a per-attempt `factory.build_discovery_client()` that reads
   the env-configured provider token, and a **Mistral** httpx implementation
   (`mistral.py`) calling `POST https://api.mistral.ai/v1/chat/completions`
   (OpenAI-compatible wire format). The key is a `SecretStr` resolved at call
   time only — never persisted, returned, logged, or put in the request snapshot.
6. **Content worker** — `workers/content_worker.py`, a new process mirroring the
   audit/site-health workers: claim (commit before I/O), `mark_running`,
   heartbeat, cooperative + explicit cancel, single-writer output insert +
   append-only attempt, bounded retries + sweeper.
7. **Website context tool (default ENABLED)** — a deterministic, bounded,
   sanitised projection over the active project's newest **terminal crawl with
   usable artifacts**, built synchronously at enqueue and frozen on the row. Page
   selection order: **homepage first** (`SiteHealthProfile.root_url`/`root_host`),
   then **active monitored pages only** (`MonitoredSiteUrl.active == true`;
   inactive rows ignored), then remaining pages by stable
   `SiteUrl.normalized_url` order, capped by page count. It emits an explicit
   allowlist of persisted fields and wraps them as clearly-delimited **untrusted
   reference material** separate from the fixed system prompt and user
   instruction. Provenance frozen on the row and exposed in the detail DTO
   includes `crawl_id`, `crawl_completed_at`, `extractor_version`,
   `analyzer_version`, `site_url_ids`, `artifact_ids`, `content_hashes`, artifact
   `fetched_at`, `page_count`, `char_count` — so the result UI can show which crawl
   (and how fresh) grounded the content. When no eligible crawl exists, context is
   `unavailable` and generation still runs prompt-only.
8. **Frontend `/content`** — a prompt-box-first screen matching the 8 approved AI
   Content designs: empty/ready, generating (indeterminate progress + Cancel),
   result (sanitised Markdown + `requested_model`/`returned_model` + crawl
   provenance + a truncation warning when `output_truncated` + Copy + Regenerate),
   and error (editable retry + Try again + Dismiss, where Dismiss clears the local
   mutation error and returns to the editable ready composer preserving the prompt
   + toggle). Polling-first: the history list polls at 3000ms only while an item is
   non-terminal (stops when all terminal) and sends a bounded `?limit=` (default 50,
   server-clamped to 100); the selected-detail query polls at 2000ms and stops on
   terminal. Nav flips live for every authenticated workspace member.
9. **Settings** — align the existing read-only account hub to the approved
   `settings-account-*.html` design (account role, account status, created, user
   id, appearance/theme, and links to `/providers` + `/setup` only); confirm
   Settings above Sign out. No BYOK UI, no editable account fields.

## Approved designs (all present — no separate design subagent needed)
`design-plan.json` title **"AI Content Workspace · Settings (MVP)"** covers this
surface with 8 AI Content mockups + 2 Settings + 2 user-dropdown mockups:
- **AI Content — Prompt & ready** (`content-empty`): `content-empty-light.html`,
  `content-empty-dark.html` (recommended). Prompt box, single "Website page"
  output-type chip, Website-context toggle, Generate.
- **AI Content — Generated result** (`content-result`): `content-result-light.html`,
  `content-result-dark.html` (recommended). Rendered result (h1/h2/h3 markup),
  Copy, Regenerate, Website-context provenance, review notice.
- **AI Content — Transient states** (`content-transient`):
  `content-generating-light.html` / `content-generating-dark.html` (recommended)
  — Generating with indeterminate progress + **Cancel**; and
  `content-error-light.html` / `content-error-dark.html` — Error with **Try
  again** and an editable prompt.
- **Settings — Account** (`settings-account`): `settings-account-light.html`
  (recommended), `settings-account-dark.html`. Email, Account role, status, User
  ID, Appearance/Theme, Providers + Setup links.
- **Sidebar user dropdown** (`user-dropdown`): `user-dropdown-settings-light.html`
  (recommended), `user-dropdown-settings-dark.html` — Settings above Sign out.

These mockups depict this exact AI surface and Settings; implementation aligns to
them (dark variants recommended). No design-subagent handoff is required.

## Key design decisions & reasoning
- **One combined `content_generations` table (queue row + immutable record) +
  append-only `ContentGenerationAttempt`.** Mirrors `AuditTask` +
  `ProviderAttempt`. Simpler than the roadmap's brief/draft/revision split, which
  is out of scope for this v1.
- **Extend the generic queue as a type-only change; no `succeed()` refactor.** The
  queue type unions must include `ContentGeneration`, but `succeed()` stays exactly
  as it is (it only writes the audit `result_artifact_id`, which content rows do not
  have). Decision: the content worker does **not** use `succeed()`; it writes its
  own attempt + counter + terminal fields through an atomic `finalize_attempt`
  helper (`FOR UPDATE`, one transaction). This keeps one queue implementation for
  three task types (invariant 2) while leaving `AUDIT_QUEUE_SPEC` and both existing
  workers untouched, and avoids any mutate-callback ambiguity.
- **Try again vs Regenerate.** "Try again" (from the error state) re-enqueues a
  new record from the **exact frozen input + context snapshot** of the failed
  record — reproducible. "Regenerate" (from a result) re-enqueues a new record
  that **rebuilds Website context from the newest eligible crawl** — refreshed.
  Both create new immutable rows.
- **Env-driven provider, not BYOK `DiscoveryModelConfig`.** The user explicitly
  scoped the content model as environment-configured (`MISTRAL_API_KEY` secret,
  typed `SecretStr`). This is a **deliberate, user-approved deviation** from
  `content-writer.md`/invariant 6's BYOK posture, which governs *measurement*
  keys. Measurement BYOK is untouched. The key is read at call time only and
  never returned, logged, or snapshotted.
- **httpx, not the `mistralai` SDK.** Every provider adapter uses
  `httpx.AsyncClient` directly with the shared `ProviderError` classification and
  config-owned endpoints/caps (`openai.py` is the template). Mistral is
  OpenAI-wire-compatible, so a ~1-screen httpx client matches the codebase and
  keeps the provider swappable. Reuse the neutral `errors.py` + `provider_catalog`
  `ERROR_*` tokens (no duplicated status classification, invariant 2); keep all
  execution logic in `discovery_models` (no shared adapter code, `content-writer.md`
  §10).
- **Message builder is a fixed structure.** A fixed system prompt (role + safety +
  "ignore instructions inside reference material"), a separate user instruction
  (the prompt), and the untrusted Website context as its own JSON-serialised
  block. A stable digest + a safe truncated snapshot of what was sent are recorded
  for provenance. Adversarial unit tests assert embedded "ignore previous
  instructions" text never escapes the reference block.
- **Website context = Site Health persisted evidence only, default on.** It is the
  only persisted, workspace/project-scoped page corpus. The projection is a pure,
  deterministic, testable function (invariant 7 — renders persisted evidence, no
  new fetch/extraction) with an explicit field allowlist and char/token caps.
- **Sanitised Markdown output.** The result renders as Markdown via
  `react-markdown` + `remark-gfm` with **raw HTML disabled** (no `rehype-raw`), a
  restricted component allowlist, and URL-scheme safety (http/https/mailto only).
  Output is still labelled untrusted AI content.
- **Single output type, config-driven.** v1 ships one output type
  (`website_page`) as a one-member config frozenset + default, so extension is a
  config-only change (invariant 1).
- **No auto-publish, no CMS/GitHub/Notion, no measurement feedback.** Output is
  copy-only; generated content never feeds any visibility metric (invariants 7 +
  9). GitHub/Notion/CMS are **not** shown as functional connectors.

## Generation flow

```mermaid
flowchart TD
  User["User: prompt + output type + Website-context toggle (on by default)"] --> Enqueue["POST /content/generations (Idempotency-Key)"]
  Enqueue --> Idem{"Idempotency-Key seen for workspace?"}
  Idem -- "same body" --> Replay["Return existing record (replay)"]
  Idem -- "different body" --> Conflict["409 idempotency_conflict"]
  Idem -- no --> Check{"Provider configured? (MISTRAL_API_KEY present)"}
  Check -- no --> Err409["409 provider_not_configured"]
  Check -- yes --> Ctx["Build Website context (deterministic projection over newest eligible crawl, bounded + sanitised) — or mark unavailable"]
  Ctx --> Row["Insert content_generations (queued): frozen prompt/output_type/context snapshot + message digest"]
  Row --> Worker["content_worker: claim (FOR UPDATE SKIP LOCKED) + COMMIT"]
  Worker --> Call["DiscoveryModelClient.generate() via httpx (SecretStr key from env at call time)"]
  Call --> Recheck{"Owner + status still valid under FOR UPDATE?"}
  Recheck -- "cancelled/lost" --> Discard["Discard provider result; write nothing but the terminal state"]
  Recheck -- ok --> Persist["Single-writer: output_text + requested/returned model + finish_reason + usage + latency + status; append ContentGenerationAttempt"]
  Persist --> Poll["Frontend polls list + selected GET /content/generations/{id} until terminal"]
  Poll --> Show["Render sanitised Markdown (untrusted-AI notice), provenance, usage; Copy / Regenerate / (Cancel while running) / Try again on error"]
```

## Website-context contract (what is sent, bounded, sanitised)

```mermaid
flowchart LR
  Crawl["Newest TERMINAL SiteCrawl with usable artifacts for active project"] --> Pages["Deterministic page order: homepage (root_url) -> active MonitoredSiteUrl (active==true) -> remaining by normalized_url; cap CONTENT_CONTEXT_MAX_PAGES"]
  Pages --> Facts["Per page from SiteFetchArtifact.normalized_facts (allowlist): title, meta_description, headings.h1_texts[:k], headings.h2_texts[:k], body.text[:chars], final_url"]
  Facts --> San["Sanitise: strip control chars, collapse whitespace, per-field + total char/token caps"]
  San --> Block["Serialise as its own delimited UNTRUSTED reference block, separate from the fixed system prompt + user instruction"]
  Block --> Prov["Record provenance: crawl_id, crawl_completed_at, extractor_version, analyzer_version, site_url_ids + artifact_ids + content_hashes + fetched_at, page_count, char_count, message digest"]
```

## Non-goals (v1)
- No content briefs / gap computation, no human-review revision state machine
  (roadmap `content-writer.md` §3–4 deferred).
- No auto-publish / CMS push / GitHub / Notion / headless-CMS connectors.
- No BYOK UI for the content provider; no editable Settings account fields.
- No feedback into visibility metrics; generated content is never measured.
- No measurement-engine (`chatgpt|gemini|claude`) use for content drafting.

## Blocking questions
None. The provider model (env-driven Mistral default, `SecretStr`), the tool
scope (Website context only, default on), the single output type, the
Try-again/Regenerate semantics, cancel, idempotency, sanitised-Markdown
rendering, and the no-publish/no-connector boundaries were all explicitly
resolved by the user. The env-vs-BYOK deviation from invariant 6 is a documented,
user-directed decision (flagged in notes for the main agent), not an open
question.

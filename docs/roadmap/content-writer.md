# Roadmap — Content (writer)

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

An **AEO-oriented content workflow**: help a brand generate and optimize the pages and answers
that make it more likely to be **cited by answer engines** (ChatGPT / Gemini / Claude). The
surface turns measured visibility gaps — prompts where the brand is *not* cited or where
competitors dominate — into **content briefs**, then produces reviewable **drafts** and tracks
human edits through to a publish-ready state.

This surface is powered by the **discovery/analysis model**, **not** a measurement engine. The
master plan (v2 §2.3) keeps these two concepts strictly separate: measurement engines
(`chatgpt|gemini|claude` via direct or OpenRouter transports) are the *products being measured*;
the discovery/analysis model is a *separately configured* generative LLM used for brand
understanding, prompt suggestion, clustering, and — here — content drafting. Because the
discovery model is a generative LLM, its output is **never** used to compute any headline
visibility metric (invariant 9). Content is a downstream *action* surface that reads the
visibility slice; it never feeds back into it.

Positioning boundaries:
- The **brief** is a deterministic projection over persisted analysis (invariant 7). It answers
  "which prompts / themes / owned pages need content, and why" purely from stored evidence.
- The **draft** is generative (discovery model), immutable per generation (invariant 3), and
  carries provenance back to the brief + the exact evidence rows that motivated it (invariant 4).
- Human review/edit lives in a separate mutable **revision** row so drafts stay immutable.

## 2. Relationship to the visibility slice (what a "gap" is)

A content brief is seeded from **already-persisted** MVP analysis — it performs no new
extraction and calls no measurement provider (invariant 7). The gap signal is derived from:

- `ResponseAnalysis` + `Citation` (`models/analysis.py`): prompts/executions where **no owned
  citation** appears (`classification != "owned"` across repetitions) → "brand absent" gap.
- `CompetitorMention` / competitor `Citation`: prompts where competitors are cited but the brand
  is not → "competitor dominates" gap, ranked by competitor share-of-voice.
- `MetricSnapshot` projection fields (brand-mention rate, owned-citation rate, mention→owned
  conversion, share-of-voice) to prioritize which gaps are worth a brief.
- `Prompt.theme` / `Prompt.intent` (`models/prompt.py`, `PROMPT_INTENTS` in
  `config/projects.py`) to group gaps and pick a content angle (discovery vs comparison vs
  purchase intent changes the brief template).
- (Roadmap cross-references) `SiteIssue` from [`technical-audit.md`](technical-audit.md) —
  "owned page exists but has thin content / missing structured data" — and `Opportunity` rows
  from [`opportunities.md`](opportunities.md) can each seed a brief.

The gap computation is a deterministic query + ranking; it never asks the discovery model "where
are we weak?". The LLM is only invoked to **write**, after the deterministic brief is fixed.

## 3. Data model (new tables — UUID PKs, workspace-scoped)

All tables are workspace-scoped through their `project_id` (invariant 5); no `user_id` columns,
no integer PKs. Mirror the existing `models/*` shape (typed `Mapped`, `PGUUID(as_uuid=True)`
defaults, `JSONB` for structured provenance).

- **`ContentBrief`** — the deterministic instruction for one piece of content. `id`,
  `workspace_id`, `project_id`, `title`, `brief_type` (`answer|page|faq`, from a config enum),
  `target_prompt_ids` (JSONB list of the `Prompt` UUIDs the brief targets),
  `target_theme` / `target_intent` (mirrors `Prompt` fields), `gap_summary` (JSONB: the
  computed gap — absent/competitor-dominated, SOV, which competitors), **`evidence`** (JSONB:
  the concrete `ResponseAnalysis` / `Citation` / `CompetitorMention` / `MetricSnapshot` /
  `SiteIssue` row ids that motivated it), `source_analyzer_version` + `brief_formula_version`
  (provenance + version, invariant 4), `status`, timestamps. The brief holds **no generated
  prose** — it is a projection (invariant 7).
- **`ContentDraft`** — an **immutable, written-once** generated draft (invariant 3). `id`,
  `workspace_id`, `project_id`, `brief_id` (FK), `version` (monotonic per brief; a re-generation
  creates a **new** row, never an overwrite), `generation_id` (the stable generation/task
  identity — see the idempotency note below), `body` (the generated markdown/HTML), `outline`
  (JSONB), `citations_suggested` (JSONB: owned URLs/sources the draft was told to reference),
  **the discovery-model identity triple** `logical_engine` + `transport_provider` +
  `transport_model` (invariant 10 — recorded exactly as `ProviderRoute` / `DiscoveryModelConfig`
  do), `discovery_config_id` (FK → `DiscoveryModelConfig`), `generation_request_snapshot`
  (JSONB — **never contains the API key or the brand/competitor list**, invariant 6),
  `generation_metadata` (JSONB: token usage, latency), `analyzer_version` (the brief formula +
  generator version), `created_at`. Exactly one writer (the worker that claimed the generation
  task) — no later stage edits it.

  **Idempotent, single-writer inserts under concurrency/retries (invariants 3 + 8).** Monotonic
  `version` alone is **not** enough: two concurrent requests, or a reclaimed worker after a lease
  expiry, could each compute "next version = N" and insert duplicate drafts. Two constraints
  close the race on the immutable insert:
  - a **unique `(brief_id, version)`** constraint, so two writers racing for the same next
    version conflict on insert instead of both succeeding; and
  - a **stable `generation_id`** — the generation/task identity (the `ContentGenerationTask`
    `idempotency_key`) carried onto the draft, with a **unique `generation_id`** constraint, so a
    retry or reclaim of the *same* generation conflicts on that identity rather than minting a
    duplicate draft/version.

  A conflicting insert is caught and treated as "already generated" (the winning row is
  returned), never an overwrite — preserving monotonic versioning + written-once semantics.
- **`ContentRevision`** — the **mutable** human review/edit layer, kept separate so drafts stay
  immutable. `id`, `workspace_id`, `project_id`, `draft_id` (FK to the draft the human started
  from), `edited_body`, `review_state` (state machine below), `reviewer_user_id` *(the one
  place a `user_id` appears — audit attribution of who edited, NOT an access-scoping column;
  access is still enforced by `workspace_id`/`require_workspace_member`, invariant 5)*,
  `review_notes`, `created_at`, `updated_at`. Editing produces new revision content on the same
  row; the underlying `ContentDraft` never changes.
- **`ContentGenerationTask`** *(optional, if generation is not folded onto the shared queue)* —
  a queue+lease row identical in contract to `AuditTask` (`lease_owner`, `lease_expires_at`,
  `heartbeat_at`, `attempt_count`, `max_attempts`, unique `idempotency_key`, `status`), claimed
  with `FOR UPDATE SKIP LOCKED` (invariant 8). Prefer extending the existing `TaskQueue`
  Protocol / `PostgresTaskQueue` over introducing a second queue (invariant 2 — grep before
  add).

The **brief templates, draft-type enum, and gap thresholds live in config** (§7), never inline
in service code (invariant 1).

## 4. Generation lifecycle (queued task)

Draft generation is a queued task, not a synchronous request — the discovery-model call is
network I/O and must obey the queue rules (invariant 8):

```
brief (deterministic) → enqueue ContentGenerationTask (idempotency key)
  → worker claims (FOR UPDATE SKIP LOCKED, COMMIT before the LLM call)
  → DiscoveryModelClient.generate() (discovery/analysis model, NOT a measurement engine)
  → write ContentDraft (immutable) with model-identity triple + provenance to brief/evidence
  → ContentRevision seeded in DRAFT review_state for human review
```

Rules that carry over verbatim:
- **Commit the claim before the LLM call.** Never hold a DB transaction open across the network
  call to the discovery model (invariant 8).
- The worker **heartbeats** the lease; the **sweeper** returns expired leases to `retry_wait`,
  or `failed` after `max_attempts`.
- **Cooperative cancel only** — a cancel stops the worker at the generation boundary (before the
  next draft), never mid-call (invariant 9).
- The brand/competitor list is **not** sent to the discovery provider as part of the prompt
  (invariant 6); the brief passes only the neutral gap description + owned-source URLs.
- The generation is **not deterministic** and is explicitly excluded from any headline metric —
  drafts are actions, not measurements (invariant 9).
- The immutable `ContentDraft` insert carries the task's `idempotency_key` as its
  `generation_id` and relies on the unique `(brief_id, version)` + unique `generation_id`
  constraints (see §3), so a reclaimed worker or concurrent request conflicts on that identity
  instead of duplicating a draft/version (invariants 3 + 8).

**Review state machine** (`ContentRevision.review_state`), following the `audit_state.py`
`_ALLOWED_TRANSITIONS` pattern:

```
DRAFT → IN_REVIEW → EDITED → APPROVED
DRAFT/IN_REVIEW → REJECTED
APPROVED → PUBLISH_READY   (export/copy only; no CMS push at MVP of this surface)
```

## 5. Discovery-model client (new connector)

Add `connectors/discovery_models/*` (already named as a target package in
`backend-architecture.md` §13) rather than overloading `connectors/answer_engines/*` — content
generation must not share code with measurement adapters (invariant 2, one concept → one owner).
The client mirrors the answer-engine adapter contract shape (`validate_connection()`,
`execute()`/`generate()`, `normalize_usage()`, `classify_error()`) and resolves its BYOK key
from the `ProviderConnection` referenced by the active `DiscoveryModelConfig` **at execution
time only** — never from env, never logged, never in `generation_request_snapshot` (invariant 6).
The `DiscoveryModelConfig` row (`models/provider.py`) is plumbing-only today; this surface is its
first real consumer, so it must be marked `active` and carry a valid `connection_id` +
identity triple before generation is allowed.

## 6. API surface (roadmap; `/api/v1`)

All workspace-scoped via `require_workspace_member` (invariant 5); secrets never returned.

- `GET /projects/{id}/content/briefs` — list briefs (deterministic projection over analysis).
- `POST /projects/{id}/content/briefs` — (re)compute briefs from current persisted analysis
  (optionally seeded from a specific `audit_id`, `opportunity_id`, or `site_issue_id`). Pure
  projection — no provider call (invariant 7).
- `GET /content/briefs/{id}` — brief detail incl. `evidence` + `gap_summary`.
- `POST /content/briefs/{id}/drafts` — enqueue a `ContentGenerationTask` (409 if no active
  `DiscoveryModelConfig`). Returns the queued task/draft id.
- `GET /content/briefs/{id}/drafts` / `GET /content/drafts/{id}` — draft(s) with the recorded
  model-identity triple + provenance (immutable; invariant 3).
- `GET /content/drafts/{id}/revisions` / `PATCH /content/revisions/{id}` — human edit +
  `review_state` transitions (the only mutable writes in this surface).
- `GET /content/drafts/{id}/export.{md}` — reproducible export of the approved revision
  (projection, invariant 7).

## 7. Config & tuning knobs (all in `app/core/config/content.py`)

New config module (never inline, invariant 1):
- `BRIEF_TYPES` / `DEFAULT_BRIEF_TYPE`, `REVIEW_STATES` (enum frozensets, like `PROMPT_INTENTS`).
- `BRIEF_FORMULA_VERSION` (bump when the gap-ranking projection changes — stamped on every
  `ContentBrief`, like `SCORING_RULE_VERSION` in `config/analysis.py`).
- Gap thresholds: min owned-citation rate to *skip* a brief, min competitor SOV to flag
  "competitor dominates", max target prompts per brief.
- `CONTENT_GENERATOR_VERSION` (stamped on every `ContentDraft`), max draft length,
  generation timeout, `MAX_GENERATION_ATTEMPTS`.
- Brief-template text keyed by `intent`/`brief_type`. The discovery model's transport/model are
  **not** set here — they come from `DiscoveryModelConfig` + `config/provider_catalog.py`.

## 8. Frontend (roadmap)

- **Route:** `/content` — already stubbed as a disabled **"soon"** nav item in
  `frontend/components/layout/nav-items.ts` (Actions group, `label: 'Content'`). Flip `live:
  true` when shipped.
- Reuse the MVP contract layer: add `frontend/lib/api/content.ts` + zod `strictValidate`
  schemas in `schemas.ts`, and `queryKeys.content.*` in `query-keys.ts` (mirror
  `queryKeys.runs.*`). Draft ids and all `*_id` fields are `z.string().uuid()`; the secret is
  never present in any DTO.
- Screen shape: a brief list (grouped by theme/intent with the gap reason), a brief detail with
  the evidence drill-down, a "generate draft" action (disabled until an active discovery model
  exists), and a draft/revision editor with the `review_state` workflow. Generation progress is
  **polling-first** (like `/runs`), SSE optional.
- Same-origin `/api/*` proxy (invariant 12); relative `/api/v1` base, `credentials:'include'`.

## 9. Suggested build order

1. Config: `content.py` (brief types, review states, thresholds, formula/generator versions) +
   migration for `ContentBrief` / `ContentDraft` / `ContentRevision`.
2. Deterministic brief builder: projection over `ResponseAnalysis` / `Citation` /
   `CompetitorMention` / `MetricSnapshot` (+ optional `SiteIssue` / `Opportunity`) →
   `ContentBrief` with `evidence` + `brief_formula_version` (table-tested, no LLM).
3. `connectors/discovery_models/*` client (BYOK resolve at call time, identity triple, no
   secret/brand-list leakage) — unit-tested against a fake transport (no live LLM in tests).
4. Generation task on the shared `PostgresTaskQueue` (commit-before-I/O, heartbeat, sweeper,
   cooperative cancel) → immutable `ContentDraft`.
5. Revision workflow (`review_state` machine) + approved-revision markdown export.
6. API routers (projections + queue enqueue) — thin, delegate to `domain/content/*`.
7. Frontend `/content` screens (flip the disabled nav item live).

## 10. Explicit non-goals (MVP of this surface)

- **No auto-publishing / CMS push** (WordPress, Webflow, headless CMS, git PRs) — output is
  export/copy only; `PUBLISH_READY` means "ready for a human to publish elsewhere".
- **No feedback into visibility metrics** — generated or published content is never used to
  compute brand-mention rate, SOV, or any headline metric (invariants 7 + 9). Measuring whether
  new content improved visibility is done by running a *new audit*, not by trusting the draft.
- **No LLM in the brief/gap computation** — the brief is a deterministic projection; only the
  draft body is generative (invariant 9).
- **No use of a measurement engine to write content** — drafting uses the separately configured
  discovery/analysis model only (v2 §2.3); the two model concepts stay distinct.
- No shared code between the discovery-model client and the answer-engine adapters (invariant 2).

# Roadmap — AI-suggested prompt generation (`/generate`)

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

Implement the **"assisted discovery"** prompt path from the master plan
([`../architecture.md`](../architecture.md)
§4.2): given a project's brand evidence (and optionally some web evidence), use the configured
**discovery/analysis model** to *suggest* a user-controlled number of benchmark prompts, which
the user reviews/edits before they enter the prompt library. This turns the existing stub into a
working feature.

**What already exists (do not rebuild — invariant 2):**
- `POST /prompt-sets/{prompt_set_id}/generate` is **already wired** in
  `backend/app/api/prompts.py` as a stub: it validates workspace scope (404 for a foreign set)
  then raises **`501 not_implemented`** with a structured `{code, message}` body so the UI shows
  a coming-soon state.
- The `Prompt` model (`backend/app/models/prompt.py`) already carries `origin`
  (`manual | imported | generated`, from `config/projects.py` `PROMPT_ORIGIN_*`) and a
  `generation_evidence` JSONB column — the provenance slot for generated prompts.
- `DiscoveryModelConfig` (`backend/app/models/provider.py`) already stores which
  logical/transport/model would drive generation, plus a BYOK `connection_id` and a
  `parameters` JSONB — currently **plumbing-only** (stored, not invoked).

This spec designs flipping the 501 to a real implementation using that existing plumbing.

## 2. The measurement / discovery boundary (critical — invariant 6, invariant 10)

Prompt generation uses the **discovery/analysis model**, *not* a measurement engine. The master
plan (§2.3, §4.2) keeps these strictly separate: measurement engines
(`provider_catalog.LOGICAL_ENGINES` = chatgpt/gemini/claude) exist only to be measured; the
discovery model is a *separately configured* model for brand understanding + prompt suggestion.

- **Resolve the model from `DiscoveryModelConfig`** for the workspace (invariant 5), decrypt its
  `ProviderConnection` BYOK key at call time (invariant 6, never from env), and record the
  **logical/transport/model triple** on the output (invariant 10) exactly as an audit attempt
  does.
- **Brand/competitor boundary.** Invariant 6's rule is precise: the brand/competitor **list** is
  *never sent to a provider as part of a prompt*. The boundary that generation must hold is about
  the **list as context**, not about brand *words* appearing in prompt text:
  - The **discovery/analysis model is the only model that receives the brand/competitor list** as
    context. Its entire job is to reason over brand evidence, so the brand/competitor list *is*
    its input (by design, invariant 6 permits this for the discovery model specifically).
  - **Measurement-engine routes must never be sent the brand/competitor list** as context — not
    in a system prompt, not in a `request_snapshot`, not appended to prompt text. Audits send only
    the prompt text; scoring matches it against the brand list *afterward* (invariant 9).
  - A **generated prompt's text MAY legitimately mention a brand or competitor name** (e.g.
    "How does Acme compare to Rivalco for X?"). That text is a normal prompt and is later sent to
    measurement engines as-is — which is fine, because it is prompt *text*, not the brand/competitor
    *list* injected as context. Such prompts are flagged via the existing `Prompt.branded` semantics
    (see §4) so they are visible and filterable, exactly like a manually-authored branded prompt.
  Sending brand evidence to the discovery provider is the master-plan §15 action that "requires
  explicit confirmation before sending brand evidence to a selected discovery provider" — the UI
  must confirm before the first generation, and the backend enforces the confirmation (§6).

## 3. Inputs & flow

```
POST /prompt-sets/{id}/generate  { count, intents?, include_web_evidence?, seed? }
  → resolve project + brand evidence (aliases, competitors, market) — workspace-scoped
  → (optional) fetch allowed web evidence → BrandEvidenceSnapshot (SSRF-guarded, master plan §15)
  → resolve DiscoveryModelConfig + decrypt BYOK key
  → call discovery model with a neutral system prompt + brand context, request `count` prompts
  → parse suggestions → dedupe against existing prompts (invariant 2 no-dup)
  → persist each as a PENDING suggestion (Prompt(origin='generated', enabled=false,
    review_status='pending', generation_evidence={...})) — NOT an active prompt
  → return the pending suggestions for user review/edit/accept/reject
```

- **User-controlled count** (`count`, bounded by config §7). The plan's §4.2 is explicit:
  *"suggest requested number of prompts."*
- **Dedupe**: one concept, one row (invariant 2). A naïve normalize-then-compare-then-insert
  **races** between concurrent generation requests (two requests can both pass the compare, then
  both insert the same concept). Instead, persist a **canonical normalized-text hash** on each
  prompt (normalization rules from config §7) and enforce a **uniqueness constraint scoped per
  prompt set** (`unique(prompt_set_id, normalized_text_hash)`). Inserts use an **atomic,
  conflict-safe upsert** (`INSERT ... ON CONFLICT DO NOTHING`) so a duplicate is dropped by the
  database, not by an application-level check — concurrent requests can never both win. Dropped
  duplicates are not re-added.
- **Determinism note**: an optional `seed` may be recorded for reproducibility, but generation
  is inherently a model call, so it is **not** a headline metric and does **not** touch
  deterministic scoring (invariant 9) — see §6.

## 4. Persistence & provenance (invariant 4)

Generated prompts reuse the **existing** `Prompt` table — no new prompt table (invariant 2):
- `origin = 'generated'` (`config/projects.py` `PROMPT_ORIGIN_GENERATED`).
- **Suggestions are persisted as pending, never as active prompts.** A fresh suggestion is
  written **disabled** (`enabled = false`) with a `review_status = 'pending'` staging marker, so
  it does **not** enter the active prompt set and can **never** be consumed by an audit before a
  human has seen it (this is what enforces the §9 non-goal "no auto-run" and the frontend
  accept/edit/reject requirement).
  - **Acceptance transition.** A pending suggestion becomes an **active** `Prompt` only after
    user review: on **accept** (optionally after **edit**) the row transitions to
    `review_status = 'accepted'`, `enabled = true` — at which point it is an ordinary prompt and
    eligible for audits. Editing before accept updates the text (and `branded` flag) but keeps it
    pending until accepted.
  - **Rejection + auditability.** On **reject** the row transitions to
    `review_status = 'rejected'` and stays disabled; it is retained (not hard-deleted) so the
    generation run remains auditable — the full set of AI output, and which suggestions a human
    accepted vs rejected, is traceable via `generation_evidence`. Because an audit only ever reads
    active (`enabled`, `accepted`) prompts, an audit can **never** consume unreviewed AI output.
- **`branded` flag.** When a suggestion's text mentions a brand/competitor name (see §2), the
  service sets `branded = true` on the pending row so it surfaces correctly in the branded/
  non-branded filters; the user can override this during review/edit.
- `generation_evidence` (JSONB) is the **provenance** (invariant 4). Minimum contents:
  - `model_identity`: the logical/transport/model triple (invariant 10);
  - `discovery_config_id`: the `DiscoveryModelConfig` used;
  - `brand_evidence_ref`: the `BrandEvidenceSnapshot` id (or a hash of the brand context) the
    suggestion was derived from;
  - `web_evidence_refs`: any fetched-evidence ids, if `include_web_evidence`;
  - `prompt_seed` / `generation_run_id`: to trace which generation produced this row;
  - `generator_version`: the generation-pipeline version.
  A generated prompt with no traceable model identity + evidence reference is invalid
  (invariant 4). This mirrors how every derived analysis row references its source + version.
- If web evidence is fetched, the `BrandEvidenceSnapshot` is an **immutable artifact**
  (invariant 3) written once and referenced, never edited in place; a re-generation creates a
  new snapshot identity.

## 5. Sync vs queued execution (invariant 8)

A small `count` with brand-only evidence can run **inline** in the request. A large `count`, or
`include_web_evidence` (which fans out to network fetches), runs as a **queued task on the
existing Postgres `FOR UPDATE SKIP LOCKED` queue** (invariant 8) via the `TaskQueue` Protocol —
no Redis. The standard rules apply: **commit the claim before any model/web I/O**, heartbeat to
hold the lease, sweeper recovers expired tasks, and cancellation is **cooperative** (stop at the
model-call / fetch boundary — invariant 9). Queued generation returns `202` + a task id; the UI
polls for completion, then the reviewed prompts appear in the set.

## 6. API & frontend

- **Backend — flip the existing stub.** In `backend/app/api/prompts.py`, replace the
  `501 not_implemented` raise in `generate_prompts_endpoint` with a call into a new
  `domain/prompts/generation.py` service. Guard order matters:
  1. **Workspace-scope check first** (foreign set → 404 before anything runs, invariant 5).
  2. **Then the backend — not just the UI — must validate `confirm_send_evidence == true`**
     before *any* discovery-provider I/O (brand evidence, web fetches, or the model call).
     A missing or `false` `confirm_send_evidence` is rejected with the standard structured
     `{code, message}` validation error (422) — the UI confirmation is a convenience, never the
     only gate, so a direct API caller cannot skip the master-plan §15 confirmation.
  Request body: `{ count, intents?, include_web_evidence?, confirm_send_evidence, seed? }`.
  Response: the created pending `Prompt` suggestions (inline) or `202` + task id (queued). Reuse
  the existing `PromptResponse` DTO — it already exposes `origin` + `generation_evidence`
  (`domain/prompts/schemas.py`).
- **Frontend — replace coming-soon.** `/prompts` currently shows AI-suggest as *coming soon*
  ([`../frontend-architecture.md`](../frontend-architecture.md) §3). Add a "Suggest prompts"
  action → a count/intents form + the master-plan §15 **confirmation** before brand evidence is
  sent to the discovery provider → a review table (accept/edit/reject each suggestion) before
  they commit to the set. Reuse `frontend/lib/api/prompts.ts` (extend it with `generate`) + zod
  schemas; browser calls stay same-origin through the `/api/*` proxy (invariant 12). A missing/
  inactive `DiscoveryModelConfig` surfaces an actionable "configure a discovery model" state.

## 7. Config & tuning knobs (all in `app/core/config/*`)

Nothing tunable is hard-coded (invariant 1):
- `app/core/config/prompts.py` (or extend `projects.py`): `GENERATION_DEFAULT_COUNT`,
  `GENERATION_MAX_COUNT`, `GENERATION_QUEUE_THRESHOLD` (count above which it queues),
  the neutral **generation system prompt**, allowed `intents`, dedupe-normalization rules,
  `generator_version`.
- Discovery-model transport/model catalog + timeouts stay in `config/provider_catalog.py`
  (one owner, invariant 2) — generation reads the discovery model the same way audits read
  measurement routes.
- Web-evidence fetching (if enabled) reuses the SSRF-guarded evidence-fetch config + approved
  host allow-list (master plan §15).

## 8. Suggested build order

1. Config knobs + generation system prompt (`config/prompts.py`).
2. `domain/prompts/generation.py`: resolve `DiscoveryModelConfig` + BYOK, build the neutral
   request, call the discovery model, parse + dedupe suggestions. Unit-test the parser/dedupe
   deterministically against fixture model output (no live provider in tests).
3. Persist as **pending** `Prompt(origin='generated', enabled=false, review_status='pending',
   generation_evidence=...)` with full provenance (invariant 4) via a conflict-safe upsert on the
   per-set `normalized_text_hash` uniqueness constraint (§3); assert the brand/competitor *list*
   never appears in a measurement path (branded prompt *text* is fine, §2).
4. Flip the `501` stub to call the service (inline path first); keep the workspace-scope 404.
5. Queued path on the existing `PostgresTaskQueue` for large `count` / web evidence (invariant 8).
6. `/prompts` UI: suggest form + evidence-send confirmation + review/accept table.

## 9. Explicit non-goals (MVP of this surface)

- **No measurement engine used for generation** — generation is the discovery model's job only
  (master plan §2.3/§4.2); measurement routes are untouched.
- **Generation is an input aid, not a metric** — deterministic headline metrics (invariant 9)
  are unaffected; generated prompts are ordinary prompts once reviewed, scored the same way.
- **The brand/competitor list never leaks into a measurement route** — it flows to the discovery
  model only, behind explicit user confirmation (invariant 6, master plan §15).
- **No auto-run** — suggestions require user review/edit before they enter the set; the product
  must never require discovery AI to run an audit (master plan §4.3).
- **No new prompt table or second crypto** — reuse `Prompt` + `generation_evidence` and
  `encrypt_secret`/`decrypt_secret` (invariant 2).

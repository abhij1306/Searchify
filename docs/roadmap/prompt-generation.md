# Roadmap — AI-suggested prompt generation (`/generate`)

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

Implement the **"assisted discovery"** prompt path from the master plan
([`../../cube27-aeo-visibility-mvp-architecture-plan-v2.md`](../../cube27-aeo-visibility-mvp-architecture-plan-v2.md)
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
- **Brand/competitor boundary.** Invariant 6 says the brand/competitor list is *never sent to a
  measurement provider*. Generation is different: the discovery model is a **separate configured
  model** whose entire job is to reason over brand context, so the brand evidence *is* its
  input. The boundary to hold: brand/competitor context flows to the **discovery model only**,
  and **never leaks into a measurement route** — the generated prompt *text* that later feeds an
  audit must still be brand-context-free at measurement time (measurement neutrality is
  preserved because audits send only the prompt text, scored against the brand list afterward).
  Sending brand evidence to the discovery provider is the master-plan §15 action that "requires
  explicit confirmation before sending brand evidence to a selected discovery provider" — the UI
  must confirm before the first generation.

## 3. Inputs & flow

```
POST /prompt-sets/{id}/generate  { count, intents?, include_web_evidence?, seed? }
  → resolve project + brand evidence (aliases, competitors, market) — workspace-scoped
  → (optional) fetch allowed web evidence → BrandEvidenceSnapshot (SSRF-guarded, master plan §15)
  → resolve DiscoveryModelConfig + decrypt BYOK key
  → call discovery model with a neutral system prompt + brand context, request `count` prompts
  → parse suggestions → dedupe against existing prompts (invariant 2 no-dup)
  → persist each as Prompt(origin='generated', generation_evidence={...})
  → return the created prompts for user review/edit
```

- **User-controlled count** (`count`, bounded by config §7). The plan's §4.2 is explicit:
  *"suggest requested number of prompts."*
- **Dedupe**: generated text is normalized + compared against existing prompts in the set before
  insert — one concept, one row (invariant 2). Duplicates are dropped, not re-added.
- **Determinism note**: an optional `seed` may be recorded for reproducibility, but generation
  is inherently a model call, so it is **not** a headline metric and does **not** touch
  deterministic scoring (invariant 9) — see §6.

## 4. Persistence & provenance (invariant 4)

Generated prompts reuse the **existing** `Prompt` table — no new prompt table (invariant 2):
- `origin = 'generated'` (`config/projects.py` `PROMPT_ORIGIN_GENERATED`).
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
  `domain/prompts/generation.py` service. Keep the existing workspace-scope check first (foreign
  set → 404 before anything runs, invariant 5). Request body: `{ count, intents?,
  include_web_evidence?, confirm_send_evidence, seed? }`. Response: the created `Prompt` rows
  (inline) or `202` + task id (queued). Reuse the existing `PromptResponse` DTO — it already
  exposes `origin` + `generation_evidence` (`domain/prompts/schemas.py`).
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
3. Persist as `Prompt(origin='generated', generation_evidence=...)` with full provenance
   (invariant 4); assert the brand list never appears in a measurement path.
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

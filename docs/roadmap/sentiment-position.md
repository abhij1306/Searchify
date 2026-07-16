# Roadmap — Sentiment + average position

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

Sentiment and average position **already exist in the schema but are null at MVP.**
`ResponseAnalysis.sentiment` (`String(16)`, nullable) and `ResponseAnalysis.avg_position`
(Float, nullable) are present columns (`backend/app/models/analysis.py`), the aggregate
`MetricSnapshot.metrics` dict tolerates their absence, and the `/visibility` DTO surfaces them
as nullable — rendered as `—` in the UI (`frontend` visibility rankings). They are null because
computing them needs an **LLM / contextual judgement**, and doing that in the deterministic
scoring path would violate the "**no LLM for headline metrics**" rule (invariant 9) and the
"**projections, not recompute**" rule (invariant 7). See the closing *"Note on not-yet-computed
metrics"* in [`../invariants.md`](../invariants.md): sentiment + avg-position are deferred, and
**must not be back-filled with a heuristic that pretends to be deterministic.**

This spec designs how to actually compute them **without breaking those invariants**, by adding
a **separate, versioned, LLM-adjudicated analysis layer** (master plan §11 *"optional
adjudication"*) that is strictly walled off from the deterministic headline metrics.

The five design pillars — every rule below traces to one of these:

- **(a) Separate engine identity.** Computed by the **discovery/analysis model** (§2.3), not the
  measurement engines, with the full logical/transport/model triple recorded (invariant 10).
- **(b) New immutable derived rows.** Written as **new** rows referencing the exact
  `RawResponseArtifact` + a distinct `adjudicator_version` (invariants 3 + 4) — never mutating
  the existing deterministic `ResponseAnalysis` row in place (invariant 3).
- **(c) UI/DTO separation.** Adjudicated values are clearly flagged and physically separated from
  deterministic headline metrics, so the no-LLM-for-headline-metrics guarantee holds and the
  provenance of every number is unambiguous.
- **(d) Reproducible / idempotent** per `(artifact, adjudicator_version)`.
- **(e) Projection when surfaced** (invariant 7) — reports render persisted adjudication rows,
  never call a model at read time.

## 2. Two distinct concepts under one surface

The MVP scorer (`app/analysis/scoring.py`) already computes a **deterministic** notion of order.
Do not conflate it with the contextual notion this surface adds.

| Concept | Owner | Nature | This surface? |
|---|---|---|---|
| **Ordered-list / table / rank detection** | `scoring.py` (ordered-list/table/rank detection, master plan §11) | Deterministic — the brand's literal position in an explicitly ranked list | **Already computed** — do not touch |
| **Average position (contextual)** | *this surface* | "How prominently is the brand positioned" when there is **no** explicit ordered list — needs LLM judgement | **New, adjudicated** |
| **Sentiment** | *this surface* | positive / neutral / negative / unknown toward the brand — needs context | **New, adjudicated** |

**Average rank stays deterministic and unchanged.** Master plan §12 already says average rank
*"include[s] only responses with a confidently detected ordered recommendation."* That
deterministic rank is a headline metric and is not adjudicated. The **contextual** "prominence"
signal this surface adds is a *separate, adjudicated* field — never overwriting the deterministic
rank, never promoted into the headline visibility score. Sentiment: master plan §12 requires
`unknown` is **never silently converted to neutral** — the adjudicator must be allowed to abstain
(invariant 9: "no forced sentiment or rank when evidence is ambiguous").

## 3. Data model (new tables — UUID PKs, workspace-scoped, immutable)

Two new derived-row tables, one per adjudicated concept. Both mirror the provenance discipline of
`ResponseAnalysis` (invariant 4) and are **written once, never mutated** (invariant 3). All ids
are string UUIDs; workspace-scoped (invariant 5).

### 3.1 `SentimentAdjudication`

- `id`, `workspace_id`, `audit_id`, `task_id`, `analysis_id` (FK → `response_analyses.id` — the
  deterministic row it *annotates*, **not** replaces).
- **Provenance (invariants 3 + 4):** `artifact_id` (FK → `raw_response_artifacts.id`, the exact
  immutable evidence it read), `adjudicator_version` (String — distinct from `analyzer_version`),
  `prompt_template_version` (String).
- **Model identity (invariant 10):** `logical_engine` + `transport_provider` + `transport_model`
  (resolved from the `DiscoveryModelConfig`; all three required or the row is invalid).
- **Result:** `sentiment` (`positive` | `neutral` | `negative` | `unknown` — enum in config),
  `confidence` (Float), `reason` (Text — the model's short justification), `abstained` (Boolean —
  true when evidence is ambiguous; `unknown` is not coerced to neutral).
- **Idempotency:** unique `(artifact_id, adjudicator_version)` so the same artifact under the same
  adjudicator version yields exactly one row and is fully reproducible (pillar d).
- `created_at`.

### 3.2 `PositionAdjudication`

Same shape and provenance/identity/idempotency columns as §3.1, with result columns:
`prominence` (a bounded ordinal token, e.g. `lead` | `prominent` | `mentioned` | `buried` —
enum in config, **not** an unbounded float masquerading as a deterministic rank),
`position_estimate` (Float, nullable — only when the model can justify a numeric estimate),
`has_explicit_rank` (Boolean — echoes whether the deterministic scorer already found an ordered
list; when true, adjudication is skipped/deferred to the deterministic value), `confidence`,
`reason`, `abstained`. Unique `(artifact_id, adjudicator_version)`.

### 3.3 What does **not** change

- `ResponseAnalysis.sentiment` / `.avg_position` **stay nullable and deterministic-path-null.**
  The deterministic writer never fills them from an LLM (invariant 3 — single writer, no in-place
  repair). They remain `—` for any run that did not request adjudication.
- `MetricSnapshot.visibility_score` and every headline rate are **unchanged.** Adjudicated
  aggregates (e.g. sentiment distribution, mean prominence) are stored in a **clearly namespaced
  sub-key** of `MetricSnapshot.metrics` (e.g. `metrics.adjudicated.*`) or a sibling snapshot,
  never merged into the headline block, and are computed as a projection over the adjudication
  rows (invariant 7) with their own `adjudicator_version` recorded.

## 4. Adjudication lifecycle (opt-in, separate task type)

Reuse the audit pattern (`app/orchestration/*`, `PostgresTaskQueue`). Adjudication is a
**separate, opt-in task type** that runs *after* deterministic analysis, never inside it.

1. **Opt-in per audit.** `POST /audits` (or a follow-up trigger) carries an `adjudication`
   flag. Only audits that requested it get adjudication tasks; the deterministic pipeline is
   unchanged for everyone else. The flag + the resolved adjudicator model identity +
   `adjudicator_version` + `prompt_template_version` are frozen into `Audit.configuration` at
   request time (determinism / provenance, like the MVP config snapshot).
2. **Enqueue after analysis.** For each completed execution with a persisted `ResponseAnalysis`,
   enqueue one adjudication task keyed to its immutable `RawResponseArtifact`. Claimed with `FOR
   UPDATE SKIP LOCKED`; commit the claim before the model call; heartbeat; sweeper reclaims
   expired leases (invariant 8). Unique `(artifact_id, adjudicator_version)` prevents
   double-adjudication (mirrors the MVP double-claim guard).
3. **Execute + persist.** Call the discovery/analysis model (BYOK, key resolved at execution
   time, never persisted or logged — invariant 6; the brand/competitor list is not injected into
   the prompt beyond the answer text being judged). Write the immutable
   `SentimentAdjudication` / `PositionAdjudication` rows with full provenance + model identity.
4. **Idempotent re-run.** Re-adjudicating an artifact under the **same** `adjudicator_version` is
   a no-op (unique constraint). Bumping `adjudicator_version` (a prompt/model change) produces a
   **new** row identity — never an overwrite (invariant 3), so old and new judgements coexist and
   remain traceable.

Cancellation is cooperative — the worker stops at the execution boundary (invariant 9).

## 5. Migration & back-fill policy (critical)

- **No back-fill of historical runs.** The invariants note forbids filling nulls with a
  heuristic that pretends to be deterministic. Historical audits that did not request
  adjudication keep `—`; they are **not** retroactively adjudicated to fabricate coverage.
- **Adjudication is opt-in per audit** and applies **only to runs that requested it.** A run's
  adjudication coverage (how many executions were adjudicated) is disclosed alongside the values,
  exactly like partial-completion coverage.
- The migration only **adds** the two tables + config + the opt-in flag/columns. It does **not**
  alter the meaning or nullability of the existing deterministic `sentiment` / `avg_position`
  columns.

## 6. API surface (roadmap; `/api/v1`) — surfaced as flagged projections

No new top-level resource; adjudicated values are surfaced through the **existing**
execution / metrics / visibility projections (invariant 7), always flagged as adjudicated with
their model identity + `adjudicator_version`:

- `GET /executions/{id}` — add an `adjudication` block (sentiment + prominence + confidence +
  reason + model identity + `adjudicator_version`), **separate** from the deterministic evidence
  block. Absent/`null` when the run was not adjudicated.
- `GET /audits/{id}/metrics` — the `MetricSnapshot` projection exposes adjudicated aggregates
  under a namespaced `adjudicated` sub-object with its own version + coverage, never blended into
  the headline rates.
- `GET /projects/{id}/visibility?audit_id=` — the rankings table's `sentiment` / `avg_position`
  cells populate **only** from adjudication rows for adjudicated runs, and carry an
  `is_adjudicated` flag + source so the UI can label them; otherwise they stay `null` (`—`).
- `POST /audits` — accepts the opt-in `adjudication` flag (validated against config).
- `GET /audits/{id}/export.{csv,md}` — adjudicated columns are exported only when present, in a
  clearly labelled section distinct from deterministic metrics (projection, invariant 7).

All workspace-scoped via `require_workspace_member` (invariant 5). The adjudicator BYOK key is
never returned in any DTO or log line (invariant 6).

## 7. Config & tuning knobs (all in `backend/app/core/config/*`)

Extend `config/analysis.py` (which already owns `ANALYZER_VERSION` / `SCORING_RULE_VERSION`) or
add `config/adjudication.py` — nothing tunable inline (invariant 1):

- `ADJUDICATOR_VERSION` — the provenance stamp on every adjudication row (bumped on any
  prompt/model/logic change; distinct from `ANALYZER_VERSION`).
- `SENTIMENT_LABELS` = `{positive, neutral, negative, unknown}` (unknown never coerced).
- `PROMINENCE_LABELS` = the bounded ordinal set (`lead|prominent|mentioned|buried`).
- `ADJUDICATION_PROMPT_TEMPLATE_VERSION` (the exact prompt template, versioned).
- `ADJUDICATION_ENABLED` flag (default false) + `ADJUDICATION_MIN_CONFIDENCE` (below which the
  result is stored as `abstained`/`unknown`, never forced).
- Concurrency / rate-limit / timeout / max-attempts knobs for the adjudication task type (reuse
  the MVP queue knobs where equivalent — invariant 2).
- The adjudicator **model** itself is workspace data (`DiscoveryModelConfig` in
  `models/provider.py`); only its guardrail defaults live in `config/provider_catalog.py`.

## 8. Suggested build order

1. Config: `ADJUDICATOR_VERSION` + label enums + prompt-template version + enable flag +
   migration for `SentimentAdjudication` + `PositionAdjudication` (+ the opt-in `Audit` columns).
2. Opt-in plumbing: accept + freeze the `adjudication` flag/model identity into
   `Audit.configuration` at request time.
3. Adjudication task type + worker path (claim → model call → persist immutable rows), reusing
   `PostgresTaskQueue` + the state machine; unit-tested with a stubbed model client (no live
   provider in tests), asserting idempotency per `(artifact, adjudicator_version)`.
4. Adjudicated aggregate projection into the namespaced `MetricSnapshot.metrics.adjudicated`
   sub-object (with coverage), read-only (invariant 7).
5. API: extend `/executions/{id}`, `/metrics`, `/visibility`, exports with flagged adjudicated
   fields.
6. Frontend: populate the previously-`—` sentiment / position cells from adjudication rows with a
   distinct "adjudicated (AI)" affordance, visually separated from deterministic metrics.

## 9. Explicit non-goals (MVP of this surface)

- **Never back-fill nulls with a heuristic pretending to be deterministic** (invariants note).
  Historical / non-opted-in runs stay `—`.
- **Never let an adjudicated value override a deterministic headline metric.** Adjudicated
  sentiment/prominence are a *separate, flagged* layer; the visibility score, brand-mention rate,
  owned-citation rate, SOV, and deterministic average rank are untouched (invariants 7 + 9).
- **Never mutate the deterministic `ResponseAnalysis` row** to fill its `sentiment` /
  `avg_position` from the LLM — adjudication writes **new** rows (invariant 3).
- **Never coerce `unknown` sentiment to neutral**, and never force a rank/prominence when
  evidence is ambiguous — the adjudicator abstains (invariant 9; master plan §12).
- **No adjudication at read time.** Reports/metrics render persisted adjudication rows only; a
  projection never calls the model (invariant 7).
- **No adjudicator key leakage** into DTOs, logs, `request_snapshot`, or raw artifacts
  (invariant 6).

# Roadmap — Topics

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

**Cluster prompts/themes into topics and report visibility per topic.** A brand's prompt library
grows into dozens or hundreds of prompts; Topics groups them into a smaller set of meaningful
**topics** (e.g. "pricing comparisons", "integrations", "security & compliance") and reports the
headline visibility metrics (Visibility%, share-of-voice, owned-citation rate) **per topic** so
the user can see *which subject areas the brand wins or loses in an answer engine*.

Topics **generalizes the existing grouping hook**: `Prompt` already carries a free-text `theme`
field and an `intent` (`models/prompt.py`; `PROMPT_INTENTS` in `config/projects.py`). `theme` is
an ad-hoc per-prompt label today; a `Topic` is a first-class, workspace-scoped grouping that a
prompt is *assigned* to (many prompts → one topic), computed either from the discovery/analysis
model or from a deterministic embedding/keyword approach.

Two distinct concerns, kept strictly separate:
- **Clustering** (assigning prompts to topics) *may* use the discovery/analysis model (v2 §2.3)
  as an **analysis aid**. Its output is persisted with provenance + the model-identity triple
  (invariants 4 + 10). A deterministic embedding/keyword clustering is also supported and is the
  default where determinism is required.
- **Per-topic visibility metrics** are a **deterministic projection** over persisted per-prompt
  `ResponseAnalysis` — **never** LLM-computed (invariants 7 + 9). Assignment can be
  LLM-assisted; the headline number is a plain aggregate of already-scored prompts.

## 2. Clustering: analysis aid, not a metric

Clustering assigns each `Prompt` to a `Topic`. Two supported strategies (config-selected,
invariant 1):

- **Deterministic (default):** embedding or keyword/theme clustering with a stored
  `random_seed` for reproducible cluster ordering (mirrors the audit seed pattern, invariant 9),
  or a rules-over-`theme`/`intent` grouping. Keyword/theme and rules-over-`theme`/`intent`
  clustering are **fully reproducible** from the stored `random_seed` alone. Embedding-based
  clustering is reproducible only when the **embedding provenance is also pinned**: because
  `random_seed` does not capture a provider/model change, the embedding **model identity +
  transport + version** must be persisted on the clustering run and on the `Topic`/`PromptTopic`
  rows (the identity triple `logical_engine` + `transport_provider` + `transport_model` plus an
  `embedding_version`, invariants 4 + 10). A clustering run whose embedding provenance is not
  recorded is **not** claimed as fully reproducible.
- **Discovery-model assisted (roadmap):** the discovery/analysis model proposes topic labels and
  groupings. This is an **analysis aid** whose output is treated like any derived row: persisted
  with provenance to the clustering run **and** the model-identity triple `logical_engine` +
  `transport_provider` + `transport_model` (invariant 10), sourced from the active
  `DiscoveryModelConfig` (`models/provider.py`). The **brand/competitor list is not sent** to
  the provider (invariant 6), and the BYOK key is resolved at call time only (invariant 6).

Crucially, **whichever strategy is used, the assignment is persisted** and the per-topic metric
reads only the persisted assignment + persisted per-prompt analysis. The LLM never produces a
visibility number (invariant 9). Re-clustering creates a **new clustering run identity** and new
`PromptTopic` rows; it does not mutate prior assignments in place (invariants 3 + 4).

## 3. Data model (new tables — UUID PKs, workspace-scoped)

Workspace-scoped through `project_id` (invariant 5); UUID PKs, no `user_id`. Mirror the existing
`models/prompt.py` shape.

- **`Topic`** — a first-class topic within a project. `id`, `workspace_id`, `project_id`,
  `label`, `description`, `slug`, `dominant_intent` (nullable; one of `PROMPT_INTENTS`),
  `clustering_run_id` (the run that produced it), `clustering_method`
  (`deterministic|discovery_model`), `random_seed` (for the deterministic method),
  `analyzer_version` + `clustering_version` (provenance + version, invariant 4), and — when
  discovery-model-assisted **or embedding-based** — the identity triple `logical_engine` +
  `transport_provider` + `transport_model` + `discovery_config_id` (invariant 10) plus an
  `embedding_version` for the embedding case, so an embedding provider/model change is captured
  rather than hidden behind `random_seed`. Timestamps.
- **`PromptTopic`** — the **assignment** of a prompt to a topic (the join, but a derived row in
  its own right). `id`, `workspace_id`, `project_id`, `prompt_id` (FK → `prompts.id`),
  `topic_id` (FK → `topics.id`), `confidence` (nullable float; only meaningful for
  model-assisted clustering), `clustering_run_id`, **provenance**: `analyzer_version` +
  `clustering_version` (+ the model-identity triple + `embedding_version` when model-assisted or
  embedding-based, invariants 4 + 10), `assigned_at`. Unique `(clustering_run_id, prompt_id)` so a prompt has exactly one topic per
  run. A prompt may be assigned to different topics across different runs (immutable per run,
  invariant 3).
- **`TopicVisibilitySnapshot`** *(optional projection)* — per-topic aggregate metrics for a
  given audit, analogous to `MetricSnapshot` (`models/analysis.py`). `id`, `workspace_id`,
  `project_id`, `topic_id`, `audit_id`, per-topic Visibility%, share-of-voice, brand-mention
  rate, owned-citation rate, prompt count, `analyzer_version` + `formula_version`,
  `source_analysis_ids` (the per-prompt `ResponseAnalysis` rows aggregated, invariant 4),
  `computed_at`. Immutable per (topic, audit) computation; a re-run creates a new identity.

The clustering knobs (method, thresholds, target cluster count) live in config (§7), never
inline (invariant 1). Before adding a "grouping" concept, **grep for the existing `theme`
usage** so Topics extends rather than duplicates it (invariant 2).

## 4. Per-topic visibility (deterministic projection)

The headline per-topic metric is a **pure aggregate** of already-persisted per-prompt analysis
(invariant 7), reusing the MVP deterministic scoring (`analysis/scoring.py`,
`analysis/normalization.py`) — no second extraction, no provider call, no LLM (invariants 7 + 9):

```
for each Topic:
  prompts = PromptTopic rows for (topic, latest clustering run)
  analyses = ResponseAnalysis rows for those prompts in the selected audit
  topic_visibility = deterministic_aggregate(analyses)   # same formula as MetricSnapshot
```

It reuses the exact ported metrics (brand-mention rate, owned-citation rate, mention→owned
conversion, response-level + mention-level share-of-voice) sliced by topic. Sentiment and
average-position remain **null** here for the same reason as the MVP (would need an LLM —
invariant 9; see invariants.md "Note on not-yet-computed metrics"). Stamped with
`analyzer_version` + `formula_version` (invariant 4).

## 5. Clustering lifecycle (queued task when model-assisted)

Deterministic clustering can run inline (pure computation). **Discovery-model-assisted**
clustering is a queued task on the shared `PostgresTaskQueue` — same row contract as `AuditTask`
(`FOR UPDATE SKIP LOCKED`, **commit the claim before the LLM call**, heartbeat, sweeper,
cooperative cancel — invariants 8 + 9). Reuse the `TaskQueue` Protocol / `PostgresTaskQueue`; do
not add a second queue (invariant 2). It shares the `connectors/discovery_models/*` client
described in [`content-writer.md`](content-writer.md) §5 (BYOK resolve at call time, no
secret/brand-list leakage, identity triple recorded — invariants 6 + 10).

```
prompts (persisted) → clustering run (deterministic OR discovery-model-assisted)
  → Topic + PromptTopic rows (immutable per run, provenance + version + model identity)
  → (on audit completion) TopicVisibilitySnapshot = deterministic aggregate of per-prompt analysis
```

## 6. API surface (roadmap; `/api/v1`)

All workspace-scoped via `require_workspace_member` (invariant 5).

- `GET /projects/{id}/topics` — list topics for the latest clustering run (projection).
- `POST /projects/{id}/topics/recluster` — trigger a clustering run (body: `method`,
  `target_count`/`seed`, optional `discovery_config_id`). Inline for deterministic; enqueues a
  task when model-assisted. Returns the clustering run id.
- `GET /topics/{id}` — topic detail incl. clustering provenance (method, run id, versions, and
  model-identity triple when model-assisted).
- `GET /topics/{id}/prompts` — the `PromptTopic` assignments for a topic (paged).
- `GET /projects/{id}/topics/visibility?audit_id=` — **per-topic visibility projection**
  (`TopicVisibilitySnapshot`), defaulting to the latest completed audit when `audit_id` is
  omitted (mirrors `GET /projects/{id}/visibility`). Pure projection (invariant 7).
- `GET /projects/{id}/topics/export.{csv,md}` — reproducible per-topic export (projection).

## 7. Config & tuning knobs (all in `app/core/config/topics.py`)

Never inline (invariant 1):
- `CLUSTERING_METHODS` / `DEFAULT_CLUSTERING_METHOD` (`deterministic` default), target cluster
  count bounds, similarity/keyword thresholds, min prompts per topic.
- `CLUSTERING_VERSION` (bump on any change to clustering logic — stamped on every `Topic` /
  `PromptTopic`, like `SCORING_RULE_VERSION`) and the per-topic metric `FORMULA_VERSION`.
- Embedding model/transport for the deterministic-embedding path (if used) — referenced from
  `config/provider_catalog.py`, not hard-coded. The discovery model's transport/model come from
  `DiscoveryModelConfig`, not this file.
- Queue knobs for model-assisted clustering (attempts, timeout).

## 8. Frontend (roadmap)

- **Route:** `/topics` — already stubbed as a disabled **"soon"** nav item in
  `frontend/components/layout/nav-items.ts` (Prompts group, `label: 'Prompt Research'`,
  `href: '/topics'`, `icon: Sparkles`). Flip `live: true` when shipped.
- Reuse the MVP contract layer: add `frontend/lib/api/topics.ts` + zod `strictValidate` schemas
  in `schemas.ts`, and `queryKeys.topics.*` in `query-keys.ts` (mirror `queryKeys.visibility.*`
  for the per-audit per-topic projection). All `id`/`*_id` fields `z.string().uuid()`.
- Screen shape: a topic list with per-topic Visibility% + SOV (reusing the visibility
  score/donut/table primitives), a topic detail with its assigned prompts + clustering
  provenance (method + model identity when assisted), and a "recluster" action. Reclustering
  progress is polling-first (like `/runs`) when queued. Sentiment/avg-position render `—`.
- Same-origin `/api/*` proxy (invariant 12).

## 9. Suggested build order

1. Config: `topics.py` (methods, thresholds, `CLUSTERING_VERSION`, per-topic `FORMULA_VERSION`)
   + migration for `Topic` / `PromptTopic` (+ optional `TopicVisibilitySnapshot`).
2. Deterministic clustering first (seeded embedding/keyword over `theme`/`intent`) →
   `Topic` + `PromptTopic` with provenance + versions (table-tested, reproducible, no LLM).
3. Per-topic visibility projection reusing `analysis/scoring.py` over persisted
   `ResponseAnalysis` (deterministic aggregate) → `TopicVisibilitySnapshot`.
4. API routers (list/detail/prompts/visibility/export + recluster) — thin, delegate to
   `domain/topics/*`.
5. Frontend `/topics` screens (flip the disabled nav item live).
6. Discovery-model-assisted clustering as an opt-in method on the shared `PostgresTaskQueue`
   (shares `connectors/discovery_models/*`; identity triple + provenance recorded) — last,
   layered on top without changing the deterministic metric path.

## 10. Explicit non-goals (MVP of this surface)

- **No LLM in the headline per-topic metric** — per-topic Visibility%/SOV is a deterministic
  aggregation of persisted per-prompt analysis only (invariants 7 + 9). The discovery model may
  only *assist clustering*, and even then its output is persisted with provenance + model
  identity and never becomes a metric.
- **No second extraction** — Topics never re-runs prompts or calls a measurement engine; it
  aggregates existing `ResponseAnalysis` (invariant 7).
- **No in-place re-labeling** — reclustering creates a new clustering run + new `Topic` /
  `PromptTopic` identities; prior assignments are immutable (invariants 3 + 4).
- **No sentiment / average-position per topic** — null for the same reason as the MVP dashboard
  (would need an LLM — invariant 9).
- No duplicate "grouping" concept — Topics extends the existing `Prompt.theme` hook rather than
  introducing a parallel one (invariant 2).

# Roadmap — LLM Analytics / AI referrals

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping via `require_workspace_member`,
> the Postgres `FOR UPDATE SKIP LOCKED` task queue, immutable artifacts, provenance +
> version on every derived row, and config-in-config-only. Read [`../../Agents.md`](../../Agents.md)
> and [`../invariants.md`](../invariants.md) first — every rule there applies here too.

## 1. Goal & positioning

**AEO Insights beyond a single audit run.** The MVP `/visibility` dashboard answers "for this
one audit, how visible is the brand across ChatGPT/Gemini/Claude?". LLM Analytics answers the
adjacent, longer-horizon question: **how do AI answer engines and AI-powered search actually
surface the brand over time, and how much traffic do they refer to the brand's own site?**

Four capabilities, all **deterministic** (no LLM in any classifier or metric — invariant 9):

- **AI-referral classification** — identify visits/sessions that originated from an AI
  assistant or answer engine (ChatGPT, Gemini, Perplexity, Copilot, Claude, AI Overviews…)
  using **referrer host + UTM + user-agent heuristics**. The rule set is data, not code
  (invariant 1): a versioned match table in config.
- **Cross-engine visibility analytics over time** — aggregate the per-run `MetricSnapshot`
  rows that already exist (see [`visibility-trends.md`](visibility-trends.md)) into a
  time-series view alongside referral signals.
- **Prompt/theme-level performance breakdowns** — roll visibility up by `Prompt.theme` /
  `Prompt.intent` so a user sees which topics win or lose AI mentions.
- **Visibility↔referral correlation** — put deterministic visibility metrics next to measured
  AI-referral volume so a user can see whether rising AI mentions track rising AI-referred
  sessions. Correlation is a **descriptive projection**, never a predictive/LLM model.

This surface **consumes** ingest from the roadmap integrations (GSC/GA4/server-logs); it does
**not** own the connectors — see [`traffic.md`](traffic.md) and the GSC/GA4 integrations spec.
It reads referral evidence those integrations land, classifies it, and projects analytics.

## 2. Relationship to existing subsystems (grep before adding — invariant 2)

Reuse, do not duplicate:

- **`MetricSnapshot`** (`backend/app/models/analysis.py`) — the per-run aggregate already
  carries `visibility_score`, the full `metrics` dict, `analyzer_version`,
  `scoring_rule_version`, and `source_analysis_ids`/`source_artifact_ids`. Time-series
  visibility is a **projection over these rows** — never a recomputation (invariant 7).
- **`Project`** (`models/project.py`) + **`Brand`/`OwnedDomain`** (`models/brand.py`) — the
  owned-domain registry defines which site the referral events are *for*.
- **`Prompt`** (`models/prompt.py`, `theme`/`intent`) — the axis for theme-level breakdowns.
- **`PostgresTaskQueue`** (`app/orchestration/postgres_task_queue.py`) + the `TaskQueue`
  Protocol — ingest/classification runs as a queued task type (invariant 8), never inline in
  a request handler.
- **`logical_engine`** vocabulary (chatgpt|gemini|claude, invariant 10) — the referral
  classifier maps a detected AI source to the **same logical engine ids** where it can, plus
  an `ai_source` label for engines outside the audited three (e.g. `perplexity`, `copilot`,
  `google_ai_overview`) so referral analytics and visibility analytics share a join key.

## 3. Data model (new tables — UUID PKs, workspace-scoped)

Mirror the MVP shape: an immutable ingest **artifact**, a deterministic **derived
classification** with provenance, and a **projection snapshot**.

- **`ReferralEvent`** — immutable ingest artifact (invariant 3), written **once** by the
  worker that claimed the sync task, never mutated. `id`, `workspace_id`, `project_id`,
  `source` (`gsc|ga4|server_log`), `import_id` (FK to the `TrafficImport`/ingest batch that
  produced it — see [`traffic.md`](traffic.md)), `occurred_at`, `landing_url`,
  `referrer_host`, `referrer_url`, `utm_source`, `utm_medium`, `utm_campaign`, `user_agent`,
  `session_id_hash` (opaque; no PII), `raw` (JSONB: the **sanitized** source payload for
  traceability — see the sanitization contract below), `content_hash` (dedupe), `ingested_at`.
  Unique `(import_id, content_hash)` so a re-sync never double-inserts the same event.

  **Sanitization contract (invariant 6 privacy — applied BEFORE persistence).** The persisted
  columns must never carry PII, credentials, secrets, or raw device/network identifiers. The
  ingest worker runs a **deterministic, versioned redaction pass** (`REFERRAL_SANITIZE_VERSION`,
  in `config/analytics.py`) over every event *before* the immutable write, so the row is
  sanitized-at-rest and invariant 3 immutability still holds on the sanitized payload:
  - **`raw` is an allowlisted, redacted payload**, never the verbatim source row. Only fields on
    the config allowlist survive; everything else is dropped.
  - **`landing_url` / `referrer_url`** are stored with the query string stripped to a
    config allowlist of non-PII marketing params (`utm_*`, `ref`); all other query params,
    fragments, and any embedded credentials (`user:pass@`) are removed.
  - **`user_agent`** is stored only as far as the UA-rule match needs (family/heuristic token);
    full fingerprintable UA strings and any embedded ids are dropped.
  - **Raw IP addresses and device ids are never persisted** — the session is represented only by
    the opaque, salted `session_id_hash`; the raw client IP/device id is used transiently for
    hashing and discarded before the write.
  - **Retention + deletion:** persisted referral data is retained for `REFERRAL_RETENTION_DAYS`
    (config); a sweeper hard-deletes `ReferralEvent` rows (and their derived
    `ReferralClassification` rows) past that horizon, and a workspace/project deletion cascades
    to remove all referral rows. Deletion of the source ingest batch deletes its events.
- **`ReferralClassification`** — derived row (invariant 4). `id`, `workspace_id`,
  `project_id`, `referral_event_id` (FK — the immutable source, provenance), `is_ai_referral`
  (bool), `ai_source` (`chatgpt|gemini|claude|perplexity|copilot|google_ai_overview|other`),
  `logical_engine` (nullable; set when `ai_source` maps to an audited engine, invariant 10),
  `matched_rule_id` (which config rule fired), `match_signal` (`referrer|utm|user_agent`),
  `confidence` (deterministic bucket: `exact|heuristic`), `rule_version`, `analyzer_version`,
  `created_at`. Exactly one classification per `referral_event_id` (single writer, unique
  constraint). No LLM (invariant 9).
- **`AnalyticsSnapshot`** — projection (invariant 7), computed from persisted
  `ReferralClassification` + `MetricSnapshot` rows for a `(project, window)`. `id`,
  `workspace_id`, `project_id`, `window_start`, `window_end`, `granularity`
  (`day|week|month`), `metrics` (JSONB: AI-referral sessions by `ai_source`, referral share,
  theme-level visibility, correlation coefficients), `source_classification_ids` (JSONB),
  `source_snapshot_ids` (JSONB — the `MetricSnapshot` ids folded in), `analyzer_version`,
  `formula_version`, `created_at`. Rebuildable at any time from the persisted evidence; holds
  no data that isn't traceable to it (invariant 4 + 7).

All tables carry `workspace_id` and are read/written only through `require_workspace_member`
(invariant 5). All ids are string UUIDs; no integer PKs, no `user_id` scoping.

## 4. AI-referral classification (deterministic rules in config — invariant 1)

The classifier is a pure function `(ReferralEvent, rule_table) -> ReferralClassification`.
The **rule table lives in config** (`backend/app/core/config/analytics.py`), never inline in
service code, and is **versioned** (`AI_REFERRAL_RULE_VERSION`) so every classification is
traceable to the exact rules that produced it (invariant 4). Rule shape:

- **Referrer-host rules** — an allow-map of known AI hostnames → `ai_source`
  (`chat.openai.com`/`chatgpt.com` → `chatgpt`, `gemini.google.com` → `gemini`,
  `perplexity.ai` → `perplexity`, `copilot.microsoft.com` → `copilot`, …). Suffix-safe host
  matching (boundary-safe, no substring false-positives).
- **UTM rules** — `utm_source`/`utm_medium` equality/pattern matches (e.g.
  `utm_source=chatgpt.com`), for platforms that tag outbound links.
- **User-agent rules** — for server-log ingest, verified AI-assistant UA substrings. (Verified
  *crawler* identification and crawler-to-page analytics are a **separate** roadmap item —
  Release 1.3 server/edge-log ingestion — cross-referenced, not built here.)

Rules are evaluated in a **fixed priority order** (referrer → utm → user_agent) so the same
event always classifies the same way (determinism, invariant 9). Unmatched events get
`is_ai_referral=false, ai_source=other`. **No LLM** may be introduced to "guess" a source.

## 5. Ingest + classification lifecycle

Ingest is **not** owned here — the GSC/GA4/server-log connectors land `ReferralEvent` rows as
part of their sync task (see [`traffic.md`](traffic.md) §sync). Classification runs as its own
idempotent, queued task on the Postgres queue (invariant 8):

1. A `classify_referrals` task is enqueued per `import_id` after ingest commits.
2. Worker claims it with `FOR UPDATE SKIP LOCKED`, **commits the claim before any I/O**
   (invariant 8), then reads the unclassified `ReferralEvent` rows for that import.
3. For each event it writes exactly one `ReferralClassification` (immutable-source provenance,
   `rule_version` + `analyzer_version`). Re-running produces a **new** classification identity
   only via a new task; it never mutates an existing row (invariant 3).
4. On completion it enqueues/refreshes the `AnalyticsSnapshot` projection for affected windows.

Cancellation is cooperative (invariant 9): the worker stops at the event/batch boundary.

## 6. API surface (roadmap; `/api/v1`, projections only — invariant 7)

All endpoints are read/projection endpoints; none call a provider or re-extract.

- `GET /projects/{id}/llm-analytics?from=&to=&granularity=` — headline AEO Insights: AI-referral
  volume + share over time, per-`ai_source` breakdown, cross-engine visibility time-series, and
  the visibility↔referral correlation summary. Reads `AnalyticsSnapshot` (built from persisted
  evidence).
- `GET /projects/{id}/llm-analytics/referrals?source=&from=&to=` — paged classified referral
  rows (drill-down) — projection over `ReferralClassification` joined to `ReferralEvent`.
- `GET /projects/{id}/llm-analytics/themes?from=&to=` — prompt/theme-level visibility rollup
  over `MetricSnapshot` (grouped by `Prompt.theme`/`intent`).

All workspace-scoped via `require_workspace_member` (invariant 5); cross-workspace access
returns 403/404, not data. If a future authenticated data source needs credentials, they
follow the BYOK Fernet pattern (`encrypt_secret`/`decrypt_secret`) and are **never** returned
in a DTO or logged (invariant 6).

## 7. Frontend (roadmap)

- **Route:** `/analytics` — already stubbed as a **disabled "soon"** nav item ("LLM Analytics",
  `BarChart3`) in the **Analytics** group of `frontend/components/layout/nav-items.ts`. Flip
  `live: true` when shipping.
- Reuse the MVP contract layer: add `frontend/lib/api/analytics.ts` (API module + zod schemas
  in `schemas.ts`), a `queryKeys.analytics.*` entry in `query-keys.ts`, and the existing
  `trend-chart` primitive (`components/ui/trend-chart.tsx`, built but unused — see
  frontend-architecture.md §9) for the time-series charts.
- Every response passes `strictValidate`; ids are `z.string().uuid()`; no `user_id`.
- Same-origin `/api/*` proxying only (invariant 12); TanStack Query with the shared retry
  policy. Polling-first — no real-time streaming at first (§9 non-goals).

## 8. Config & tuning knobs (all in `backend/app/core/config/analytics.py`)

Nothing tunable is hard-coded in service/worker code (invariant 1). New knobs:

- `AI_REFERRAL_RULE_VERSION` — stamped onto every `ReferralClassification` (invariant 4).
- `AI_REFERRAL_HOST_RULES` / `AI_REFERRAL_UTM_RULES` / `AI_REFERRAL_UA_RULES` — the versioned
  match tables (host → `ai_source`, etc.).
- `AI_SOURCE_TO_LOGICAL_ENGINE` — the map from `ai_source` to the audited `logical_engine`
  vocabulary (invariant 10) so referral + visibility analytics share a join key.
- `ANALYTICS_DEFAULT_GRANULARITY`, `ANALYTICS_MAX_WINDOW_DAYS`, `ANALYTICS_SNAPSHOT_TTL_S`
  (snapshot rebuild cadence), `CORRELATION_MIN_SAMPLE` (below which correlation is reported as
  `insufficient_data`, never a fabricated number).
- `REFERRAL_SANITIZE_VERSION` (stamped on the redaction pass), the `raw`/URL-param allowlists,
  and `REFERRAL_RETENTION_DAYS` — the sanitization + retention contract for `ReferralEvent`
  (invariant 6 privacy); redaction is applied before the immutable write.

Reuse the existing `ANALYZER_VERSION`/`SCORING_RULE_VERSION` constants (`config/analysis.py`)
for the visibility-derived parts of the snapshot — do not fork a second version constant
(invariant 2).

## 9. Suggested build order

1. Config: referral rule tables + version constants + snapshot knobs (`config/analytics.py`)
   and the migration for the 3 tables.
2. Deterministic classifier (pure function, table-tested against fixture events; no live data).
3. `classify_referrals` queued task + worker path (reuse `PostgresTaskQueue`, commit-before-I/O)
   — gated behind the ingest connectors landing `ReferralEvent` rows.
4. `AnalyticsSnapshot` projection builder (folds classifications + existing `MetricSnapshot`).
5. API routers (projections only) + zod contracts.
6. Frontend `/analytics` screen (wire `trend-chart`, flip the disabled nav item live).

## 10. Explicit non-goals (MVP of this surface)

- **No LLM anywhere** in referral classification or any headline metric (invariant 9); AI
  source detection is deterministic rules only.
- **No real-time streaming** at first — analytics are batch projections refreshed on sync;
  live event streaming is a later iteration.
- **No ingest connectors here** — GSC/GA4/server-log sync is owned by the integrations +
  [`traffic.md`](traffic.md) specs; this surface only classifies + projects what they land.
- **No verified-crawler / crawler-to-page analytics** — that is Release 1.3 server/edge-log
  ingestion, a separate roadmap item.
- **No predictive modelling** — visibility↔referral correlation is a descriptive projection
  over persisted evidence, never a forecast; below `CORRELATION_MIN_SAMPLE` it reports
  `insufficient_data`, never a made-up figure.

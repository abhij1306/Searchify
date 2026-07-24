# Roadmap — Opportunities

> **Status: implemented (v1).** The deterministic core below is coded: the config-owned rule
> catalog + priority formula, pure detectors over the persisted visibility/site evidence, the
> supersede-not-mutate recompute service (inline-only, no queue), the workspace-scoped API
> (`/projects/{id}/opportunities*`, `/opportunities/{id}`), and the CSV/MD exports. Two rules
> stay **deferred** (disabled in config): `low_share_of_voice_theme` (needs per-topic SOV) and
> `high_traffic_low_visibility` (needs the Traffic surface). It follows the same conventions as
> the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP LOCKED` task queue,
> immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

A **prioritized, actionable list of AEO opportunities** derived **deterministically** from
evidence Searchify has already persisted. Each opportunity answers "here is a concrete thing to
fix or do, here is why (the evidence), and here is how important it is (the score)". Examples:

- A **high-value prompt** where the brand is absent but competitors are cited (from the
  visibility slice).
- An **owned page that should be cited but isn't** (owned domain exists, but no owned citation
  appears for a prompt it should win).
- **Thin or missing structured data** on an owned page (cross-reference
  [`technical-audit.md`](technical-audit.md) `SiteIssue`, e.g. missing `FAQPage`/`Organization`
  JSON-LD).
- A **theme/topic with low share-of-voice** (cross-reference [`topics.md`](topics.md)).

Opportunities is fundamentally a **projection + ranking layer** over already-persisted data
(invariant 7): the MVP visibility analysis (`ResponseAnalysis`, `Citation`, `CompetitorMention`,
`MetricSnapshot`), plus roadmap `SiteIssue` (site audit) and roadmap traffic data. It performs
**no new extraction, calls no provider, and uses no LLM** (invariant 9). Prioritization is a
**deterministic scoring formula defined in config** (invariants 1 + 9). It is structurally
analogous to the technical-audit **Issues catalog**, but its inputs span *all* Searchify
subsystems rather than one crawl.

## 2. Why deterministic (and not an LLM)

The value of an opportunity list is that it is **traceable and reproducible**: the same
persisted evidence + the same `formula_version` always yields the same ranked list. That is only
possible if the detection rules and the priority score are deterministic (invariant 9) and every
row carries provenance back to the exact source rows + versions (invariant 4). An LLM ranking
would be non-reproducible, un-auditable, and would violate "no LLM for headline metrics". If
LLM-written remediation copy is ever added, it is a **projection layer** rendered *over* an
already-computed deterministic opportunity — never the detector or the ranker (see §10).

## 3. Opportunity rules (deterministic detectors, in config)

Like the technical-audit **rule catalog**, opportunity rules live in config
(`app/core/config/opportunities.py`), never inline in service code (invariant 1). Each rule:
`rule_id`, `opportunity_type`, source subsystem(s), a deterministic predicate over persisted
rows, default `severity`/weight, thresholds, and human-readable title + remediation text.
Because the catalog is **config, not a table**, a persisted row cannot FK to it: an
`Opportunity.rule_id` is a **validated, versioned string** — the write path validates it against
the config catalog (an unknown `rule_id` is rejected) and stamps the catalog's `rule_version`
onto the row for provenance (invariants 1 + 4). This keeps rules config-zero-tolerance while
every derived row stays traceable to the exact rule + version that produced it.
Illustrative rules:

| `rule_id` | Type | Source rows (persisted) | Fires when |
|---|---|---|---|
| `brand_absent_high_value_prompt` | `visibility` | `ResponseAnalysis` + `Citation` + `CompetitorMention` | no owned citation across repetitions **and** ≥1 competitor cited |
| `owned_page_not_cited` | `visibility` | `Citation` + `OwnedDomain` (`models/brand.py`) | owned domain exists but no owned citation for a targeted prompt |
| `low_share_of_voice_theme` | `visibility` | `MetricSnapshot` + `Prompt.theme` (roadmap `Topic`) | theme/topic SOV below threshold |
| `missing_structured_data` | `site` | `SiteIssue` (technical-audit) | rule category = structured-data on an owned page |
| `thin_content` | `site` | `SiteIssue` | word-count-below-threshold issue on an owned page |
| `high_traffic_low_visibility` | `traffic` | roadmap traffic rows + `MetricSnapshot` | high traffic/intent page with low AEO visibility |

The detectors read persisted rows only (invariant 7); thresholds are config, not inline
(invariant 1). Before adding a rule, **grep the existing rule catalogs** (`config/site_audit.py`
rules, `config/opportunities.py`) to avoid duplicating a concept (invariant 2).

## 4. Deterministic scoring / prioritization

The priority score is a **pure function of persisted evidence**, defined in config (invariants 1
+ 9). A transparent, versioned formula, e.g.:

```
priority = severity_weight[rule_id]
         * value_factor(prompt_intent, traffic_or_volume)   # config-weighted
         * gap_factor(competitor_sov, owned_citation_rate)   # from persisted metrics
```

All weights and factor tables live in `config/opportunities.py`; the resolved value is stored on
the row along with the `formula_version`. Bumping the formula bumps `formula_version` and
re-computation produces **new** opportunity rows — never an in-place rewrite of the score's
meaning (invariants 3 + 4). No randomness, no LLM (invariant 9).

## 5. Data model (new tables — UUID PKs, workspace-scoped)

Workspace-scoped through `project_id` (invariant 5); UUID PKs, no `user_id` scoping. Mirror the
`SiteIssue` derived-row shape from [`technical-audit.md`](technical-audit.md).

- **`Opportunity`** — a derived opportunity **instance**. `id`, `workspace_id`, `project_id`,
  `rule_id` (a **validated, versioned string**, not a DB FK — the config catalog is code, not a
  table; the write path validates `rule_id` against `config/opportunities.py` and rejects an
  unknown value, and records the catalog's `rule_version` below for provenance — invariants 1 +
  4), `opportunity_type`
  (`visibility|site|traffic|topic`), `severity`, `priority_score` (the computed float),
  `title` (from the rule), `target_prompt_id` / `target_url` / `target_theme` (whichever the
  rule targets, nullable), **`evidence`** (JSONB: the concrete offending values + the source row
  ids), and the **provenance columns** (invariant 4): `source_analysis_ids` /
  `source_issue_ids` / `source_metric_ids` / `source_traffic_ids` (JSONB lists of the
  `ResponseAnalysis` / `SiteIssue` / `MetricSnapshot` / traffic row ids it was computed from),
  `analyzer_version`, `rule_version`, `formula_version`, `status`
  (`open|in_progress|dismissed|resolved`), timestamps. A row with no traceable source + versions
  is invalid (invariant 4). One row per (rule, target).
- **`OpportunitySnapshot`** *(optional)* — an aggregate **projection** row per computation run,
  analogous to `MetricSnapshot`: `id`, `workspace_id`, `project_id`, `run_id` (the computation
  run identity), counts by type/severity, total/median priority, `analyzer_version` +
  `formula_version`, `source_*_ids`, `computed_at`. Snapshots are immutable per run (invariant
  3); a re-run creates a new snapshot identity, never an overwrite.

`status` (the human workflow) is the **only** mutable field — the derived facts (`evidence`,
scores, provenance) are written once. Changing a status never edits the evidence.

## 6. Computation lifecycle

Computation is a projection pass, not an extraction. It can run:
- **inline/synchronously** on `POST /projects/{id}/opportunities/recompute` for small projects
  (pure DB reads + deterministic scoring), or
- as a **queued task** on the shared `PostgresTaskQueue` for large projects — same queue-row
  contract as `AuditTask` (`FOR UPDATE SKIP LOCKED`, commit-before-I/O even though the "I/O"
  here is only DB, heartbeat, sweeper, cooperative cancel — invariants 8 + 9). Reuse the
  existing `TaskQueue` Protocol; do not add a second queue (invariant 2).

A recompute reads the latest persisted analysis/site/traffic rows, evaluates every enabled rule,
scores each hit, and writes fresh `Opportunity` rows (superseding prior `open` rows for the same
(rule, target) by writing a new identity and closing the stale one — never mutating evidence).
It never calls a measurement provider or the discovery model (invariant 7).

## 7. API surface (roadmap; `/api/v1`)

Modeled on the technical-audit Issues catalog. All workspace-scoped via
`require_workspace_member` (invariant 5).

- `GET /projects/{id}/opportunities?type=&severity=&status=&rule_id=&min_priority=` — filtered,
  paged, **priority-sorted** list (projection, invariant 7).
- `GET /opportunities/{id}` — detail incl. `evidence` + full provenance (source ids + versions).
- `POST /projects/{id}/opportunities/recompute` — trigger a deterministic recompute (optionally
  scoped to `audit_id` / `site_crawl_id`). Returns the run id / snapshot id.
- `PATCH /opportunities/{id}` — update `status` only (`in_progress|dismissed|resolved`); never
  edits evidence or score.
- `GET /projects/{id}/opportunities/summary` — `OpportunitySnapshot` projection (counts by
  type/severity, totals).
- `GET /projects/{id}/opportunities/export.{csv,md}` — reproducible export (projection,
  invariant 7).

## 8. Config & tuning knobs (all in `app/core/config/opportunities.py`)

Never inline (invariant 1):
- The **rule catalog** (`rule_id`, type, predicate thresholds, default severity/weight,
  title + remediation text).
- `OPPORTUNITY_TYPES` / severity enum frozensets (like `PROMPT_INTENTS`).
- The **scoring formula** weights: `SEVERITY_WEIGHTS`, `INTENT_VALUE_WEIGHTS`,
  `gap_factor` / `value_factor` coefficients, `MIN_PRIORITY_TO_SURFACE`.
- `RULE_VERSION` and `FORMULA_VERSION` (stamped on every derived row; bump on any change to
  rule logic or scoring — mirrors `SCORING_RULE_VERSION` in `config/analysis.py`).
- Recompute concurrency / queue knobs if run as a task.

## 9. Frontend (roadmap)

- **Route:** `/opportunities` — already stubbed as a disabled **"soon"** nav item in
  `frontend/components/layout/nav-items.ts` (Actions group, `label: 'Opportunities'`,
  `icon: Lightbulb`). Flip `live: true` when shipped.
- Reuse the MVP contract layer: add `frontend/lib/api/opportunities.ts` + zod `strictValidate`
  schemas in `schemas.ts`, and `queryKeys.opportunities.*` in `query-keys.ts` (mirror
  `queryKeys.runs.*` list/detail). All `id`/`*_id` fields `z.string().uuid()`.
- Screen shape: a filterable, priority-sorted dense table (severity badges + type filter +
  status filter), reusing the same table/badge primitives as the Issues catalog, with an
  evidence drill-down panel (source prompt/URL/issue + the offending value) and a
  status-workflow control. A summary strip renders the `OpportunitySnapshot` counts.
- Same-origin `/api/*` proxy (invariant 12); polling-first for recompute progress if queued.

## 10. Suggested build order

1. Config: `opportunities.py` (rule catalog + scoring weights + `RULE_VERSION` /
   `FORMULA_VERSION`) + migration for `Opportunity` (+ optional `OpportunitySnapshot`).
2. Deterministic detectors over the **visibility slice only** first (`ResponseAnalysis` /
   `Citation` / `CompetitorMention` / `MetricSnapshot`) — table-tested, no LLM.
3. Deterministic scoring/prioritization + provenance stamping (source ids + versions).
4. Recompute service (inline first) + `Opportunity` write path (supersede-not-mutate).
5. API routers (list/filter/detail/summary/export + status PATCH) — thin, delegate to
   `domain/opportunities/*`.
6. Frontend `/opportunities` catalog (flip the disabled nav item live).
7. Extend detectors to `SiteIssue` (technical-audit) and `Topic` SOV (topics) as those surfaces
   land; traffic-sourced rules last (depend on the roadmap Traffic surface).
8. Optional: queue the recompute for large projects on the shared `PostgresTaskQueue`.

## 11. Explicit non-goals (MVP of this surface)

- **No LLM-generated opportunities** — detection and ranking are deterministic config-defined
  rules only (invariant 9). No "AI suggests what to fix".
- **No new extraction / provider calls** — Opportunities is a pure projection over persisted
  analysis + site-audit + traffic rows (invariant 7). If the data isn't persisted, no
  opportunity references it.
- **No in-place edits to evidence or score** — only the human `status` is mutable; recomputes
  create new derived rows with fresh versions (invariants 3 + 4).
- **No auto-remediation** — Opportunities points at fixes; executing them (writing content,
  editing pages) belongs to [`content-writer.md`](content-writer.md) / the site itself.
- LLM-written remediation *copy*, if ever added, is a projection over an already-computed
  deterministic opportunity — never the detector or the ranker.

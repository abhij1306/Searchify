# Roadmap — Brand / Competitors / E-E-A-T rich profile

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

The MVP already stores a **basic brand identity** per project: a single `Brand` with normalized
`BrandAlias` rows, `Competitor` rows (name + JSONB alias/domain arrays), `OwnedDomain`,
`UnintendedDomain`, plus the project-level `brand_name`, `website_url`, `country_code`,
`language_code`, and `benchmark_mode` (see `backend/app/models/brand.py` and
`backend/app/models/project.py`). Those rows exist to feed the deterministic scorer
(`app/analysis/scoring.py::ScoringConfig.from_project`, rebuilt from rows by the shim in
`domain/projects/shim.py`) — nothing more.

This surface **expands that thin setup into a rich brand profile** without duplicating what
already exists (invariant 2). It adds:

- a **brand knowledge base** (positioning, products/services, target audience, canonical
  descriptions, `sameAs` links) that both humans and the roadmap prompt-generation / accuracy
  pipelines can read;
- **E-E-A-T signals** — Experience / Expertise / Authoritativeness / Trust — collected
  deterministically from evidence (authorship, entity / knowledge-graph presence, review &
  rating signals, `sameAs` / social links, structured-data org identity);
- **richer competitor intelligence** — a per-competitor profile with its own tracked visibility
  over time, layered on top of the existing `Competitor` row;
- **optional AI-assisted brand analysis** driven by the **discovery/analysis model**
  (`DiscoveryModelConfig`, master plan §2.3), which is *distinct from the measurement engines*
  (chatgpt/gemini/claude) that produce headline visibility numbers.

**Hard positioning line:** AI-assisted output here is **advisory metadata**, never a headline
metric. E-E-A-T "signals" are the deterministic *presence/absence of evidence* (does an
Organization JSON-LD exist? are there `sameAs` links? is there a knowledge-graph entity id?),
**not** an LLM-fabricated 0–100 "authority score" presented as fact (invariant 9). Any
LLM-derived interpretation is persisted with full model identity + provenance and labelled
advisory (invariants 4 + 10).

## 2. Relationship to existing models (extend, don't duplicate — invariant 2)

Grep first. These already own the concept — **reuse them**:

| Existing (keep as the owner) | This surface adds |
|---|---|
| `Project` (`models/project.py`) — brand_name, website_url, country, language, benchmark_mode | 1:1 `BrandProfile` for extended attributes (do **not** widen `Project`) |
| `Brand` + `BrandAlias` (`models/brand.py`) | knowledge-base fields hang off `BrandProfile`, keyed to the project's `Brand` |
| `Competitor` (name + JSONB aliases/domains) | 1:1 `CompetitorProfile` for rich attributes + tracked visibility |
| `OwnedDomain` / `UnintendedDomain` | unchanged; `sameAs` / entity links are separate signals, not domain-ownership |
| `DiscoveryModelConfig` (`models/provider.py`, plumbing-only) | this surface is the **first real invoker** of the discovery/analysis model |

The **scorer contract is untouched.** `ScoringConfig.from_project` and the shim continue to read
only the MVP rows; nothing in this surface may change what the deterministic headline scorer
consumes. Profile/E-E-A-T data is *additive* and read by projections + the roadmap
prompt/accuracy pipelines, never by `score_execution`.

## 3. Data model (new tables — UUID PKs, workspace-scoped)

All tables carry a `workspace_id` (denormalized for direct invariant-5 filtering) **and** a
`project_id`; access is enforced via `require_workspace_member` on the owning project
(invariant 5). All ids are string UUIDs; no integer PKs, no `user_id`.

### 3.1 `BrandProfile` — extended brand attributes (1:1 with `Brand`)

Human-authored + optionally AI-suggested knowledge base. `id`, `workspace_id`, `project_id`,
`brand_id` (FK → `brands.id`, unique — one profile per brand), and the attribute columns:
`positioning` (Text), `description` (Text, canonical brand blurb), `products_services` (JSONB
array), `target_audience` (Text), `markets` (JSONB array of country codes), `founding_year`
(Integer, nullable), `same_as_links` (JSONB array — Wikipedia/Wikidata/Crunchbase/LinkedIn/etc.
identity URLs), `social_handles` (JSONB), `knowledge_graph_entity_id` (String, nullable — e.g.
Wikidata QID or Google KG mid), `logo_url` (String), timestamps. Each attribute optionally
carries a **source token** (`manual` | `web_evidence` | `ai_suggested`) so the UI can show
provenance per field; where a field was AI-suggested it also references the
`BrandAnalysisArtifact` that produced it (§3.4). Human edits always win and flip the field's
source to `manual`.

### 3.2 `EEATSignal` — derived E-E-A-T evidence rows (append-only, provenance-stamped)

One row per **discovered signal**, derived from an immutable evidence source, following the same
provenance discipline as `ResponseAnalysis` (invariant 4). Columns:

- `id`, `workspace_id`, `project_id`, `brand_id`.
- `pillar` — `experience` | `expertise` | `authoritativeness` | `trust` (enum in config).
- `signal_type` — e.g. `organization_schema` | `author_schema` | `sameas_link` |
  `knowledge_graph_entity` | `review_rating` | `aggregate_rating` | `https_valid` |
  `about_page` | `contact_present` (catalog in config, §7).
- `present` (Boolean) + `value` (JSONB — the exact evidence: the rating count, the entity id,
  the `sameAs` URL list, the schema `@type`).
- **Provenance (invariant 4):** `source_artifact_id` (FK → the immutable web-evidence artifact,
  §5, **`ondelete=RESTRICT`** — a signal can never lose its source; the source artifact is
  retained while any signal cites it, or the FK cascade-deletes dependent signals with it, but
  it is **never** nulled out), `collector_version` (String — the deterministic collector rule
  set), `analyzer_version`. A signal with no traceable source + version is invalid.
- `collected_at` timestamp.

**These are deterministic collection results, not scores.** `present`/`value` record what the
evidence literally contains. There is **no LLM headline E-E-A-T score** (invariant 9). A rollup
(e.g. "3 of 4 pillars have positive evidence") is a **projection** computed at read time over
these rows (invariant 7) — not a stored, LLM-authored number.

### 3.3 `CompetitorProfile` — rich competitor intelligence (1:1 with `Competitor`)

`id`, `workspace_id`, `project_id`, `competitor_id` (FK → `competitors.id`, unique),
`website_url`, `positioning` (Text), `same_as_links` (JSONB), `knowledge_graph_entity_id`,
`notes` (Text), `tracked` (Boolean — include in cross-run competitor visibility), `source`
token, timestamps. **Tracked visibility is not stored here** — it is a projection over the
existing `CompetitorMention` / `MetricSnapshot` rows keyed by `competitor_id`, so there is one
owner of visibility numbers (the analysis rows) and no recompute (invariants 2 + 7).

### 3.4 `BrandAnalysisArtifact` — optional AI-assisted analysis output (immutable)

Written once per AI-assisted analysis run and **never mutated** (invariant 3). This is the
advisory layer. Columns: `id`, `workspace_id`, `project_id`, `brand_id`, the **full model
identity triple** (invariant 10) `logical_engine` + `transport_provider` + `transport_model`
(resolved from `DiscoveryModelConfig` → its `ProviderConnection`), `prompt_template_version`
(String), `input_evidence_snapshot` (JSONB — which `BrandEvidence` artifacts fed the analysis,
by id + hash; **never** contains the BYOK key, invariant 6), `output` (JSONB — suggested
positioning / audience / products, each with a `confidence` + `reason`), `status`, `error_code`,
`created_at`. Suggested values only reach `BrandProfile` when a user accepts them, at which
point the profile field records `source=ai_suggested` + this artifact id. The analysis model is
resolved via BYOK at execution time and its key is never persisted (invariant 6).

### 3.5 `BrandEvidence` — web-evidence source artifacts (immutable; roadmap connector)

The deterministic collector and the AI-assisted analysis both read from immutable web-evidence
artifacts produced by the **roadmap `connectors/web_evidence` connector** (master plan §6.2
`BrandEvidenceSnapshot` / `BrandEvidencePage`). Fields mirror the master plan: fetched URL,
`final_url`, `content_type`, `content_hash`, extracted structured-data blocks (JSON-LD/microdata
`@type` + properties), page title, canonical URL, compact extracted text, fetch diagnostics,
`fetched_at`. Written once (invariant 3); large raw bodies go to object storage (roadmap) with
the key + hash stored here. **No scraping beyond this connector** (see non-goals). `EEATSignal`
rows and `BrandAnalysisArtifact` inputs both cite these by id for provenance.

## 4. Collection & analysis lifecycle

Reuse the audit pattern (`app/orchestration/*`, `PostgresTaskQueue`). Profile enrichment is a
**separate task type**, opt-in per project:

1. **Web-evidence fetch** — enqueue one task per candidate URL (brand site + declared `sameAs`
   links, respecting robots), claimed with `FOR UPDATE SKIP LOCKED` (invariant 8), producing
   immutable `BrandEvidence` artifacts. Commit the claim before network I/O; heartbeat; sweeper
   reclaims expired leases (invariant 8). Because the brand site + `sameAs` URLs are
   user-controlled, every fetch **must go through the shared SSRF-guarded fetcher** (arch doc
   §Security SSRF stance) — respecting robots.txt alone does not stop requests to internal
   addresses. The fetcher is **HTTP(S)-only**, **revalidates every redirect hop** (no blind
   redirect following), and enforces **approved-host + resolved-address checks** that block
   loopback, private, link-local, and cloud-metadata ranges; it re-resolves and re-checks the
   address it actually connects to, defending against **DNS rebinding**.
2. **Deterministic E-E-A-T collection** — parse the immutable evidence and emit `EEATSignal`
   rows (present/value + provenance + `collector_version`). No LLM (invariant 9).
3. **Optional AI-assisted analysis** — only if `BRAND_ANALYSIS_ENABLED` is true, a
   `DiscoveryModelConfig` is active, *and* the user opted in. Calls the discovery/analysis model,
   writes one immutable `BrandAnalysisArtifact` with full model identity + provenance. Advisory
   only.

Cancellation is cooperative — workers stop at the URL/analysis boundary (invariant 9). Re-running
enrichment produces **new** artifact identities, never an in-place overwrite (invariant 3).

## 5. API surface (roadmap; `/api/v1`, extend `/projects/{id}`)

All workspace-scoped via `require_workspace_member` (invariant 5). Extend the existing
`/projects` router (`app/api/projects.py`) rather than adding a parallel resource (invariant 2):

- `GET /projects/{id}/brand-profile` — profile projection (attributes + per-field source).
- `PUT /projects/{id}/brand-profile` — upsert human-authored attributes (flips edited fields to
  `source=manual`).
- `GET /projects/{id}/eeat` — E-E-A-T signals + the computed pillar rollup **projection**
  (invariant 7); every number links to its `source_artifact_id`.
- `POST /projects/{id}/brand-evidence:collect` — enqueue a web-evidence + E-E-A-T collection run
  (returns the task/run id; cooperative cancel via the shared cancel path).
- `GET /projects/{id}/competitors/{cid}/profile` / `PUT` — competitor rich profile.
- `GET /projects/{id}/competitors/visibility` — per-competitor tracked-visibility projection over
  existing `CompetitorMention`/`MetricSnapshot` rows (no recompute, invariant 7).
- `POST /projects/{id}/brand-analysis` — enqueue optional AI-assisted analysis. Enabled **only
  when `BRAND_ANALYSIS_ENABLED` is true *and* an active `DiscoveryModelConfig` exists**; if
  either condition is unmet it keeps the existing **501 / disabled** behavior, mirroring the
  `/prompt-sets/{id}/generate` stub.
- `GET /projects/{id}/brand-analysis/{artifactId}` — advisory analysis artifact (with model
  identity + confidence/reason), clearly flagged as AI-suggested.

BYOK for the analysis model follows the Fernet pattern — key resolved from `ProviderConnection`
at execution, never returned in a DTO, and the brand/competitor list is never leaked into a
provider prompt beyond the evidence the user authored (invariant 6).

## 6. Frontend (roadmap)

- **Route:** `/brand` — a Brand suite (Profile, Competitors, E-E-A-T tabs). Add it as a
  disabled **"soon"** nav item; today `frontend/components/layout/nav-items.ts` has the roadmap
  items under existing groups (Setup lives in the **On Page** group). Add `/brand` there with
  `live: false` until this surface ships, then flip to `live: true`.
- Reuse the MVP contract layer: add a `brandProfile.ts` API module + zod `strictValidate` schemas
  in `frontend/lib/api/`, `queryKeys.brandProfile.*`, and the existing card/table/badge
  primitives. Extend the existing `projectSchema`/`competitorSchema` rather than forking them.
- **E-E-A-T view = evidence, not verdicts:** each pillar shows present/absent signal chips that
  drill down to the exact evidence + source artifact; AI-suggested profile fields render with a
  distinct "AI-suggested" affordance + confidence, visually separated from manual/evidence-backed
  fields so provenance is never ambiguous.
- Same-origin `/api/*` proxying (invariant 12); polling-first for collection-run progress like
  `/runs`.

## 7. Config & tuning knobs (all in `backend/app/core/config/*`)

Nothing tunable is hard-coded in service/worker code (invariant 1). Add a `brand_profile.py`
config module:

- `EEAT_PILLARS` and the **E-E-A-T signal-type catalog** (each: `signal_type`, `pillar`,
  human description, and the deterministic detection rule reference).
- `COLLECTOR_VERSION` (bumped when the deterministic E-E-A-T collection rules change — the
  provenance stamp on `EEATSignal`, mirroring `ANALYZER_VERSION` in `config/analysis.py`).
- `BRAND_PROFILE_SOURCE_TOKENS` (`manual` | `web_evidence` | `ai_suggested`).
- `BRAND_ANALYSIS_PROMPT_TEMPLATE_VERSION` + `BRAND_ANALYSIS_ENABLED` flag (default false — the
  `POST /projects/{id}/brand-analysis` endpoint is enabled only when this is true *and* an active
  `DiscoveryModelConfig` exists; otherwise it stays 501/disabled, §5).
- Web-evidence fetch knobs (max URLs per brand, per-host delay, request timeout, respect-robots)
  — reuse the site-audit fetch knobs where they already exist (invariant 2).
- The discovery/analysis model itself is chosen from `DiscoveryModelConfig` (workspace data),
  not config; only its *defaults/guardrails* live in `config/provider_catalog.py`.

## 8. Suggested build order

1. Config: `brand_profile.py` (pillars + signal catalog + versions + flags) + migration for
   `BrandProfile`, `EEATSignal`, `CompetitorProfile`, `BrandAnalysisArtifact`, `BrandEvidence`.
2. `BrandProfile` + `CompetitorProfile` CRUD (manual authoring) + projections — no fetching yet.
3. `connectors/web_evidence` fetch → immutable `BrandEvidence` artifacts (reuse queue + state
   machine; fixture-server tests, no live internet).
4. Deterministic E-E-A-T collector → `EEATSignal` rows (table-tested, provenance-stamped).
5. E-E-A-T + competitor-visibility projection endpoints (read-only, invariant 7).
6. Optional AI-assisted analysis (first real `DiscoveryModelConfig` invoker) → immutable
   `BrandAnalysisArtifact`, advisory, behind the enable flag.
7. Frontend `/brand` suite (flip the disabled nav item live).

## 9. Explicit non-goals (MVP of this surface)

- **No LLM-fabricated E-E-A-T scores presented as deterministic facts.** Signals are the
  presence/absence of evidence; any rollup is a read-time projection over deterministic rows
  (invariants 7 + 9).
- **No scraping beyond the roadmap `web_evidence` connector.** No headless browser, no
  crawling third-party sites for competitor intel beyond declared URLs / `sameAs` links.
- **No AI-suggested value silently overwriting human-authored profile fields** — AI output is
  advisory and only lands on acceptance, always tagged with model identity + provenance
  (invariants 4 + 10).
- **No change to the deterministic scorer's inputs.** Profile/E-E-A-T data never feeds
  `score_execution` or a headline visibility metric.
- **No new brand-identity owner.** Aliases/domains/competitors stay owned by the existing
  `models/brand.py` rows; this surface extends them (invariant 2).
- **The brand/competitor list is never sent to a measurement engine as part of a prompt**
  (invariant 6) — unchanged by this surface.

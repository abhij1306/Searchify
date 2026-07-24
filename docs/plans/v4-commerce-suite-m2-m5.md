# v4 — Commerce Suite (Agentic Commerce): M2–M5 implementation plan

**Status:** ready to build. M1 is shipped; M2–M5 are unbuilt.
**Audience:** an engineer or agent picking this up cold. Everything needed to build is in this
file plus the codebase — no other design doc is required.

**Prerequisite reading:** [`../../Agents.md`](../../Agents.md) and
[`../invariants.md`](../invariants.md). Every rule there applies to every line of this plan.

---

## 1. What this builds

Searchify's Commerce Suite is a closed **measure → diagnose → attribute** loop at **SKU level**,
built on the same deterministic, evidence-provenanced architecture as the brand-visibility slice.

- **Measure** — which products get mentioned in AI shopping answers, where they rank, at what
  quoted price, which merchants the engine sends buyers to, which competitor products appear
  alongside, and which attributes the engine uses as comparison dimensions.
- **Diagnose** — deterministic rules that turn measured gaps into a prioritized fix list, with
  optional BYOK content drafts as approve-to-copy artifacts.
- **Attribute** — connect AI-referred traffic to real orders: revenue per surface and per SKU.

It is a **measurement and diagnosis product with attribution**. It never writes to storefronts or
ad feeds, never manages merchant feeds, and never participates in a transaction (§11).

### Module map

| Phase | Name | Size | Depends on |
|---|---|---|---|
| **M2a** | Analyzer v2 — the per-SKU metric set | M | M1 (shipped) |
| **M2b** | Shopping-intent prompt fanout | S | M2a |
| **M2c** | Shopping-surface probes (gated; likely stays empty) | M/surface | M2a |
| **M3** | Opportunities engine + commerce rule catalog | L | M2a; site-health extension |
| **M4** | Commerce integrations: catalog, orders, feed health | L | Shipped integrations framework |
| **M5** | AI attribution & revenue impact | M | M4 (for Layer A2); shipped LLM Analytics |

**Build order:** M2a → M2b → (M3 ∥ M4) → M5. M2c slots in whenever a surface gains a sanctioned
API.

**Fastest path to user-visible value:** M2a → M5 Layer A1 → M4 Shopify orders → M5 Layer A2.
Layer A1 needs only the shipped GA4 integration plus two dataset templates, so revenue-by-AI-source
reporting can ship well before the commerce connectors land.

---

## 2. Ground rules

These are not negotiable and are the most common source of rejected work in this codebase.

1. **Config zero-tolerance.** Every threshold, catalog, enum, vocabulary and version string lives
   in `app/core/config/*.py`. Never inline a literal in domain, worker, or API code.
2. **Grep before you add.** Reuse the shared `TaskQueue[T]`, the integrations grant/artifact
   machinery, `encrypt_secret`, the completeness matrix, the canonical-identity page join, the
   content layer. Do not build a second queue, crypto path, or fetch pipeline.
3. **Immutable artifacts, single writer.** Derived rows are written once by the claiming worker.
   Re-syncs bump `resync_seq` and write new rows. Analyzer v2 writes **new** rows; it never edits
   v1 rows.
4. **Provenance + versions on every derived row.** `source_artifact_id` / source-row-id lists,
   plus the analyzer / rule / formula / importer / sanitize version that produced it.
5. **Workspace auth on every query.** The products surface uses `require_active_workspace`; other
   surfaces use `require_workspace_member`. Follow the owning surface's existing convention.
   Project binding for synced shops resolves via `IntegrationPropertyMapping`, never client input.
6. **Secrets and PII.** OAuth tokens are Fernet-encrypted on grants, never in DTOs or logs. Order
   payloads are sanitized **before** the immutable write — no customer PII column may exist.
7. **Reports are projections.** Every endpoint reads persisted rows. Nothing re-calls a provider
   at read time.
8. **Postgres queue leasing.** Commit-claim-before-I/O, heartbeat, sweeper, unique idempotency
   keys, cooperative cancel.
9. **Determinism.** Headline metrics are deterministic config-defined functions of persisted rows.
   No LLM in detection, ranking, or any headline metric. Catalog and prompts are frozen into
   `Audit.configuration` at audit creation; re-scoring reads the frozen copy, never live rows.
10. **Logical vs transport identity.** Measurement executions keep the strict approved-route
    triple. `APPROVED_ROUTES` is not extended by this work.
11. **Greenfield migrations.** Until production, schema changes are made by editing the models and
    recreating the DB. No Alembic revision files.
12. **Same-origin `/api/*`.** All browser calls go through the Next.js `rewrites()` proxy.

---

## 3. What already exists (do not rebuild)

### 3.1 M1 — shipped product surface

A two-tab `/products` workspace: **Catalog** (product + competitor-product CRUD, CSV import,
per-SKU completeness badges) and **Visibility** (share-of-voice, mentions, rank distribution,
price accuracy, engine-sliceable), plus a `/products/[productId]` drill-down with mention evidence
and CSV export.

| Concern | Location |
|---|---|
| Models | [`models/product.py`](../../backend/app/models/product.py) — `Product`, `CompetitorProduct`, `ProductResponseAnalysis`, `ProductMention`, `ProductMetricSnapshot` |
| Config | [`config/products.py`](../../backend/app/core/config/products.py) — versions, price tolerances, rank buckets, completeness matrix, evidence bounds, import cap |
| Scorer | [`analysis/product_scoring.py`](../../backend/app/analysis/product_scoring.py) — `ProductScoringConfig.from_project`, `score_product_execution`, `aggregate_product_run`, plus reusable helpers `extract_price_mentions`, `price_matches_catalog`, `detect_product_rank`, `_rank_bucket` |
| Persistence | [`analysis/product_service.py`](../../backend/app/analysis/product_service.py) — `build_product_scoring_config`, `analyze_task_products`, `finalize_audit_product_analysis` |
| Catalog freeze | [`domain/products/shim.py`](../../backend/app/domain/products/shim.py) — `project_product_identity(project)` |
| Projections | [`domain/products/visibility.py`](../../backend/app/domain/products/visibility.py) — `get_product_visibility`, `get_product_evidence`, `product_visibility_csv` |
| API | [`api/products.py`](../../backend/app/api/products.py) — `product_visibility_endpoint`, `product_evidence_endpoint`, `product_visibility_export_endpoint` |
| Worker hook | [`workers/audit_worker.py`](../../backend/app/workers/audit_worker.py) — the sibling product pass runs right after `analyze_task` on every successful task |

The product analyzer is a **deterministic sibling pass**: it reads the same immutable
`RawResponseArtifact` the brand scorer reads, and writes its own rows stamped with
`PRODUCT_ANALYZER_VERSION` / `PRODUCT_SCORING_RULE_VERSION`. It never touches brand rows.

### 3.2 Machinery M2–M5 reuses

| Machinery | Location | Used by |
|---|---|---|
| AI-referral classification + rule tables | [`config/analytics.py`](../../backend/app/core/config/analytics.py) (`AI_REFERRAL_HOST_RULES`, `AI_REFERRAL_UTM_RULES`, `AI_SOURCES`, `CONFIDENCE_EXACT/HEURISTIC`), `domain/analytics/classification.py` (`classify_referral_signals`) | M5 A1 + A2 |
| Analytics task queue + executor dispatch | `AnalyticsTask`, `EXECUTORS` dict in [`workers/analytics_worker.py`](../../backend/app/workers/analytics_worker.py), kinds in `config/analytics.py` | M5 tasks |
| Snapshot upsert pattern | `domain/analytics/snapshot.py` (`refresh_analytics_snapshot`), `domain/traffic/projection.py` | M5 snapshots |
| Integrations: OAuth, grants, sync runs, artifacts, `resync_seq` derivation | `domain/integrations/*`, `connectors/integrations/*`, `workers/integration_worker.py`, `integration_dispatcher.py` | M4 |
| Dataset templates | `IntegrationDatasetTemplate(dataset, provider, api_method, dimensions, metrics)` + `INTEGRATION_DATASET_TEMPLATES` in [`config/integrations.py`](../../backend/app/core/config/integrations.py) | M4, M5 A1 |
| Sanitize-before-write contract | `domain/analytics/sanitize.py`, `ReferralEvent` | M4 orders |
| Page join | `canonical_identity` at [`domain/site_health/normalization.py:27`](../../backend/app/domain/site_health/normalization.py#L27) | M3 PDP rules |
| Owned-domain registry | `OwnedDomain` at [`models/brand.py:234`](../../backend/app/models/brand.py#L234) | M2a destination classification |
| BYOK content generation | `POST /content/generations`, `ContentGeneration` / `ContentGenerationAttempt` in [`models/content.py`](../../backend/app/models/content.py) | M3 drafts |

---

## 4. Pinned architecture decisions

These are settled. Do not re-derive them; the rationale is given so you know when a decision would
genuinely need revisiting.

**D1 — Analyzer v2 writes new rows, never migrates old ones.** Bump
`PRODUCT_ANALYZER_VERSION` → `product-analysis-2` and `PRODUCT_SCORING_RULE_VERSION` →
`product-scoring-v2`. Historical audits keep v1 rows forever. Projections must handle mixed
versions (§5.2).

**D2 — Probes run as ordinary audit executions on a new slot dimension**, not as a parallel task
family. This reuses the whole claim → provider-call → immutable-artifact → analyze pipeline. The
cost is a change to the `AuditTask` unique slot constraint, whose full blast radius is in §7.

**D3 — Merchants are observed entities, not catalog rows.** There is no merchant CRUD table.
Identity is snapshotted onto each derived row, mirroring how `ProductMention.matched_name` survives
a catalog delete.

**D4 — M3 builds the Opportunities engine, not just the rules.** The engine described in
[`../roadmap/opportunities.md`](../roadmap/opportunities.md) does not exist. Building product rules
against a non-existent engine is impossible, and a throwaway product-only subset would have to be
retrofitted later. M3 delivers the engine core with commerce rules as its first rule family.

**D5 — Attribution never reconstructs sessions.** GA4 and GSC expose **aggregate, date-grained**
reports (see the dataset templates in `config/integrations.py`: `sessionSource` × `date` with
count metrics). There is no session identity in the system —
[`ingest.py:120`](../../backend/app/domain/analytics/ingest.py#L120) passes `session_id=None`, so
every `ReferralEvent.session_id_hash` is the empty string, and the column exists only for a future
server-log source. **Any design that joins an order to a session will silently match everything to
everything.** M5 instead uses the two paths where a real deterministic key exists (§9). Searchify
also does not add client-side tracking to manufacture session identity.

**D6 — Deterministic, statistical, and LLM outputs are three separate classes**, stored in
separate namespaces and rendered with different UI treatments. A rule-based allocation model is
*not* deterministic just because its rules live in config; if it estimates rather than joins, it
belongs in `metrics.statistical.*`.

**D7 — Sync never destroys user data.** Feed sync adopts and updates platform-owned fields but
never deletes catalog rows and never overwrites hand-curated matching input (§8.2).

---

## 5. M2a — Analyzer v2: the per-SKU metric set

Owner: `analysis/product_scoring.py` (scoring) and `product_service.py` (persistence and
aggregation). New config module: `app/core/config/commerce.py`.

### 5.1 Win rate

Per SKU, over the audit's executions:

```
ranked_executions(sku) = executions where sku has a ProductMention with rank_position != null
wins(sku)              = executions where sku has a ProductMention with rank_position == 1
win_rate(sku)          = wins(sku) / ranked_executions(sku)     # null when denominator == 0
```

The denominator is **executions where this SKU was ranked** — not all executions containing an
enumeration. An execution that enumerates competitors without mentioning the SKU is not a loss;
it is invisible to win rate, and is instead captured by mention rate / SOV (already shipped) and by
the `product_not_listed` opportunity in M3.

`win_rate = null` renders as "—", never `0.0`. A response with no detected enumeration produces no
`rank_position` under the shipped `detect_product_rank`, so it lands in neither numerator nor
denominator.

Config knob in `config/commerce.py`: `PRODUCT_WIN_REQUIRES_ENUMERATION` (default `True`).

Applies to competitor SKUs identically.

### 5.2 Price relation (and the mixed-version read rule)

Add `ProductMention.price_relation` (`String(16)`, nullable): `match | higher | lower`, `null`
when unverifiable (no catalog price, or currency mismatch). Computed from the existing
`PRODUCT_PRICE_TOLERANCE_PCT` / `PRODUCT_PRICE_TOLERANCE_ABS` — reuse `price_matches_catalog` for
the tolerance test and add the direction.

**v2 also keeps writing `price_matches_catalog`** (derivable: `match` → `True`, `higher|lower` →
`False`, `null` → `null`), so every existing read path keeps working untouched.

**Read rule for audits with mixed v1/v2 rows:** the projection reads `price_relation` when
non-null; otherwise it falls back to the bool rendered as `match` / `mismatch`. Every response
carries `product_analyzer_version` so the UI labels a v1 slice "direction unavailable" rather than
inventing a direction.

### 5.3 Attribute dimensions

`config/commerce.py` owns the catalog:

```python
@dataclass(frozen=True)
class AttributeDimension:
    key: str            # "battery_life"
    group: str          # characteristics | facts | ratings
    phrases: tuple[str, ...]   # casefolded match literals + synonyms

ATTRIBUTE_DIMENSIONS: Final[dict[str, tuple[AttributeDimension, ...]]]  # keyed by product category
ATTRIBUTE_DIMENSION_GROUPS: Final[frozenset[str]]
PRODUCT_ATTRIBUTE_WINDOW_CHARS: Final = 200
```

Extraction runs in a character window around each product mention's offset, reusing the same
windowing approach as `extract_price_mentions`. Category comes from the frozen catalog entry's
`attributes["category"]`; a SKU with no category uses a `DEFAULT` dimension set.

Persisted as `ProductMention.attribute_mentions` — a JSONB list of
`{dimension, group, text, offset}`. Aggregated into the snapshot as frequency counts per
group/dimension.

**Frequency only.** Per-dimension valence is not deterministic and is deferred to the sentiment
layer (§10). Never present an attribute as positive or negative here.

### 5.4 Merchant presence and buyer destination

Extract URLs from the answer text near product mentions and classify each deterministically:

| Class | Source of truth |
|---|---|
| `brand_site` | matches an `OwnedDomain` row for the project |
| `marketplace` / `retailer` | matches `MERCHANT_DOMAINS` in `config/commerce.py` |
| `other` | anything else |

Use the suffix-safe host comparison already used by referral classification
(`analysis/normalization.domain_matches`) so `notamazon.com` never matches `amazon.com`. Sanitize
the URL before persisting (strip fragments, credentials, non-allowlisted query params) — reuse the
sanitizer in `domain/analytics/sanitize.py`.

Writes one `MerchantMention` row per observation (§5.6). Aggregated into
`metrics["buyer_destination_mix"]`.

### 5.5 Competitor co-placement

Per-SKU co-occurrence with competitor products, aggregated from per-execution `ProductMention`
sets into `metrics["competitor_co_placement"]`.

This is O(mentions²) per execution. Bound it with `CO_PLACEMENT_MAX_PAIRS` in `config/commerce.py`
and record `{"truncated": true}` in the metrics dict when the bound is hit — never silently drop.

### 5.6 Schema delta

```
ProductMention          + price_relation       String(16) null      # match|higher|lower
                        + attribute_mentions   JSONB list           # [{dimension, group, text, offset}]

ProductMetricSnapshot   + win_rate             Float null
                        + price_mismatch_rate  Float null
                        ~ metrics              new keys: win_rate, price_relation_counts,
                                               attribute_dimension_frequency,
                                               buyer_destination_mix, competitor_co_placement

ProductResponseAnalysis + shopping_surface     String(32) default ""

AuditTask               + shopping_surface     String(32) default ""
                        ~ uq_audit_task_slot -> (audit_id, prompt_index, repetition,
                                                 logical_engine, shopping_surface)

Audit.configuration     + shopping_surfaces[]  and fanout seed, frozen at creation

MerchantMention (new table)
  id, workspace_id, audit_id
  analysis_id            FK -> product_response_analyses.id   (CASCADE)
  artifact_id            FK -> raw_response_artifacts.id      (SET NULL — provenance)
  product_id             FK -> products.id                    (SET NULL)
  competitor_product_id  FK -> competitor_products.id         (SET NULL)
                         # exactly one of the two set at write time; no CHECK constraint,
                         # because a catalog delete legitimately SET NULLs it
  merchant_name          String(255)
  merchant_domain        String(255)
  merchant_kind          String(16)    # marketplace|retailer|brand_site|other (config enum)
  destination_url        Text          # sanitized
  price_text, price_value Numeric(12,2), price_currency String(3)
  product_analyzer_version String(32)
  created_at
```

`MerchantMention` needs no unique constraint: it hangs off `analysis_id`, and
`uq_product_response_analysis_task` already guarantees one analysis per execution, so the analyzer
is a single writer and cascade delete handles replacement.

**Both snapshot partial unique indexes carry the new columns** — every M2a metric is computed for
competitor SKUs wherever the mention evidence exists.

### 5.7 API delta

Extend, do not add routes:

- `GET /projects/{id}/products/visibility` — new metric fields; add `?surface=` alongside the
  shipped `?engine=`.
- `GET /products/{id}/evidence` — attribute mentions and destination URLs as new evidence kinds.
- `GET /projects/{id}/products/visibility/export.csv` — new columns.

Every response carries `product_analyzer_version` (§5.2).

---

## 6. M2b — Shopping-intent prompt fanout

`config/commerce.py` owns `SHOPPING_PROMPT_TEMPLATES`, each with `intent=purchase|comparison`,
seeded from catalog categories and attributes. Generated prompts become ordinary `Prompt` rows in
the shipped library and inherit the shipped `proposed | active | archived` review lifecycle, so
nothing runs until a human accepts it. Optionally route generation through the shipped
prompt-generation surface.

**Budget guard — required, not optional.** Fanout multiplies `prompts × engines × repetitions`
against paid provider APIs, and without a guard a catalog import can silently make the next audit
cost an order of magnitude more than the last.

```python
SHOPPING_FANOUT_MAX_PROMPTS_PER_GENERATION: Final = 25
AUDIT_MAX_EXECUTIONS_SOFT: Final = 500
```

Audit creation computes the planned execution count, returns **422 with the computed count** when
it exceeds `AUDIT_MAX_EXECUTIONS_SOFT`, and returns `planned_execution_count` in the success
response so the UI can show cost before the user commits.

---

## 7. M2c — Shopping-surface probes (gated)

Direct probing of AI shopping surfaces is the one place this suite extends beyond the three
approved answer-engine transports. It is deliberately gated.

**Rules.** Config-gated off by default, in a **separate** `SHOPPING_SURFACE_PROBES` catalog in
[`config/provider_catalog.py`](../../backend/app/core/config/provider_catalog.py) —
`APPROVED_ROUTES` is not touched. A probe ships **only where an official API exists**; no scraping.
Probe responses persist as the same immutable raw artifacts and are scored by the same
deterministic analyzer. Surfaces without an official API are covered indirectly through referral
classification, never probed.

**Reality check:** as of this writing no major AI shopping surface (ChatGPT Shopping, Perplexity
Shopping, Amazon Rufus, Google AI Mode) offers a sanctioned shopping-results API.
**Build the gate, not the connectors.** M2c is expected to stay empty until that changes, and
nothing else in this plan depends on it.

**Execution model (D2).** A probe is an ordinary audit execution with `shopping_surface` set. The
slot `(audit_id, prompt_index, repetition, logical_engine, shopping_surface)` means a probe slot
never collides with the engine-API slot for the same prompt (`shopping_surface = ""`).

**Recorded identity for probe executions only:** `shopping_surface` always records the surface id;
`logical_engine` records the host engine when the surface is a mode of an audited engine
(ChatGPT Shopping → `chatgpt`), otherwise a config-owned surface label; `transport_provider` /
`transport_model` record the probe connector id and exact API model. Measurement executions
(`shopping_surface = ""`) keep the strict approved-route triple unchanged.

### 7.1 Blast radius of the slot change — check every item

The `AuditTask` column and constraint change lands in **M2a**, while probes stay disabled, so the
constraint migration happens once on a quiet path. When it lands, verify all seven:

1. **Idempotency keys** — `uq_audit_task_idempotency_key` is a separate constraint; the key builder
   must incorporate `shopping_surface` or two surfaces of one slot collide.
2. **Brand analyzer pass** — `audit_worker.py` must skip `shopping_surface != ""` tasks when
   writing `ResponseAnalysis` / `MetricSnapshot`, or probe responses contaminate brand headlines.
3. **Per-engine denominators** — brand metrics divide by execution counts per `logical_engine`, and
   a ChatGPT-Shopping probe records `logical_engine = "chatgpt"`. Every count over `audit_tasks`
   must filter `shopping_surface = ""`. **This is the easiest place to introduce a silent metric
   regression — grep them all.**
4. **Progress and completion** — audit finalize and progress percentages count tasks.
5. **Engine freeze** — `AuditEngineSnapshot` is unique on `(audit_id, logical_engine)`
   ([`models/audit.py:188`](../../backend/app/models/audit.py#L188)) and cannot express a
   per-surface freeze. Add a sibling `AuditShoppingSurfaceSnapshot` keyed
   `(audit_id, shopping_surface)`. Do **not** widen the engine snapshot.
6. **Executions API** — [`api/executions.py`](../../backend/app/api/executions.py) exposes and
   filters `shopping_surface`, defaulting to measurement-only.
7. **Product projections** — `?surface=` slicing reads
   `ProductResponseAnalysis.shopping_surface`.

---

## 8. M3 — Opportunities engine + commerce rule catalog

Per D4, M3 builds the engine described in [`../roadmap/opportunities.md`](../roadmap/opportunities.md)
and lands commerce rules as its first family. Read that spec for the engine's full design; this
section pins what it needs for commerce plus the deltas.

### 8.1 Engine core

- **`Opportunity`** — `id`, `workspace_id`, `project_id`, `rule_id` (validated versioned string,
  checked against the config catalog on write — not a FK), `opportunity_type`, `severity`,
  `priority_score`, `title`, target columns, `evidence` (JSONB: offending values + source row ids),
  provenance (`source_*_ids` JSONB lists, `analyzer_version`, `rule_version`, `formula_version`),
  `status` (`open|in_progress|dismissed|resolved`), timestamps. One row per (rule, target).
  `status` is the **only** mutable field.
- **`OpportunitySnapshot`** — per-run aggregate projection; immutable per run.
- **Recompute** — reads persisted rows only, evaluates enabled rules, scores each hit, writes fresh
  rows superseding prior `open` rows for the same (rule, target) by writing a new identity and
  closing the stale one. Never mutates evidence. Runs inline for small projects or as a queued task
  on the shared queue.
- **Endpoints** — list (filtered, paged, priority-sorted), detail, recompute, `PATCH` status only,
  summary, export.

**Commerce deltas:** `OPPORTUNITY_TYPES` gains `product` (the `?type=product` filter depends on
it); `Opportunity` gains nullable `target_product_id` (FK → `products.id`, SET NULL) and
`content_generation_id`.

### 8.2 Prerequisite — per-type PDP structured-data rules

Shipped Site Health has exactly one structured-data rule, `aeo.structured_data_present`
([`config/site_health.py:574`](../../backend/app/core/config/site_health.py#L574)), which fails
only when a page has **zero** recognized JSON-LD blocks. Before the schema and review rules below
can fire:

In [`config/site_health.py`](../../backend/app/core/config/site_health.py):

- `STRUCTURED_DATA_REQUIRED_PROPERTIES` (line 622) already contains
  `"Product": ("name", "offers")`. **Extend** that tuple as the rules require, and **add** the two
  missing types: `"Offer": ("price", "priceCurrency", "availability")` and
  `"AggregateRating": ("ratingValue", "reviewCount")`.
- Add three deterministic `SiteIssue` rules: `pdp.product_schema_missing`,
  `pdp.offer_schema_missing`, `pdp.aggregate_rating_missing` — evaluated only on pages classified
  as product detail pages (a `Product` JSON-LD block is present, **or** the URL matches a catalog
  `Product.url`).

**Verify before writing the rules:** confirm the extractor retains `Offer` and `AggregateRating`
when they are **nested inside** `Product`, which is where they normally appear. If it only walks
top-level `@type`, these rules will false-positive on correct markup. This is the single most
likely source of bogus M3 findings.

### 8.3 Commerce rule catalog

| Lever | `rule_id` | Persisted evidence the predicate reads |
|---|---|---|
| Semantic relevance | `thin_product_content` | Completeness-matrix gaps (missing/short `description`, missing use-case attributes) from `PRODUCT_REQUIRED_ATTRIBUTES` / `PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS` |
| Semantic relevance | `product_not_listed` | Shopping prompt where competitor products are mentioned but no own SKU is — `ProductResponseAnalysis` + `ProductMention` |
| Structured data | `missing_product_schema` | `pdp.product_schema_missing` / `pdp.offer_schema_missing` on the SKU's PDP — `Product.url` joined to `SiteUrl` via `canonical_identity` |
| Review signals | `missing_review_signals` | `pdp.aggregate_rating_missing` **and** no third-party review-platform domain among the execution's `Citation` rows (review-domain catalog in config) |
| Price accuracy | `price_mismatch` | `ProductMention.price_relation` = `higher\|lower` rate above a config threshold |
| Availability / freshness | `stale_catalog_data` | Catalog vs synced-feed divergence, or missing availability/GTIN in the feed (**requires M4**) |
| Platform eligibility | `ai_channel_ineligible` | The platform's own AI-eligibility verdict (Shopify Agentic Commerce Dashboard, GMC item status) says the SKU cannot be recommended (**requires M4**) |
| Third-party authority | `authority_gap` | Competitor-cited listicle/comparison/forum/award domains where the brand's SKUs are absent — shared `Citation` evidence |
| Entity consistency | `entity_inconsistency` | Name/alias/URL/price mismatches across catalog rows, feed rows, and PDP structured data (**requires M4**) |

`ai_channel_ineligible` is the highest-signal rule in the catalog when present, because it is
ground truth rather than inference.

**Rules whose evidence source is absent must emit nothing.** M3 ships before M4 and must degrade
cleanly: never emit a low-confidence guess in place of missing evidence.

**Intent-conditional weighting is a scoring feature, not a rule.** `value_factor` in the priority
formula becomes intent-conditional (budget-intent prompts weigh price rules; quality-intent prompts
weigh review and authority rules), with the weight table in `config/opportunities.py`, stamped via
`formula_version`.

### 8.4 BYOK content drafts

A `thin_product_content` opportunity may request a product-description or PDP-FAQ draft. Post the
generation through the **shipped** `/content` generations surface (`POST /content/generations`)
with the opportunity's deterministic evidence as prompt input, linking back via
`content_generation_id`. The draft is reviewed in the shipped `/content` flow and exported as an
approve-to-copy artifact.

This is a generation layer **over** deterministic diagnosis. The draft never affects detection,
ranking, or any headline metric, and there is no deploy machinery (§11).

---

## 9. M4 — Commerce integrations: catalog, orders, feed health

Built entirely on the shipped integrations framework — OAuth grants, Fernet-encrypted tokens,
`IntegrationSyncRun` on the shared queue, immutable `IntegrationImportArtifact` → derived rows with
`resync_seq`, sync worker plus cadence dispatcher. **Read scopes only.**

### 9.1 Providers

Extend the vocabularies in [`config/integrations.py`](../../backend/app/core/config/integrations.py)
(`INTEGRATION_PROVIDERS`, `INTEGRATION_TRANSPORTS`, `INTEGRATION_PROVIDER_TRANSPORT`,
`INTEGRATION_DATASET_TEMPLATES`) and add connectors under `connectors/integrations/`, alongside the
shipped `gsc.py` / `ga4.py` / `bing.py`:

| Provider | Transport | Datasets |
|---|---|---|
| `shopify` | `shopify_oauth` (new) | `shopify.products`, `shopify.orders` |
| `bigcommerce` | `bigcommerce_oauth` (new) | `bigcommerce.products`, `bigcommerce.orders` |
| `gmc` | existing `google_oauth`, own scope set | `gmc.items` (Content API item status + diagnostics) |

The OAuth, grant, and sync machinery is reused unchanged. Ship **Shopify first**; BigCommerce and
GMC follow behind the same contract.

### 9.2 Catalog sync and the merge policy

A feed import lands as an immutable `IntegrationImportArtifact`; a deterministic derivation pass
upserts `Product` rows keyed by the existing `uq_product_project_sku`.

New `Product` columns: `connection_id` (FK → `integration_connections.id`, SET NULL),
`external_item_ref` (`String(255)`), `last_seen_sync_run_id` (FK → `integration_sync_runs.id`,
SET NULL). `PRODUCT_ORIGINS` gains `synced`; CSV import keeps `imported`; manual CSV import remains
as the no-integration path.

**Merge policy (D7):**

| Situation | Behaviour |
|---|---|
| SKU exists, `origin=manual`, no `connection_id` | **Adopt.** Set `origin=synced` + provenance. Overwrite platform-owned fields (`name`, `price`, `currency`, `url`, platform attributes). **Preserve** `aliases` and any `attributes` key absent from the feed. |
| SKU exists, `origin=synced`, same connection | Normal update of platform-owned fields. |
| SKU exists, `origin=synced`, **different** connection | Do not steal. Leave the row; emit `FeedIssue` `feed.duplicate_sku_across_connections`. |
| SKU in catalog, absent from feed | **Never delete.** Stamp staleness via `last_seen_sync_run_id`; let `stale_catalog_data` fire. Deletion is a user action. |

`aliases` is hand-curated matching input that drives the analyzer — a feed silently overwriting it
would degrade measurement invisibly. Audits freeze the catalog at creation regardless, so
determinism is unaffected either way.

### 9.3 Feed and eligibility health

Deterministic validators over persisted feed artifacts: missing GTIN/MPN, missing availability,
price divergence from catalog, GMC-reported item issues, and the platform's AI-eligibility verdict.

```
FeedIssue (new)
  id, workspace_id, project_id, connection_id, sync_run_id
  external_item_ref  String(255)
  product_id         FK -> products.id (SET NULL)
  rule_id            String   # validated versioned string from the feed-rule catalog
  severity           String
  evidence           JSONB
  source_artifact_id, importer_version, created_at
```

Projection-only. No feed is ever modified.

Note on positioning: Shopify ships a native Agentic Commerce Dashboard giving merchants AI-channel
visibility and per-product eligibility for free, so standalone feed diagnostics are commoditized
for Shopify merchants. Scope this work as an **input to cross-surface diagnosis** (M3), not as a
selling surface in its own right. Reading a platform's eligibility verdict is ordinary read-only
diagnostics of the same kind as GMC item issues, and does not breach the no-checkout non-goal.

### 9.4 `OrderFact` — the attribution input

Shopify orders and GA4 ecommerce events land as artifacts and derive into `OrderFact` rows,
sanitized before persistence exactly like `ReferralEvent`.

```
OrderFact (new)
  id, workspace_id, project_id, connection_id, provider
  order_ref_hash     String(64)   # opaque salted hash — never the merchant order number
  resync_seq         Integer
  occurred_at, currency, total_amount Numeric(12,2)
  line_items         JSONB   # [{sku, product_id, quantity, unit_price}]
                             # product_id resolved deterministically by SKU; null when unresolved
  attribution_keys   JSONB   # allowlisted utm_*, sanitized landing_url, sanitized referrer
                             # — nothing else
  source_artifact_id, importer_version, order_sanitize_version, created_at

  UNIQUE (connection_id, order_ref_hash, resync_seq)
```

**The unique key must include `resync_seq`.** Orders mutate — refunds, cancellations, fulfilment
changes — and an immutable row cannot absorb that. Each re-sync writes a new immutable row, and
**every projection reads the max `resync_seq` per `(connection_id, order_ref_hash)`**, exactly as
the shipped `IntegrationMetricRow` path does. A two-column unique key would make re-sync fail.

**No customer PII columns exist.** Sanitization runs before the immutable write. Retention via
`ORDER_RETENTION_DAYS` with a sweeper, mirroring the referral retention sweep. Shop → project
binding reuses `IntegrationPropertyMapping`.

---

## 10. M5 — AI attribution & revenue impact

Extends the shipped LLM Analytics surface from sessions to revenue.

### 10.1 Layer A1 — platform-attributed (deterministic, aggregate, surface-level)

Add two GA4 dataset templates to `INTEGRATION_DATASET_TEMPLATES`:

```python
DATASET_GA4_ECOMMERCE_SOURCE_MEDIUM_DAILY   # dims (sessionSource, sessionMedium, date)
                                            # metrics (transactions, purchaseRevenue, sessions)
DATASET_GA4_ITEM_SOURCE_MEDIUM_DAILY        # dims (itemId, sessionSource, sessionMedium, date)
                                            # metrics (itemRevenue, itemsPurchased)
```

GA4 has already performed the session → purchase attribution. Searchify reads the attributed
aggregate and classifies `(sessionSource, sessionMedium)` with the **shipped** rule table via
`classify_referral_signals`, stamping the same `rule_version`. No join, no session identity, no PII.

Yields revenue, orders, AOV and conversion rate by `ai_source`, plus per-SKU revenue by `ai_source`
via `itemId` → `Product.sku`.

**This layer needs only the shipped GA4 integration — it ships without M4.**

**Verify at build:** GA4 restricts some item-scoped × session-scoped dimension combinations. If
`itemId × sessionSource` is rejected, fall back to `itemId × sessionDefaultChannelGroup` (coarser
but permitted) and surface the reduced granularity in the DTO. Do not silently degrade.

### 10.2 Layer A2 — order-level referrer (deterministic, per-order, per-SKU)

Shopify persists `landing_site` and `referring_site` (plus UTM parameters) **on the order itself**.
Classify `OrderFact.attribution_keys` with the same shipped rule table and `rule_version` →
`AttributionLink(method=order_referrer, confidence=exact)`. Per-SKU revenue comes from
`OrderFact.line_items` resolved to `product_id`.

This is genuinely deterministic, order-grained, and needs no session identity. It is the real
per-SKU revenue path.

**A1 and A2 are cross-checks, not duplicates. Never sum them.** A1 covers all traffic including
non-Shopify merchants and reflects GA4's own attribution model; A2 is order-grained truth for
Shopify. They will disagree — attribution windows, GA4 modelling, ad blockers. Render both with
their method labelled and expose the delta; the disagreement is itself a useful signal, and
silently picking one would be an invented number.

### 10.3 Layer B — unattributed

Orders with no referrer evidence cannot be matched to a specific AI touch, because no shared key
exists (D5). **Report them as `unattributed`.** Any allocation across sources is a model, not a
join: if one is offered at all, it renders only under `metrics.statistical.*` with an explicit
label, never as a headline and never merged into a deterministic total. Ambiguity is surfaced, not
guessed.

### 10.4 Layer C — incrementality (statistical, labelled)

Pre/post designs only. Holdout-geo is **out of scope**: there is no experiment infrastructure and
the aggregate GA4 templates carry no geo dimension, so such a design could only ever be simulated.

Three mandatory guards:

1. A minimum sample threshold, below which the row is written with `guard=insufficient_data` and
   **no effect number**.
2. A mandatory confound label — pre/post over observational data cannot separate an AI-visibility
   change from seasonality, promotions, or price moves.
3. Rendered exclusively under `metrics.statistical.*`, visually separated from deterministic
   numbers.

### 10.5 Schema

```
AttributionLink (new)
  id, workspace_id, project_id
  order_fact_id      FK -> order_facts.id
  method             String(24)   # order_referrer | ga4_platform_attributed
  confidence         String(16)   # exact | heuristic
  matched_rule_id, rule_version, analyzer_version
  evidence_refs      JSONB        # the classification / metric-row ids joined
  revenue_amount     Numeric(12,2), currency String(3)
  created_at
  UNIQUE (order_fact_id, matched_rule_id, rule_version)   # same-version re-run is idempotent;
                                                          # a rule bump writes new rows

AttributionSnapshot (new)   # unique-tuple upsert, exactly like TrafficSnapshot
  (project_id, window_start, window_end, granularity)
  metrics JSONB:
    deterministic.*   revenue / orders / AOV / conversion rate by ai_source, by surface,
                      by product_id; funnel counts; A1-vs-A2 delta
    statistical.*     allocation estimates and lift — separate namespace, separate UI treatment
  source_link_ids / source_order_fact_ids / source_snapshot_ids, formula_version

LiftEstimate (new)
  id, workspace_id, project_id
  design             String(16)   # pre_post
  window_start, window_end, metric
  effect_estimate    Float null, confidence_interval JSONB
  sample_size        Integer
  guard              String(16)   # ok | insufficient_data
  lift_method_version, evidence_refs JSONB, created_at
```

### 10.6 Wiring

New task kinds in `config/analytics.py` (`attribution_link`, `attribution_snapshot`), registered in
the `EXECUTORS` dispatch table in `workers/analytics_worker.py` as siblings of `classify_referrals`.
Domain code in `domain/attribution/{link,snapshot,lift}.py`, beside `domain/analytics/*`.

Endpoints: `GET /projects/{id}/commerce/attribution?from=&to=&granularity=`,
`…/attribution/orders`, `…/attribution/lift`.

New config module `app/core/config/attribution.py`: method enum, confidence buckets,
`formula_version`, `LIFT_METHOD_VERSION`, minimum-sample guard.

---

## 11. Frontend

Grow `/products` into the commerce workspace; do not add a parallel route. (Renaming the nav item
to "Commerce" is a one-line `nav-items.ts` edit, not an architecture decision.)

- **Catalog** (shipped) — add feed-origin badges, per-SKU feed-health status, bound-connection sync
  state.
- **Visibility** (shipped) — add win-rate column, price-relation badges, attribute-dimension
  frequency panel, buyer-destination breakdown, competitor co-placement matrix; engine **and**
  surface slicing. The `/products/[productId]` drill-down reuses the evidence-explorer pattern for
  the new evidence kinds.
- **Opportunities** (new tab, M3) — priority-sorted table, evidence drill-down, status workflow,
  and a "draft suggestion" action handing off to `/content`.
- **Attribution** (new tab, M5) — revenue by surface and SKU, conversion-rate and AOV comparisons,
  A1-vs-A2 shown side by side with methods labelled, unattributed share stated plainly, and the
  lift panel under an explicit "statistical estimate" treatment.

Contracts: extend `lib/api/products.ts`; add `lib/api/commerce.ts` and `lib/api/attribution.ts`
with zod `strictValidate` schemas (`z.string().uuid()` ids, no secret or PII fields —
`strictValidate` fails loud on a leak), `queryKeys` entries, TanStack Query polling-first,
same-origin `/api/*` only. Reuse existing table/badge/trend-chart primitives; no new design tokens.

---

## 12. Non-goals

- **No agentic checkout or ACP readiness.** No feed-compliance checks, no Instant Checkout
  eligibility reports, no Delegate Payment. Searchify never transacts and never holds payment
  credentials.
- **No feed management or write-back.** No one-click deploy to storefronts or ad feeds, no schema
  injection into live pages, no image optimization, no write-scope OAuth. Searchify diagnoses and
  drafts; humans deploy. *(Reading a platform's own eligibility verdict is read-only diagnostics
  and is in scope — acting on it automatically is not.)*
- **No new measurement transports beyond the approved three**, except M2c's gated,
  monitoring-only, official-API-only probes, which keep their transports out of `APPROVED_ROUTES`.
- **No per-session identity reconstruction** (D5). No client-side tracking is added to manufacture
  session identity.
- **No holdout-geo incrementality.**
- **No physical retail or shelf vision.**
- **No agency multi-client controls.**
- **No LLM in detection, ranking, or any headline metric**, and no heuristic sentiment presented as
  deterministic.
- **No cross-workspace identity graph and no third-party ad-data onboarding.** Attribution resolves
  identity within workspace data only.
- **No fabricated attribution.** Below sample thresholds, surfaces report `insufficient_data`.

---

## 13. Determinism boundaries

- **Deterministic (headline-safe):** all M2 scoring and extraction; all M3 detection and
  prioritization; all M4 derivation and validators; M5 layers A1 and A2.
- **LLM — two sanctioned layers only:** (1) the versioned adjudicated sentiment layer, optionally
  extended with product-mention scope for per-mention sentiment and attribute valence as flagged
  adjudicated rows — never mutating deterministic product rows, never headline; (2) the BYOK
  content-generation layer for product copy and FAQ drafts.
- **Statistical, labelled:** M5 layer B allocation and layer C lift — methodology pinned in config,
  effect reported with interval and sample guard, separate namespace, separate UI treatment.

Every derived row carries provenance plus the versions that produced it.

---

## 14. Test plan

Follow the repo convention: `backend/tests/unit/` for pure functions, `backend/tests/component/`
for API and worker paths.

| Phase | Unit | Component |
|---|---|---|
| M2a | `test_product_scoring_v2.py` — win-rate denominator including the null case and the not-mentioned case; price-relation tri-state incl. currency mismatch; attribute extraction and windowing; destination classification incl. the `notamazon.com` suffix case; co-placement truncation | `test_products_visibility_api.py` — mixed v1/v2 audit read path; `?surface=` slicing; export columns |
| M2a slot | — | `test_audit_task_slot_surface.py` — constraint shape, idempotency-key uniqueness, **brand-pass exclusion, and per-engine denominators unchanged when probe rows exist** |
| M2b | `test_shopping_fanout.py` — template seeding, generation cap | `test_audit_execution_budget.py` — 422 above `AUDIT_MAX_EXECUTIONS_SOFT`, `planned_execution_count` in the response |
| M3 | `test_opportunity_rules_product.py` — each rule fires and, critically, **emits nothing when its evidence source is absent**; `test_pdp_structured_data_rules.py` — nested `Offer` / `AggregateRating` inside `Product` do **not** false-positive | `test_opportunities_api.py` — filters, priority sort, status-only PATCH, recompute supersede-not-mutate |
| M4 | `test_feed_validators.py`; `test_order_sanitize.py` — no PII survives the write | `test_integration_shopify.py`; `test_catalog_sync_merge.py` — every row of the §9.2 table, especially alias preservation and never-delete; `test_order_resync_seq.py` — a refund re-sync writes a new row and projections read max seq |
| M5 | `test_attribution_link.py` — idempotent same-version re-run, new rows on rule bump; `test_lift_estimate_guard.py` — `insufficient_data` carries no effect number | `test_attribution_api.py` — A1 and A2 rendered separately and **never summed**; unattributed share reported; statistical namespace never merged into deterministic totals |

---

## 15. Acceptance criteria

**M2a** — A re-scored audit produces v2 rows with win rate, price relation, attribute frequencies,
destination mix, and co-placement for both own and competitor SKUs; v1 audits still render
correctly with direction labelled unavailable; `uq_audit_task_slot` includes `shopping_surface` and
all seven §7.1 items are verified; brand metrics are numerically unchanged.

**M2b** — Shopping prompts generate as `proposed`, never auto-run; an audit exceeding the execution
cap is rejected with the computed count.

**M3** — Recompute produces priority-sorted product opportunities with full evidence and
provenance; rules whose evidence source is absent emit nothing; a `thin_product_content`
opportunity can hand off to `/content` and links back by `content_generation_id`.

**M4** — A Shopify connection syncs catalog and orders into immutable artifacts and derived rows;
the merge policy behaves per §9.2 on every row of the table; a refund re-sync writes a new
`OrderFact` and projections read the latest; no PII column exists anywhere in the order path.

**M5** — Revenue by `ai_source` renders from GA4 alone (A1) before any commerce connector exists;
with Shopify connected, per-SKU order-level revenue renders (A2) alongside A1 with both methods
labelled and the delta shown; unattributed orders are reported as such; lift estimates appear only
under the statistical treatment and report `insufficient_data` below threshold.

---

## 16. Verify at build time

1. **Nested JSON-LD extraction** (§8.2) — does the site-health extractor retain `Offer` and
   `AggregateRating` nested inside `Product`? If not, the three PDP rules false-positive on correct
   markup. Check before writing the rules.
2. **GA4 item × session dimension compatibility** (§10.1) — verify `itemId × sessionSource` is
   accepted; if not, fall back to channel group and label the coarser granularity in the DTO.
3. **Shopify order referrer coverage** (§10.2) — measure what share of real orders carry a non-empty
   `referring_site` on the first connected shop. This sets the ceiling on Layer A2 and should be
   known before the UI promises per-SKU coverage.
4. **Attribute-dimension seed catalog** (§5.3) — the per-category dimension lists are hand-authored
   config. Decide the seed categories before starting M2a; an empty catalog makes the feature
   invisible.

<!-- Commit-time: add a Commerce Suite row to docs/roadmap/README.md's surface-status table —
     "Partially shipped — M1 (/products) live; M2–M5 planned, see
     docs/plans/v4-commerce-suite-m2-m5.md". The roadmap README has no Commerce row today. -->

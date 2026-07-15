# Cube27 AI Visibility Audit — MVP Architecture and Migration Plan v2

**Status:** Approved architecture direction  
**Date:** 2026-07-14  
**Target:** Standalone Cube27 AEO / AI-visibility audit product  
**Frontend:** Next.js App Router on Vercel  
**Backend:** Python / FastAPI on Railway  
**Primary database and MVP job queue:** PostgreSQL on Railway  
**MVP boundary:** Migrate the existing AI-visibility audit and reporting behavior into a focused product, then add only the minimum configuration, execution, evidence inspection, and reporting surfaces required for a reliable one-time audit.

---

## 1. Executive decisions

### 1.1 Build a modular monolith

Use one Python application with explicit domain modules and a separate worker process. Do not create microservices for the MVP.

The main workflow is one coherent product transaction:

```text
brand and prompt definition
  -> audit plan
  -> provider execution
  -> raw evidence persistence
  -> response analysis
  -> metrics
  -> report
```

Separating those steps into networked services now would add deployment, consistency, tracing, and contract overhead without improving the initial customer outcome.

### 1.2 Use PostgreSQL as both durable state and the MVP task queue

The revised deployment has no Redis dependency:

```text
                         cube27.com
                             |
                    app.visibility.cube27.com
                             |
                             v
                    Next.js on Vercel
                             |
                             v
                  FastAPI API on Railway
                       |             |
                       |             +------ SSE audit events
                       |
             +---------+-------------------------+
             |                                   |
             v                                   v
      PostgreSQL on Railway          S3-compatible object storage
      - product data                 - raw provider payloads
      - audit tasks                  - generated reports
      - leases/retries               - large evidence artifacts
      - metrics/provenance
             ^
             |
     Python worker on Railway
     - provider calls
     - response analysis
     - report generation
```

PostgreSQL remains the source of truth for every audit and task. Workers claim queue rows using row-level locking with `FOR UPDATE SKIP LOCKED`, leases, and idempotency keys.

### 1.3 Treat Redis as a future scaling option, not an MVP requirement

Introduce Redis only after measurements show that PostgreSQL queue activity or distributed coordination is becoming a bottleneck.

A future Redis upgrade may provide:

- faster wake-ups and fan-out;
- distributed rate-limit counters;
- provider-specific concurrency semaphores;
- high-volume transient caching;
- event pub/sub;
- a dedicated queue implementation.

Even after Redis is added, PostgreSQL should continue to hold the canonical audit task, attempt, response, and outcome records. Redis must not become the sole durable evidence store.

### 1.4 Keep all provider configuration in Providers & Settings

Provider selection, API keys, transport routes, model IDs, connection testing, and defaults belong only in:

```text
Settings -> Providers
```

Main audit pages may display a read-only summary such as:

```text
Discovery model: OpenRouter / configured model
Measurement engines: ChatGPT, Gemini, Claude
```

They must not repeat provider cards, API-key forms, or model-selection controls. An audit may override operational values such as enabled engines, repeat count, region, concurrency, and rate limits, but it uses provider routes configured centrally.

### 1.5 Reports are projections of persisted evidence

The canonical rule carried into the new project is:

> A report renderer never performs a second extraction or silently repairs analysis. It renders versioned, persisted evidence and metrics.

---

## 2. Findings from the current CrawlerAI repository

### 2.1 Reusable engineering patterns

The audited repository already contains useful foundations:

- Python and FastAPI backend conventions.
- PostgreSQL persistence through SQLAlchemy.
- Pydantic request and response contracts.
- Thin route modules.
- encrypted LLM/API-key configuration;
- provider catalog and connection testing;
- task-specific model settings;
- run configuration snapshots;
- cost logging;
- structured logs and diagnostics;
- immutable artifacts plus canonical derived records;
- report/export behavior downstream from persisted data;
- an explicit frontend API-contract layer;
- typed UI primitives and semantic design tokens.

These patterns should be migrated selectively. The new product should not copy commerce crawling, selector management, domain memory, or acquisition complexity that is unrelated to AI visibility.

### 2.2 Blocking source mismatch

The audited default branch does not expose an identifiable first-class AI-visibility subsystem in its documented frontend routes, backend route map, ORM map, or searchable module ownership.

The currently visible product is primarily a deterministic commerce/jobs crawler with Crawl Studio, run history, product intelligence, enrichment, domain memory, and administrator LLM configuration.

Before file-level migration starts, locate the exact implementation of the existing AI-visibility audit:

1. another branch;
2. an older commit;
3. an unpushed local workspace;
4. a separate repository; or
5. generated source/artifacts outside the audited branch.

The new architecture can be implemented without that source, but “keep the audit exactly as it is” cannot be verified until its revision and golden outputs are frozen.

### 2.3 Provider layer migration

The current provider layer is a useful starting point for encrypted configuration and broad discovery-model support, but the new product needs two distinct concepts.

#### Measurement engines

The products being measured:

- ChatGPT / OpenAI;
- Gemini / Google;
- Claude / Anthropic.

Each logical engine can use:

- its direct provider API; or
- an OpenRouter transport route.

#### Discovery and analysis model

A separately configured model used for brand understanding, prompt suggestion, clustering, and optional ambiguity adjudication. Supported transports may include:

- OpenRouter;
- NVIDIA;
- Mistral API;
- OpenAI;
- Anthropic;
- Google;
- Groq;
- other explicitly approved OpenAI-compatible endpoints.

A result must always preserve both identities:

```text
logical_engine = gemini
transport_provider = openrouter
transport_model = google/<exact-model-id>
```

---

## 3. MVP scope

### 3.1 Included

- user authentication;
- workspace and project creation;
- one brand per initial project;
- brand name, website, market, language, aliases, and competitors;
- optional website evidence collection;
- optional AI-assisted brand analysis;
- user-controlled number of generated prompts;
- manual prompt creation;
- CSV prompt import and export;
- prompt review, editing, grouping, filtering, enable/disable, and deletion;
- centralized BYOK provider settings;
- direct and OpenRouter routes for ChatGPT, Gemini, and Claude;
- separate discovery/analysis-model configuration;
- one-time audit execution;
- enabled-engine selection per audit;
- configurable repeat count;
- configurable global and per-provider concurrency;
- configurable request-rate limits;
- bounded retries;
- cancellation;
- raw request/response evidence preservation;
- partial completion when one provider fails;
- deterministic brand/competitor mention detection;
- citation extraction and URL normalization;
- owned-domain citation classification;
- ordered recommendation/rank detection;
- sentiment and theme analysis;
- accuracy findings tied to brand evidence;
- aggregate metrics and engine comparison;
- prompt-level evidence explorer;
- HTML, Markdown, CSV, and JSON exports;
- cost, usage, failure, and diagnostic summaries;
- audit and analyzer version provenance.

### 3.2 Deferred

- recurring schedules;
- consumer-chat browser automation;
- prompt-volume datasets;
- large-scale country/language matrices;
- GSC and GA4 integration;
- server/edge-log crawler analytics;
- automated content generation or publishing;
- SSO and advanced enterprise roles;
- subscriptions and metered billing;
- a public benchmark network;
- Redis-backed queueing or pub/sub.

The database may include nullable scheduling fields, but the MVP UI must present only one-time audits.

---

## 4. Product workflow

### 4.1 Project setup

```text
Create project
  -> add brand URL and market
  -> optionally define aliases and competitors
  -> verify provider settings
```

### 4.2 Prompt path A: assisted discovery

```text
Fetch allowed brand evidence
  -> create BrandEvidenceSnapshot
  -> run configured discovery model
  -> suggest requested number of prompts
  -> classify and cluster prompts
  -> user reviews and edits
```

### 4.3 Prompt path B: no discovery AI

```text
Manual prompt entry or CSV import
  -> validation and dedupe
  -> optional manual metadata
  -> user reviews
```

The product must never require discovery AI to run an audit.

### 4.4 Audit execution

```text
Estimate calls and cost
  -> snapshot prompt and provider configuration
  -> create AuditTask rows
  -> worker claims tasks
  -> execute provider calls
  -> persist raw artifacts and attempts
  -> deterministic analysis
  -> optional ambiguity adjudication
  -> aggregate metrics
  -> generate report
```

---

## 5. Repository shape

```text
cube27-ai-visibility/
  apps/
    web/                              # Next.js App Router
  backend/
    app/
      api/                            # thin FastAPI route modules
      core/                           # config, DB, security, telemetry
      models/                         # SQLAlchemy persistence
      schemas/                        # Pydantic API contracts
      domain/
        workspaces/
        brands/
        prompts/
        providers/
        audits/
        visibility/
        citations/
        reports/
      connectors/
        answer_engines/
        discovery_models/
        web_evidence/
        object_storage/
      orchestration/
        audit_planner.py
        audit_dispatcher.py
        audit_state_machine.py
        task_queue.py
        postgres_task_queue.py
      analysis/
        deterministic.py
        entity_matching.py
        citation_parser.py
        ranking.py
        sentiment.py
        fact_check.py
        llm_adjudication.py
      reporting/
        canonical_report.py
        html_renderer.py
        markdown_renderer.py
        csv_exporter.py
        json_exporter.py
      workers/
        audit_worker.py
        analysis_worker.py
        report_worker.py
      tests/
  migrations/
  docs/
    architecture/
    product/
    runbooks/
    adr/
  infra/
    docker/
    railway/
    vercel/
```

Use one repository. Vercel can deploy `apps/web` as its root directory, while Railway deploys the API and worker from the backend directory with different start commands.

---

## 6. Domain model

### 6.1 Identity and workspaces

- `User`
- `Workspace`
- `WorkspaceMember`
- `Project`

Every project-owned query must include workspace authorization. Do not rely on IDs alone.

### 6.2 Brand evidence

- `Brand`
- `BrandAlias`
- `Competitor`
- `BrandEvidenceSnapshot`
- `BrandEvidencePage`

A snapshot records what the discovery/accuracy pipeline saw at that time:

- fetched URLs;
- page titles and canonical URLs;
- extracted text or compact evidence;
- brand claims;
- positioning;
- services/products;
- target audience;
- market;
- timestamp;
- fetch diagnostics;
- content hash;
- discovery-model snapshot.

### 6.3 Prompts

- `PromptSet`
- `Prompt`
- `PromptCluster`
- `PromptImport`

Prompt fields:

- exact text;
- language;
- region;
- persona;
- buyer stage;
- branded/non-branded;
- topic;
- cluster;
- enabled state;
- origin: generated, manual, imported;
- generation evidence;
- created/updated timestamps.

### 6.4 Provider configuration

- `ProviderConnection`
- `ProviderRoute`
- `ProviderConnectionTest`
- `ModelCatalogSnapshot`

Secrets are encrypted and never returned after creation. A route stores:

- logical engine or discovery purpose;
- transport provider;
- exact model ID;
- base URL from an approved provider definition;
- non-secret options;
- encrypted credential reference;
- active/default state.

### 6.5 Audit execution

- `Audit`
- `AuditPromptSnapshot`
- `AuditEngineSnapshot`
- `AuditTask`
- `ProviderAttempt`
- `RawResponseArtifact`
- `AuditEvent`

The atomic execution identity is:

```text
audit + prompt snapshot + logical engine + repeat index
```

### 6.6 Analysis and reports

- `ResponseAnalysis`
- `BrandMention`
- `CompetitorMention`
- `Citation`
- `Claim`
- `AccuracyFinding`
- `MetricSnapshot`
- `Report`
- `ExportArtifact`

Every derived row points to:

- raw response artifact;
- analyzer version;
- formula or rule version;
- optional adjudication artifact.

---

## 7. PostgreSQL task queue

### 7.1 Queue table

Recommended `audit_tasks` fields:

| Field | Purpose |
|---|---|
| `id` | UUID primary key |
| `audit_id` | parent audit |
| `prompt_snapshot_id` | immutable prompt input |
| `logical_engine` | ChatGPT, Gemini, or Claude |
| `provider_route_snapshot` | non-secret execution configuration |
| `repeat_index` | reproducibility repeat |
| `idempotency_key` | unique task identity |
| `status` | queued, leased, running, succeeded, retry_wait, failed, cancelled |
| `priority` | bounded integer |
| `available_at` | retry/scheduling gate |
| `lease_owner` | worker identity |
| `lease_expires_at` | crash recovery |
| `heartbeat_at` | live worker signal |
| `attempt_count` | attempts used |
| `max_attempts` | bounded retry limit |
| `result_artifact_id` | successful raw evidence |
| `error_code` | normalized error |
| `error_detail` | redacted diagnostic |
| timestamps | creation, update, start, completion |

Indexes:

- `(status, available_at, priority, created_at)`;
- `(audit_id, status)`;
- partial index on expired leases;
- unique index on `idempotency_key`.

### 7.2 Claim algorithm

Within one short transaction:

1. select eligible rows in deterministic priority order;
2. lock them with `FOR UPDATE SKIP LOCKED`;
3. update them to `leased`;
4. assign `lease_owner` and `lease_expires_at`;
5. return the claimed tasks;
6. commit before making any external API call.

Do not hold a database transaction open while waiting for a provider.

### 7.3 Lease and recovery

- Worker heartbeats extend a task lease.
- A sweeper returns expired leased/running tasks to `retry_wait` or marks them failed after `max_attempts`.
- Provider attempts are append-only.
- A successful task cannot be re-executed unless an explicit operator rerun creates a new task identity.
- Cancellation marks queued tasks immediately and causes active workers to stop before the next provider call or analysis stage.

### 7.4 Worker wake-up

For the MVP, workers can use bounded polling with jitter. A short idle delay is acceptable because provider calls are much slower than queue claims.

Optional later optimization:

- PostgreSQL `LISTEN/NOTIFY` to wake workers while retaining polling as recovery.
- Redis wake-up/pub-sub only when measured scale justifies it.

### 7.5 Queue abstraction

Keep orchestration dependent on an interface:

```text
TaskQueue
  claim()
  heartbeat()
  succeed()
  retry()
  fail()
  cancel()
  release_expired()
```

The MVP implementation is `PostgresTaskQueue`. A future Redis implementation must not change audit-domain or reporting code.

---

## 8. Audit state machine

```text
DRAFT
  -> VALIDATING
  -> QUEUED
  -> RUNNING
  -> ANALYZING
  -> REPORTING
  -> COMPLETED
```

Terminal and exceptional states:

- `VALIDATION_FAILED`
- `PARTIALLY_COMPLETED`
- `FAILED`
- `CANCELLED`

A provider authentication failure must not discard successful results from other engines. The report must disclose coverage and failed tasks.

---

## 9. Concurrency and rate limiting

Snapshot all run controls at audit creation:

- total audit concurrency;
- workspace concurrency;
- provider-route concurrency;
- logical-engine concurrency;
- requests per minute;
- token budget where supported;
- retryable error classes;
- maximum attempts;
- request timeout.

MVP implementation:

- worker obtains a bounded batch of PostgreSQL task leases;
- in-process async semaphores enforce local provider concurrency;
- the worker persists rate-limit timestamps and retry availability;
- deployment begins with one worker service or a small fixed worker count.

Future Redis upgrade:

- distributed token buckets;
- distributed semaphores;
- high-frequency coordination across many worker replicas.

Do not add Redis merely to support two small worker instances.

---

## 10. Provider adapter contract

```text
AnswerEngineAdapter
  validate_connection()
  estimate()
  execute()
  normalize_response()
  normalize_usage()
  normalize_citations()
  classify_error()
```

Normalized response fields:

- answer text;
- complete provider payload artifact;
- provider-returned citations;
- exact model ID;
- logical engine;
- transport provider;
- request ID;
- timestamps;
- finish reason;
- usage;
- safety/refusal metadata;
- normalized error.

Adapters execute and normalize. They do not calculate visibility.

---

## 11. Analysis pipeline

```text
raw response
  -> text normalization
  -> deterministic brand and alias detection
  -> deterministic competitor detection
  -> citation URL extraction
  -> URL normalization and source ownership
  -> ordered-list/table/rank detection
  -> sentiment and theme extraction
  -> claim extraction
  -> evidence comparison
  -> optional LLM adjudication
  -> persisted canonical analysis
```

### Deterministic-first rules

- Unicode and case normalization.
- Boundary-safe alias matching.
- Explicit alias and domain registry.
- URL canonicalization and tracking-parameter removal.
- Ordered list/table parsing.
- explicit negation handling;
- source classification: owned, competitor-owned, third party, unknown.
- no forced sentiment or rank when evidence is ambiguous.

### Optional adjudication

Use the configured analysis model only for bounded ambiguity:

- unclear entity reference;
- implied order;
- sentiment requiring context;
- claim comparison against the brand evidence snapshot.

Persist the adjudication prompt, model, output, confidence, and reason.

---

## 12. Metrics

All formulas are versioned.

### Visibility rate

```text
eligible successful responses mentioning brand
/
eligible successful responses
```

### Owned citation rate

```text
eligible successful responses citing at least one owned URL
/
eligible successful responses
```

### Share of voice

Report two definitions:

1. response-level brand presence compared with tracked competitors;
2. mention-level share across all tracked brand and competitor mentions.

### Average rank

Include only responses with a confidently detected ordered recommendation.

### Sentiment

- positive;
- neutral;
- negative;
- unknown.

Unknown is not silently converted to neutral.

### Accuracy issue rate

```text
brand-mentioning responses with at least one supported accuracy finding
/
brand-mentioning responses
```

### Repeat stability

When repeat count is greater than one:

- answer agreement;
- mention variance;
- citation variance;
- rank variance;
- refusal/error variance.

---

## 13. Reporting architecture

Create one renderer-independent canonical model:

```text
CanonicalReport
  methodology
  coverage
  executive_summary
  metric_cards
  engine_comparison
  competitor_comparison
  prompt_clusters
  citation_analysis
  accuracy_findings
  opportunities
  prompt_appendix
  cost_and_failures
  provenance
```

Renderers:

- HTML;
- Markdown;
- CSV datasets;
- JSON evidence bundle.

Every recommendation links to supporting prompt and response IDs. The report distinguishes:

- observed evidence;
- calculated metrics;
- model/analyst interpretation;
- recommended action.

---

## 14. API surface

```text
POST   /api/v1/brands/analyze
GET    /api/v1/projects/{id}/brand-evidence

POST   /api/v1/prompt-sets
POST   /api/v1/prompt-sets/{id}/generate
POST   /api/v1/prompt-sets/{id}/import
PATCH  /api/v1/prompts/{id}
DELETE /api/v1/prompts/{id}

GET    /api/v1/provider-connections
POST   /api/v1/provider-connections
PATCH  /api/v1/provider-connections/{id}
POST   /api/v1/provider-connections/{id}/test
DELETE /api/v1/provider-connections/{id}

POST   /api/v1/audits/estimate
POST   /api/v1/audits
GET    /api/v1/audits/{id}
POST   /api/v1/audits/{id}/cancel
GET    /api/v1/audits/{id}/events
GET    /api/v1/audits/{id}/responses
GET    /api/v1/audits/{id}/metrics

POST   /api/v1/audits/{id}/reports
GET    /api/v1/reports/{id}
GET    /api/v1/reports/{id}/download
```

Use server-sent events for audit progress. Keep polling as a fallback.

---

## 15. Security

- Encrypt BYOK secrets with envelope encryption backed by a production secret/KMS provider.
- Never return a stored secret after creation.
- Redact credentials and authorization headers from logs and artifacts.
- Maintain an approved provider/base-URL catalog.
- Reject arbitrary private-network provider endpoints.
- Apply SSRF protection to brand evidence fetching.
- Enforce workspace authorization in every repository query.
- Use secure HttpOnly cookies.
- Apply CSRF protection to cookie-authenticated mutations.
- Tenant-isolate raw provider responses and reports.
- Define retention and project deletion behavior.
- Audit connection creation, testing, rotation, and deletion.
- Require explicit confirmation before sending brand evidence to a selected discovery provider.

---

## 16. Deployment

### Vercel

Deploy:

```text
apps/web
```

Responsibilities:

- Next.js rendering;
- UI assets;
- preview deployments;
- route shell and interactive frontend.

### Railway API service

Start FastAPI as a long-running web service.

Responsibilities:

- auth;
- domain APIs;
- audit creation and cancellation;
- SSE event endpoint;
- report download authorization.

### Railway worker service

Use the same backend image with a worker start command.

Responsibilities:

- claim PostgreSQL tasks;
- provider execution;
- analysis;
- report generation;
- lease heartbeat and recovery.

### Railway PostgreSQL

Responsibilities:

- canonical product state;
- queue rows and leases;
- metrics;
- provenance;
- configuration snapshots.

### Object storage

Use an S3-compatible service for large raw payloads and generated artifacts. PostgreSQL stores content hashes, metadata, and object keys.

---

## 17. Migration plan

### Phase 0 — Locate and freeze the source audit

Deliverable: `AI_VISIBILITY_SOURCE_MAP.md`

- identify exact source revision;
- list routes, models, workers, analyzers, templates, fixtures, and reports;
- preserve representative inputs and outputs;
- create a golden evidence bundle;
- document behavior that must remain unchanged.

### Phase 1 — Characterization tests

Black-box tests for:

- prompt ingestion;
- provider execution;
- response parsing;
- mentions;
- citations;
- ranking;
- competitors;
- metrics;
- partial failures;
- reports and exports.

### Phase 2 — Project skeleton

- monorepo;
- Next.js application;
- FastAPI application;
- PostgreSQL and migrations;
- PostgreSQL task queue;
- worker process;
- authentication and workspace boundaries;
- object storage;
- telemetry;
- local Docker environment.

### Phase 3 — Provider settings

- encrypted provider connections;
- centralized settings UI;
- direct and OpenRouter routes;
- connection tests;
- model snapshots;
- cost estimates;
- strict separation of measurement and discovery settings.

### Phase 4 — Brand and prompt workflow

- evidence fetch;
- brand snapshot;
- configurable prompt generation count;
- manual prompts;
- CSV import/export;
- prompt classification and review.

### Phase 5 — Audit execution

- state machine;
- PostgreSQL task leases;
- provider adapters;
- retries;
- concurrency;
- rate limits;
- cancellation;
- raw evidence;
- progress events.

### Phase 6 — Analysis migration

- deterministic analyzers first;
- analyzer versioning;
- optional LLM adjudication;
- comparison against golden artifacts.

### Phase 7 — Reporting

- canonical report model;
- existing report meaning preserved;
- HTML and Markdown;
- CSV and JSON evidence;
- provenance.

### Phase 8 — Frontend

Implement only:

- project overview;
- brand setup;
- prompt library;
- audit setup and progress;
- response/citation explorer;
- reports;
- provider settings.

### Phase 9 — Hardening

- provider fault injection;
- concurrent-worker tests;
- lease recovery tests;
- security review;
- backup and restore;
- retention/deletion test;
- load and cost tests;
- user acceptance against golden audits.

---

## 18. Redis upgrade criteria

Redis is not scheduled work. Create an architecture decision only when telemetry shows one or more of these:

- queue claim/update traffic materially degrades normal PostgreSQL latency;
- many worker replicas require distributed rate-limit coordination;
- audit event fan-out requires lower-latency pub/sub;
- cache load is expensive enough to justify a dedicated transient store;
- PostgreSQL queue maintenance becomes a measurable operational burden.

Before upgrading:

1. capture queue throughput and latency;
2. identify whether the problem is claiming, rate limiting, events, or caching;
3. choose Redis only for the affected capability;
4. retain canonical task/attempt state in PostgreSQL;
5. run dual-write or reconciliation tests during migration.

---

## 19. MVP acceptance criteria

1. All provider secrets are configured only in Providers & Settings.
2. Direct or OpenRouter routes work for all three measurement engines.
3. Discovery/analysis model configuration is separate.
4. AI discovery can be skipped.
5. Manual and CSV prompt paths work.
6. A configurable number of prompts can be generated and edited.
7. A one-time audit creates durable PostgreSQL tasks.
8. Multiple workers cannot claim the same task.
9. Expired leases recover safely.
10. Provider failures do not discard unrelated successful evidence.
11. Every metric is traceable to raw evidence.
12. Citation normalization and owned-domain classification are inspectable.
13. HTML, Markdown, CSV, and JSON exports are reproducible.
14. Secrets do not appear in logs or artifacts.
15. Costs, retries, failures, and coverage are visible.
16. A running audit can be cancelled safely.
17. Golden reports match the existing audit within documented tolerances.
18. The product deploys with Vercel, Railway API, Railway worker, and Railway PostgreSQL without Redis.

---

## 20. Post-MVP roadmap

### Release 1.1

- recurring schedules;
- report delivery;
- historical trends;
- multi-region/language runs;
- saved views and prompt clusters.

### Release 1.2

- Google Search Console;
- Google Analytics 4;
- AI referral classification;
- conversion correlation;
- owned-page opportunity mapping.

### Release 1.3

- server/edge-log ingestion;
- verified AI crawler identification;
- crawler-to-page analytics;
- robots and bot-access recommendations.

### Release 2

- Redis where measured scaling requires it;
- browser-based consumer experience capture where appropriate;
- content recommendation workflow;
- remediation tracker;
- agency multi-client controls;
- consented aggregate benchmarks.

---

## 21. References

- CrawlerAI repository: https://github.com/abhij1306/CrawlerAI
- CrawlerAI frontend architecture: https://github.com/abhij1306/CrawlerAI/blob/main/docs/frontend-architecture.md
- PostgreSQL locking and `SKIP LOCKED`: https://www.postgresql.org/docs/current/sql-select.html
- Next.js App Router: https://nextjs.org/docs/app
- Vercel monorepos: https://vercel.com/docs/monorepos
- Profound product reference: https://www.tryprofound.com/

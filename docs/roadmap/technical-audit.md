# Roadmap — Technical Audit (Site Health) crawler

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

A **fast, HTTP-first, Screaming-Frog-style site crawler** that audits a website's technical
SEO / AEO health and emits a de-duplicated **Issues catalog**. It is deliberately **not** a
browser-rendering crawler and **not** a commerce/acquisition engine — those are
heavyweight and out of scope. The design target is **throughput**: crawl tens of thousands of
URLs in minutes by doing concurrent async HTTP fetches + streaming HTML parsing, with browser
rendering as an optional, opt-in, per-URL fallback only.

Two product surfaces consume it (both roadmap):
- **Site Health** — a dashboard: crawl summary, score, issue counts by severity/category,
  crawl coverage (indexable vs blocked), and trend across crawls.
- **Issues** — an open-ended, filterable catalog of individual issues with affected-URL lists
  and remediation guidance.

## 2. Why HTTP-first (the speed requirement)

| Approach | Throughput | Use |
|----------|-----------|-----|
| Async HTTP fetch + streaming parse (`httpx.AsyncClient` + `selectolax`/`lxml`) | Thousands of URLs/min | **Default path — 95%+ of checks** |
| Headless browser render (Playwright) | ~1–5 URLs/sec/instance | **Opt-in fallback only** — JS-rendered content, hydration diffs, CWV field-ish lab metrics |

The crawler must never block the fast path on the slow path. Browser rendering is a separate,
rate-limited task type that only runs for a sampled/flagged subset of URLs (e.g. pages where
`<body>` is near-empty in raw HTML, or the user explicitly enables "render check").

Performance levers:
- A **bounded async worker pool** with per-host concurrency caps + politeness delay (respect
  `robots.txt` crawl-delay); global concurrency cap from config.
- **Streaming HTML parsing** (SAX-style / `selectolax`) so large pages don't buffer fully.
- **HEAD-first** for asset/link status checks; upgrade to GET only when needed.
- **Response body size cap** + content-type allow-list (parse `text/html`, HEAD-only for
  binaries).
- **Connection reuse** (keep-alive), gzip/br, HTTP/2 via httpx.
- **Content-hash dedup** so identical templates aren't re-analyzed.
- **Incremental crawl** (roadmap+): conditional requests (`If-None-Match`/`If-Modified-Since`)
  to skip unchanged pages on re-crawl.

## 3. Data model (new tables — UUID PKs, workspace-scoped)

Mirror the audit engine's shape: one **crawl** owns a queue of **crawl tasks**, each producing
an immutable **page artifact**, from which deterministic **issues** are derived.

- **`SiteCrawl`** — one crawl run. `id`, `workspace_id`, `project_id`, `root_url`, `status`
  (state machine below), `config_snapshot` (JSONB: max URLs, depth, include/exclude patterns,
  render-fallback on/off, respect-robots), `random_seed` (frontier ordering determinism),
  `requested_count`, `completed_count`, `failed_count`, counts by severity, `analyzer_version`,
  timestamps. Immutable config snapshot per crawl (invariant 4 provenance).
- **`CrawlTask`** — one URL to fetch. Reuse the MVP queue-row contract: `lease_owner`,
  `lease_expires_at`, `heartbeat_at`, `attempt_count`, `max_attempts`, `idempotency_key`
  (unique), unique `(crawl_id, url_hash)`, `depth`, `discovered_from` (parent URL / provenance),
  `status`. Claimed with `FOR UPDATE SKIP LOCKED` (invariant 8). No double-claim.
- **`PageArtifact`** — immutable, written once (invariant 3). `id`, `crawl_id`, `task_id`,
  `url`, `final_url` (after redirects), `http_status`, `redirect_chain` (JSONB), `content_type`,
  `content_hash`, `fetched_at`, `latency_ms`, `response_bytes`, `headers` (JSONB, redacted),
  and the **parsed facts** (see §4) either inline JSONB or a linked `PageFacts` row. Large raw
  HTML goes to S3-compatible object storage (roadmap) with the object key + hash stored here —
  Postgres holds metadata, not multi-MB bodies.
- **`SiteIssue`** — a derived issue **instance**. `id`, `crawl_id`, `rule_id` (FK to the rule
  catalog in config), `severity`, `category`, `url` (affected page), `evidence` (JSONB: the
  exact offending value — e.g. the 512-char title, the 404 target), `source_artifact_id`
  (provenance, invariant 4), `analyzer_version` + `rule_version`. One row per (rule, url).
- **`CrawlEvent`** — lifecycle events (append-only), same as `AuditEvent`, for SSE/polling.

The **rule catalog itself lives in config** (`app/core/config/site_audit.py`) — never inline in
service code (invariant 1). Each rule: `rule_id`, `category`, default `severity`, thresholds
(e.g. title length 30–60), and a human-readable description + remediation. Issues reference
`rule_id`; the catalog defines the rule.

## 4. What the fast (HTTP) path extracts per page

Parse once, stream, and record deterministic facts (no LLM — invariant 9):

**Indexability & crawl directives**
- HTTP status + redirect chain (flag chains > 1 hop, redirect loops, 4xx/5xx).
- `robots.txt` allow/deny for the URL; `X-Robots-Tag` header; `<meta name="robots">`
  (noindex/nofollow).
- `rel=canonical` (self / cross / missing / conflicting with `og:url`).
- `hreflang` cluster consistency (return-tag reciprocity).
- Presence in `sitemap.xml` vs discovered-by-crawl (orphan detection).

**On-page SEO**
- `<title>` (missing / duplicate / length out of band).
- `<meta name="description">` (missing / duplicate / length).
- Heading structure: exactly-one-H1, H1 present, heading order.
- Canonical/OG/Twitter card completeness.
- Image `alt` coverage; `img` without dimensions.
- Word count / thin-content threshold.

**Structured data / AEO**
- JSON-LD / microdata presence + type (Organization, Product, FAQ, Article…), and
  **schema validity** (required-property checks against a bundled schema map in config).
- `FAQPage` / `HowTo` presence (AEO-relevant).

**Links & assets**
- Internal vs external link graph; **broken links** (HEAD sweep of discovered links, deduped).
- Anchor text quality (empty / "click here"); `nofollow`/`sponsored` on external.
- Mixed-content (HTTP asset on HTTPS page); asset 404s.

**Performance / delivery (lab-ish, no field CWV without RUM)**
- TTFB (from the fetch), response size, compression (gzip/br) presence, cache headers,
  HTTP version. **Note:** true Core Web Vitals (LCP/CLS/INP) are field metrics — they belong
  to the browser-render fallback or a PageSpeed/CrUX integration (roadmap), not the fast path.
- Optional: HTML size, blocking-resource count (static heuristic from markup).

**Security / hygiene**
- HTTPS, HSTS header, canonical http→https redirect, valid `Content-Type`, charset declared.

## 5. Crawl lifecycle & state machine

Reuse the audit pattern (`app/orchestration/*`). Crawl-level:
`DRAFT → VALIDATING → QUEUED → RUNNING → ANALYZING → REPORTING → COMPLETED`
plus `PARTIALLY_COMPLETED / FAILED / CANCELLED`. Cancellation is **cooperative** (workers stop
at the URL boundary — invariant 9). Frontier expansion (discovering new URLs from parsed links)
happens inside the worker as it processes each task: it enqueues child `CrawlTask` rows
(respecting depth/pattern/robots limits and the max-URL cap) with an idempotency key so the same
URL is never queued twice. Analysis (issue derivation) can run per-page as artifacts land, with a
final aggregation at `REPORTING` (issue counts, score, coverage) — exactly like MetricSnapshot.

## 6. API surface (roadmap; `/api/v1`)

- `POST /site-crawls` — start a crawl (body: root url or project, config overrides).
- `GET /site-crawls?project_id=` / `GET /site-crawls/{id}` — list / detail projection.
- `POST /site-crawls/{id}/cancel` — cooperative cancel (409 if not cancelable).
- `GET /site-crawls/{id}/pages` — crawled-page rows (paged).
- `GET /site-crawls/{id}/issues?severity=&category=&rule_id=` — issues catalog (paged, filtered).
- `GET /site-crawls/{id}/events?stream=true` — SSE lifecycle (polling is baseline, SSE optional).
- `GET /site-crawls/{id}/export.{csv,md}` — issue/page export (projection, invariant 7).
- `GET /projects/{id}/site-health` — dashboard projection (latest completed crawl; trend is
  roadmap+).

All workspace-scoped via `require_workspace_member` (invariant 5). Secrets/creds N/A here, but
if authenticated crawling is added, credentials follow the BYOK Fernet pattern (invariant 6).

## 7. Frontend (roadmap)

- **Routes:** `/site-health` (dashboard) and `/issues` (catalog) — already stubbed as disabled
  "soon" nav items in `frontend/components/layout/nav-items.ts` under the **On Page** group.
- Reuse the MVP contract layer: add `siteHealth.ts` API module + zod schemas in
  `frontend/lib/api/`, `queryKeys.siteHealth.*`, and the existing table/badge/card/donut
  primitives. Dashboard = score ring + severity donut + coverage bars + issue-count table;
  catalog = filterable dense table with severity badges + affected-URL drill-down + remediation.
- Same-origin `/api/*` proxying (gotcha 2) and polling-first + optional SSE (like `/runs`).

## 8. Config & tuning knobs (all in `app/core/config/site_audit.py`)

`MAX_URLS_PER_CRAWL`, `MAX_DEPTH`, `GLOBAL_CONCURRENCY`, `PER_HOST_CONCURRENCY`,
`PER_HOST_DELAY_MS`, `REQUEST_TIMEOUT_S`, `MAX_RESPONSE_BYTES`, `PARSE_CONTENT_TYPES`,
`RESPECT_ROBOTS` (default true), `RENDER_FALLBACK_ENABLED` (default false) + its sampling rule,
the **rule catalog** (thresholds + severities + remediation text), and the schema-validity map.
Nothing tunable is hard-coded in service/worker code (invariant 1).

## 9. Suggested build order

1. Config: rule catalog + crawl config (`site_audit.py`) + migration for the 5 tables.
2. Fetcher: async httpx pool with per-host politeness + robots + redirect/size caps (unit-tested
   against a local fixture server; no live internet in tests).
3. Parser: streaming HTML → `PageFacts` (deterministic, table-tested).
4. Queue + worker + frontier expansion (reuse `PostgresTaskQueue` / state machine).
5. Issue derivation (rules → `SiteIssue`, provenance + versions) + aggregation snapshot.
6. API routers + exports (projections only).
7. Frontend Site Health + Issues screens (flip the disabled nav items live).
8. Browser-render fallback as a separate opt-in task type (last).

## 10. Explicit non-goals (MVP of this surface)

- No full browser rendering on the fast path (opt-in fallback only).
- No true field Core Web Vitals without a RUM/CrUX/PageSpeed integration (roadmap+).
- No commerce/acquisition/selector/domain-memory crawling engine.
- No LLM in issue detection (deterministic rules only — invariant 9); LLM-written remediation
  copy, if ever added, is a projection layer over deterministic findings, never the detector.

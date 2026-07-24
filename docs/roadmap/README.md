# Roadmap

Searchify's MVP is the **AI-visibility slice** (see the repo [`README.md`](../../README.md)).
The full product is a broader AEO suite. This folder holds detailed design specs for roadmap
surfaces as they are written, so future development can start without re-deriving the
architecture. Surfaces marked **IMPLEMENTED** below have since shipped (v2 Visibility Insights:
direct OpenAI, cross-run trends, and persisted mention/citation + query-fanout evidence); their
specs are retained as design records and annotated with the live owners/tests.

All roadmap work must still honor the same conventions as the MVP: UUID PKs, workspace
scoping, the Postgres `FOR UPDATE SKIP LOCKED` task queue (no Redis at MVP), immutable
artifacts, provenance + version on every derived row, config-in-config-only, and same-origin
`/api/*` proxying. See [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md).

## Surface status

| Surface | Detailed spec | Notes |
|---------|---------------|-------|
| **Technical Audit / Site Health** (HTTP-first, Screaming-Frog-style crawler) | [`technical-audit.md`](technical-audit.md) | **Implemented** — fast async-HTTP crawler + Issues catalog. As-shipped reference: [`../site-health.md`](../site-health.md). |
| **Site Health v2** (page-type-aware analysis + adaptive fetch) | [`site-health-v2-page-aware.md`](site-health-v2-page-aware.md) | **Design** — deterministic page-type classification, per-type rule/schema/scoring profiles, expanded AEO rule catalog (AI-crawler robots.txt stance, `llms.txt`, citability), curl_cffi fetch escalation + opt-in render tier. |
| LLM Analytics / AI referrals | [`llm-analytics.md`](llm-analytics.md) | **IMPLEMENTED** — deterministic AI-referral classification + sanitization (`app/domain/analytics/classification.py`, `sanitize.py`), ingestion chain on the `AnalyticsTask` queue (`app/domain/analytics/ingest.py`, `tasks.py`, `workers/analytics_worker.py`), persisted `AnalyticsSnapshot` projections (`snapshot.py`) served by `GET /projects/{id}/llm-analytics(+referrals,+themes)` (`app/api/analytics.py`). Tests: `backend/tests/component/test_referral_ingest.py`, `test_classify_referrals_worker.py`, `test_post_sync_chain.py`, `test_llm_analytics_api.py`. |
| Traffic | [`traffic.md`](traffic.md) | **IMPLEMENTED** — `TrafficSnapshot`/`TrafficPageStat`/`TrafficQueryStat` projections over `IntegrationMetricRow` (`app/domain/traffic/projection.py`, `service.py`; page join → `SiteUrl` via `canonical_identity`), served by `GET /projects/{id}/traffic(+pages,+queries)` + `POST …/traffic/sync` (`app/api/traffic.py`). Tests: `backend/tests/unit/test_traffic_projection.py`, `backend/tests/component/test_traffic_refresh.py`, `test_traffic_api.py`, `test_traffic_sync_api.py`. |
| Content (writer) | [`content-writer.md`](content-writer.md) | Gap-derived briefs + discovery-model drafts; distinct from measurement engines. |
| Opportunities | [`opportunities.md`](opportunities.md) | Deterministic prioritized action list; projection over analysis + site issues + traffic. |
| Brand / Competitors / E-E-A-T rich profile | [`brand-profile.md`](brand-profile.md) | Extends the MVP brand models with E-E-A-T signals + competitor profiles. |
| Topics | [`topics.md`](topics.md) | Prompt/theme clustering + per-topic visibility (deterministic headline metric). |
| Cross-run Visibility trend history | [`visibility-trends.md`](visibility-trends.md) | **IMPLEMENTED** — `GET /projects/{id}/visibility/trends` (`app/api/projects.py`, `app/domain/analysis/service.py`) powers the Trends tab (`components/visibility/visibility-trends.tsx`); tests in `backend/tests/component/test_analysis_http.py`, `frontend/lib/visibility/dashboard.test.ts`. |
| Persisted execution evidence + Query Fanout | [`visibility-trends.md`](visibility-trends.md) | **IMPLEMENTED** — `GET /projects/{id}/visibility/evidence` → `VisibilityEvidenceResponse{items,truncated}` feeds the Mentions & Citations + Query Fanout tabs (`queries_available\|count_only\|no_search`). |
| Sentiment + average position | [`sentiment-position.md`](sentiment-position.md) | Separate versioned LLM-adjudicated layer; never mutates or back-fills deterministic rows (invariant 9). |
| GSC / GA4 / Bing integrations | [`integrations.md`](integrations.md) | **IMPLEMENTED** — OAuth grants (one shared Google grant ⇒ GSC+GA4; Microsoft ⇒ Bing) with Fernet-encrypted tokens (`app/domain/integrations/service.py`, `app/connectors/integrations/`), connection/test/sync/mapping APIs (`app/api/integrations.py`), sync worker + cadence dispatcher (`app/workers/integration_worker.py`, `integration_dispatcher.py`), immutable `IntegrationImportArtifact`s → `IntegrationMetricRow` derivation with `resync_seq` re-syncs (`app/domain/integrations/derive.py`). Tests: `backend/tests/component/test_integrations_oauth_api.py`, `test_integration_worker.py`, `test_integration_ga4.py`, `test_integration_bing.py`, `test_integration_dispatcher.py`. |
| Agent, MCP | [`agent-mcp.md`](agent-mcp.md) | In-product agent + MCP server, both read-only via workspace-scoped projections. |
| AI-suggested prompt generation (`/prompt-research`) | [`prompt-generation.md`](prompt-generation.md) | **IMPLEMENTED** — topic-driven generation via the app-level default agent (`app/domain/prompts/generation.py`, `app/connectors/agent/client.py`, `app/domain/prompts/topics.py`); first-class `Topic` table + `proposed/active/archived` prompt review lifecycle (suggestions are never audit-eligible until accepted). Tests: `backend/tests/component/test_prompt_generation_api.py`, `backend/tests/unit/test_prompt_generation.py`, `frontend/app/(app)/prompts/page.test.tsx`. |
| Direct OpenAI adapter | [`openai-adapter.md`](openai-adapter.md) | **IMPLEMENTED** — `app/connectors/answer_engines/openai.py` + `openai_parser.py` (OpenAI Responses API) is the active `openai` transport for ChatGPT. Tests: `backend/tests/unit/test_answer_engine_adapters.py`. |

The high-level product rationale for all of these lives in
[`../architecture.md`](../architecture.md);
the per-surface MVP/roadmap markers live in [`../backend-architecture.md`](../backend-architecture.md)
§Surface map and [`../frontend-architecture.md`](../frontend-architecture.md) §Route map.

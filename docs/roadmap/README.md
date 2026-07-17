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
| LLM Analytics / AI referrals | [`llm-analytics.md`](llm-analytics.md) | Deterministic AI-referral classification + cross-engine analytics over time (feeds off the integrations). |
| Traffic | [`traffic.md`](traffic.md) | Organic + AI-driven traffic from GSC/GA4; page-joined to the site crawl. |
| Content (writer) | [`content-writer.md`](content-writer.md) | Gap-derived briefs + discovery-model drafts; distinct from measurement engines. |
| Opportunities | [`opportunities.md`](opportunities.md) | Deterministic prioritized action list; projection over analysis + site issues + traffic. |
| Brand / Competitors / E-E-A-T rich profile | [`brand-profile.md`](brand-profile.md) | Extends the MVP brand models with E-E-A-T signals + competitor profiles. |
| Topics | [`topics.md`](topics.md) | Prompt/theme clustering + per-topic visibility (deterministic headline metric). |
| Cross-run Visibility trend history | [`visibility-trends.md`](visibility-trends.md) | **IMPLEMENTED** — `GET /projects/{id}/visibility/trends` (`app/api/projects.py`, `app/domain/analysis/service.py`) powers the Trends tab (`components/visibility/visibility-trends.tsx`); tests in `backend/tests/component/test_analysis_http.py`, `frontend/lib/visibility/dashboard.test.ts`. |
| Persisted execution evidence + Query Fanout | [`visibility-trends.md`](visibility-trends.md) | **IMPLEMENTED** — `GET /projects/{id}/visibility/evidence` → `VisibilityEvidenceResponse{items,truncated}` feeds the Mentions & Citations + Query Fanout tabs (`queries_available\|count_only\|no_search`). |
| Sentiment + average position | [`sentiment-position.md`](sentiment-position.md) | Separate versioned LLM-adjudicated layer; never mutates or back-fills deterministic rows (invariant 9). |
| GSC / GA4 / Bing integrations | [`integrations.md`](integrations.md) | OAuth connections with Fernet-encrypted tokens; sync via the SKIP LOCKED queue. |
| Agent, MCP | [`agent-mcp.md`](agent-mcp.md) | In-product agent + MCP server, both read-only via workspace-scoped projections. |
| AI-suggested prompt generation (`/generate`) | [`prompt-generation.md`](prompt-generation.md) | Flips the existing 501 stub; discovery-model driven, `origin='generated'` provenance. |
| Direct OpenAI adapter | [`openai-adapter.md`](openai-adapter.md) | **IMPLEMENTED** — `app/connectors/answer_engines/openai.py` + `openai_parser.py` (OpenAI Responses API) is the active `openai` transport for ChatGPT; the OpenRouter adapter/parser were deleted and OpenRouter retired by migration `0008` (marker `openrouter_retired_v2`). Tests: `backend/tests/unit/test_answer_engine_adapters.py`, `backend/tests/component/test_provider_retirement_migration.py`. |

The high-level product rationale for all of these lives in
[`../architecture.md`](../architecture.md);
the per-surface MVP/roadmap markers live in [`../backend-architecture.md`](../backend-architecture.md)
§Surface map and [`../frontend-architecture.md`](../frontend-architecture.md) §Route map.

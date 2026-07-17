# Roadmap

Searchify's MVP is the **AI-visibility slice** (see the repo [`README.md`](../../README.md)).
The full product is a broader AEO suite; every surface below is **documented as roadmap and
not yet coded**. This folder holds detailed design specs for roadmap surfaces as they are
written, so future development can start without re-deriving the architecture.

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
| Cross-run Visibility trend history | [`visibility-trends.md`](visibility-trends.md) | Pure projection over per-run `MetricSnapshot`; wires the built-but-unused `trend-chart`. |
| Sentiment + average position | [`sentiment-position.md`](sentiment-position.md) | Separate versioned LLM-adjudicated layer; never mutates or back-fills deterministic rows (invariant 9). |
| GSC / GA4 / Bing integrations | [`integrations.md`](integrations.md) | OAuth connections with Fernet-encrypted tokens; sync via the SKIP LOCKED queue. |
| Agent, MCP | [`agent-mcp.md`](agent-mcp.md) | In-product agent + MCP server, both read-only via workspace-scoped projections. |
| AI-suggested prompt generation (`/generate`) | [`prompt-generation.md`](prompt-generation.md) | Flips the existing 501 stub; discovery-model driven, `origin='generated'` provenance. |
| Direct OpenAI adapter | [`openai-adapter.md`](openai-adapter.md) | Fast-follow; new adapter on the existing contract, disabled at MVP. |

The high-level product rationale for all of these lives in
[`../architecture.md`](../architecture.md);
the per-surface MVP/roadmap markers live in [`../backend-architecture.md`](../backend-architecture.md)
§Surface map and [`../frontend-architecture.md`](../frontend-architecture.md) §Route map.

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
| **Technical Audit / Site Health** (HTTP-first, Screaming-Frog-style crawler) | [`technical-audit.md`](technical-audit.md) | Fast async-HTTP crawler + Issues catalog. Full design spec written. |
| LLM Analytics / AI referrals | — | One-line entry in surface maps only. Needs a spec. |
| Traffic | — | Needs a spec. |
| Content (writer) | — | Needs a spec. |
| Opportunities | — | Needs a spec. |
| Brand / Competitors / E-E-A-T rich profile | — | Needs a spec. |
| Topics | — | Needs a spec. |
| Cross-run Visibility trend history | — | MVP dashboard is single-run; trend is roadmap. |
| Sentiment + average position | — | Nullable at MVP; needs contextual (LLM) design that preserves determinism rules. |
| GSC / GA4 / Bing integrations | — | Needs a spec. |
| Agent, MCP | — | Needs a spec. |
| AI-suggested prompt generation (`/generate`) | — | Backend stub returns not-implemented (501). |
| Direct OpenAI adapter | — | Fast-follow; disabled at MVP. |

The high-level product rationale for all of these lives in
[`../../cube27-aeo-visibility-mvp-architecture-plan-v2.md`](../../cube27-aeo-visibility-mvp-architecture-plan-v2.md);
the per-surface MVP/roadmap markers live in [`../backend-architecture.md`](../backend-architecture.md)
§Surface map and [`../frontend-architecture.md`](../frontend-architecture.md) §Route map.

# Roadmap — Agent + MCP

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

Two related capabilities, sharing one data-access core:

- **(a) In-product AI Agent** — a conversational assistant that answers questions about a
  workspace's visibility data ("which competitor gained share of voice on ChatGPT last run?",
  "which prompts have zero owned citations?") and can drive workflows (launch an audit, open a
  run) on the user's behalf. It reasons over **persisted, projected evidence** — it does not
  re-measure anything.
- **(b) MCP (Model Context Protocol) server** — a standards-compliant server that exposes the
  same workspace-scoped data + tools to *external* MCP clients (Claude Desktop, IDE agents,
  etc.), so a user's own agent can query their Searchify workspace.

The unifying rule: **both read the same workspace-scoped projection endpoints the MVP dashboard
uses; neither bypasses workspace auth and neither recomputes a metric** (invariant 5, invariant
7). The agent is a *reader + orchestrator over persisted evidence*, not a second analysis
engine. This is the master plan's "Agent, MCP" roadmap line
([`../backend-architecture.md`](../backend-architecture.md) §1;
[`../frontend-architecture.md`](../frontend-architecture.md) §2).

## 2. The measurement / analysis boundary (invariant 10)

The agent's LLM is the **discovery/analysis model**, NOT a measurement engine. Measurement
engines (`chatgpt`/`gemini`/`claude`, `provider_catalog.LOGICAL_ENGINES`) exist only to be
*measured*; the agent's brain is a separately configured model — the same `DiscoveryModelConfig`
role introduced for prompt generation (`models/provider.py` `DiscoveryModelConfig`, plumbing-only
at MVP). Every agent turn that calls a model records the **logical/transport/model triple**
(invariant 10): `logical_engine` (the discovery model's logical name), `transport_provider`, and
the exact `transport_model` — so an agent answer is as traceable as an audit attempt.

Consequences:
- The discovery model is configured in Provider Settings as a `DiscoveryModelConfig`, resolving
  a `ProviderConnection` for its BYOK key at call time (invariant 6) — never from env.
- Mixing roles is a violation: the agent must not be pointed at a measurement route, and audits
  must not use the discovery model to answer prompts.

## 3. Data-access contract — read persisted evidence only

The agent + MCP tools may **only** read through the existing workspace-scoped projection
endpoints (invariant 7). They are wrappers over the same service functions the HTTP routers call
— not new SQL that reaches around workspace scoping:

- `GET /projects/{id}/visibility?audit_id=` — selected-run score + per-engine comparison +
  brand-vs-competitor rankings.
- `GET /audits`, `GET /audits/{id}`, `GET /audits/{id}/metrics`, `GET /audits/{id}/executions`,
  `GET /executions/{id}` — run/execution evidence (answer text, classified citations, mentions).
- `GET /prompt-sets`, `GET /projects` — prompt + project context.

Hard rules:
- **Every tool call carries the caller's workspace context** and goes through
  `require_workspace_member` scoping (invariant 5). A tool can never read another workspace;
  cross-workspace access returns 403/404, not data.
- **The agent never recomputes metrics** (invariant 7). If a number is not persisted, the agent
  cannot report it — it surfaces `—` (e.g. sentiment/avg-position, still null at MVP) exactly
  like the dashboard, and never back-fills with a guess.
- **No fabrication** (non-goal §8): every quantitative claim the agent makes must cite a
  persisted row (an execution id, an audit id, a `MetricSnapshot`). The provenance the agent
  used is recorded on the message (§4) so an answer is auditable.

## 4. Data model (new tables — UUID PKs, workspace-scoped)

- **`AgentSession`** — one conversation thread. `id`, `workspace_id`, `project_id` (nullable —
  a session may be workspace-wide), `title`, `discovery_model_snapshot` (JSONB: the resolved
  logical/transport/model triple frozen at session start, invariant 10), `status`
  (`active | archived`), `created_at`, `updated_at`. Workspace-scoped (invariant 5).
- **`AgentMessage`** — one turn, **append-only / immutable** (invariant 3). `id`, `session_id`,
  `workspace_id`, `role` (`user | assistant | tool`), `content` (text), `tool_calls` (JSONB: the
  tools invoked this turn + their arguments — never any credential), `evidence_refs` (JSONB:
  the persisted rows the turn read — audit ids, execution ids, metric-snapshot ids — this is the
  **provenance** for the turn, invariant 4), `model_identity` (logical/transport/model triple
  for assistant turns, invariant 10), `usage` (JSONB token counts), `created_at`. Written once;
  a re-run is a new message, never an overwrite (invariant 3).
- **`AgentToolInvocation`** (optional, or folded into `tool_calls`) — append-only record of one
  tool call: `id`, `message_id`, `tool_name`, `arguments_snapshot` (JSONB, credential-free),
  `result_ref`, `status`, `latency_ms`, `created_at`. Lets a write-capable tool (e.g. "launch
  audit") be audited independently.

No message row is ever mutated after write; conversation history is an immutable log, exactly
like `AuditEvent` (invariant 3).

## 5. Agent execution (queue for long turns, cooperative cancel)

A short agent turn can run inline in the request. A turn that fans out into several tool calls +
a long model completion runs as a **queued task on the existing Postgres `FOR UPDATE SKIP
LOCKED` queue** (invariant 8) through the `TaskQueue` Protocol — no Redis. The rules carry over:
**commit the claim before any model/network I/O**, heartbeat to hold the lease, sweeper returns
expired turns to `retry_wait`, cancellation is **cooperative** (the agent stops at a
tool-call / completion boundary — invariant 9). A cancelled turn terminalizes cleanly; no
zombie completion.

## 6. MCP server surface

The MCP server is a **separate transport surface** (its own module, `app/mcp/*`, and its own
transport — e.g. an authenticated MCP-over-HTTP/SSE endpoint) that exposes:

- **Resources** — read-only views of persisted evidence (a project's latest visibility
  projection, a run's executions, a prompt set) — each backed by the same workspace-scoped
  service function as the REST projection (invariant 7).
- **Tools** — named operations an external client can invoke: `list_audits`, `get_visibility`,
  `get_execution`, and (behind explicit scoping) `launch_audit`. Every tool is
  **workspace-scoped and auth-gated** (invariant 5).

Auth + secrets:
- **No unauthenticated MCP access** (non-goal §8). An MCP client authenticates with a
  workspace-scoped **MCP access token** minted in Settings; the token is a BYOK-style secret —
  **Fernet-encrypted at rest** (`encrypt_secret`, reuse — invariant 2/6) and **never returned
  after creation** or logged. It resolves to a `workspace_id` + a capability scope; every tool
  call is then scoped by that `workspace_id` (invariant 5).
- Read tools are enabled by default; **write/destructive tools require explicit per-token
  scoping** and are off unless granted (non-goal §8).

## 7. API + frontend

- **Agent REST** (`/api/v1`, workspace-scoped, invariant 5):
  - `GET /agent/sessions`, `POST /agent/sessions`, `GET /agent/sessions/{id}` — session CRUD +
    history projection (persisted `AgentMessage` rows only, invariant 7).
  - `POST /agent/sessions/{id}/messages` — post a user turn; returns the assistant turn (inline)
    or 202 + a task id (queued, §5).
  - `DELETE /agent/sessions/{id}` — archive.
- **MCP transport** — a dedicated mounted surface (not under the REST `/api/v1` router table),
  token-authenticated (§6), speaking MCP.
- **Frontend** — a new `/agent` route (chat UI: thread list + transcript + evidence chips
  linking to the runs/executions the answer cited). Rendered **disabled ("soon")** today; the
  route map lists *Agent / MCP* as Roadmap and the sidebar renders roadmap items disabled. Add
  an `agent.ts` API module + zod schemas; all browser calls go same-origin through the `/api/*`
  proxy (invariant 12).

## 8. Config & tuning knobs (all in `app/core/config/agent.py`)

Nothing tunable is hard-coded (invariant 1): the agent **system prompt** and tool catalog,
`AGENT_MAX_TOOL_CALLS_PER_TURN`, `AGENT_TURN_TIMEOUT_S`, `AGENT_MAX_HISTORY_MESSAGES`, the
default discovery-model reference, MCP token TTL + default capability scope, and the MCP
tool/resource allow-list. The discovery-model transport catalog stays in
`config/provider_catalog.py` (one owner, invariant 2).

## 9. Suggested build order

1. Config: `agent.py` (system prompt, tool catalog, limits) + migration for `AgentSession` /
   `AgentMessage` (+ `AgentToolInvocation`).
2. Read-only tool layer wrapping the existing projection services (workspace-scoped), with the
   `evidence_refs` provenance recorded per turn (invariant 4).
3. Discovery-model call path reusing `DiscoveryModelConfig` + BYOK resolution + the
   logical/transport/model triple (invariant 10); inline turns first.
4. Agent REST + `/agent` chat UI (flip the nav item live).
5. Queued long turns on the existing `PostgresTaskQueue` (invariant 8) + cooperative cancel.
6. MCP server: token minting (Fernet, never returned), read resources/tools, then the scoped
   `launch_audit` write tool last.

## 10. Explicit non-goals (MVP of this surface)

- **No fabricated metrics.** The agent surfaces only persisted, projected data + cites its
  source; it never recomputes or invents a number (invariant 7).
- **No write/destructive tools without explicit scoping** — read is default; writes require a
  granted capability on the token.
- **No unauthenticated MCP access**; every MCP session is workspace-scoped + auth-gated
  (invariant 5), and any token is Fernet-encrypted + never logged/returned (invariant 6).
- **The agent's model is the discovery/analysis model, never a measurement engine** — it must
  not be pointed at a measurement route, and it does not change how audits are measured or
  scored (invariant 9/10).
- **No new crypto and no parallel queue** — reuse `encrypt_secret` and the Postgres `TaskQueue`
  (invariant 2, invariant 8).

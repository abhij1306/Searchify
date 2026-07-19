# v3 — AI Prompt Generation + Topics (flips the `/generate` 501 stub)

> Feature picked from `docs/roadmap/`: **AI-suggested prompt generation** (`prompt-generation.md`),
> reshaped per product direction: generation is **topic/category-driven** (topics left rail,
> Active / Proposed / Archived prompt tabs) and is powered by a **general default AI agent**
> configured in `.env` (OpenAI-compatible endpoint; Mistral key present for dev) — NOT by the
> per-workspace BYOK `DiscoveryModelConfig` and NOT by the three measurement engines.
> Loop: explore ✅ → plan (this doc) → build → review → test → simplify.

## Decisions (confirmed with user)

1. **First-class `Topic` table** (project-scoped) + nullable `topic_id` FK on `prompts`.
   Users can add topics manually; generation proposes topics AND prompts together.
2. **Prompt review states**: new `status` column — `proposed | active | archived`.
   Manual/CSV prompts start `active`; generation fills a set-wide pool of the earliest 20
   (`GENERATION_ACTIVE_THRESHOLD`) `active` prompts and leaves the rest `proposed`; audits only
   ever consume `status='active' AND enabled=true` (preserves the "no auto-run" non-goal).
3. **One-call generation**: brand context (name, aliases, competitors, market, existing topics,
   existing prompt texts) → default agent → JSON `{topics:[{name, prompts:[...]}]}`. Optional
   `topic_id` in the request scopes generation to one existing topic. Inline execution only
   (count capped by env, default 20) — no queued path in this iteration.
4. **Default agent, not BYOK**: a new app-level, env-configured OpenAI-compatible client
   (`DEFAULT_AGENT_API_KEY` with fallback alias `MISTRALAI_API_KEY`, `DEFAULT_AGENT_BASE_URL`
   default `https://api.mistral.ai/v1`, `DEFAULT_AGENT_MODEL` default `mistral-small-latest`).
   This is deliberate scope: it will also power future content-generation features. Invariant 6's
   "never from env" governs BYOK measurement keys; this is a distinct app credential — still
   never logged, never in a DTO. Measurement engines are untouched (roadmap non-goal).
   Prompt-count limit is env for now (`GENERATION_MAX_COUNT=20`), later tied to subscription tier.
5. Backend still enforces `confirm_send_evidence=true` before any agent call (master plan §15 —
   brand evidence leaves the app only after explicit confirmation; 422 otherwise).

## Backend

### B1. Config (invariant 1 — nothing inline)
- **New `backend/app/core/config/agent.py`** — `DefaultAgentSettings(BaseSettings)`:
  `api_key` (AliasChoices `DEFAULT_AGENT_API_KEY`, `MISTRALAI_API_KEY`), `base_url`
  (default Mistral v1), `model` (default `mistral-small-latest`), `timeout_seconds`,
  `max_output_tokens`. Singleton `default_agent_settings`. Key never logged/echoed.
- **New `backend/app/core/config/prompts.py`** — `GENERATION_DEFAULT_COUNT=10`,
  `GENERATION_MAX_COUNT=20` (env-overridable via a small `BaseSettings`), `GENERATOR_VERSION`,
  the neutral generation **system-prompt template**, JSON response schema instructions,
  normalization rules for the dedupe hash, prompt `status` constants
  (`PROMPT_STATUS_PROPOSED/ACTIVE/ARCHIVED` + frozenset + default), topic origin constants.
- Add the new env vars to `.env.example` / infra env docs.

### B2. Models + schema (greenfield migration policy: edit ORM, recreate dev DB)
- **New `Topic`** (in `models/prompt.py`, beside `PromptSet`): `id` UUID PK, `project_id` FK
  (CASCADE, indexed), `name`, `description`, `origin` (`manual|generated`), timestamps,
  `UniqueConstraint(project_id, name)`. Relationship `project.topics`, `topic.prompts`.
- **`Prompt` additions**: `topic_id` nullable FK → `topics.id` (`ondelete=SET NULL`, indexed);
  `status` String(16) default `active` (server_default `'active'`); `normalized_text_hash`
  String(64) (sha256 of normalized text, computed on create/update in the service);
  `UniqueConstraint(prompt_set_id, normalized_text_hash)` → conflict-safe dedupe
  (`INSERT .. ON CONFLICT DO NOTHING`) exactly as the roadmap §3 race-avoidance requires.
- **Audit gate**: `domain/audits/planner.py` `_resolve_prompts` adds
  `Prompt.status == PROMPT_STATUS_ACTIVE` next to the existing `Prompt.enabled.is_(True)`
  filter — proposed/archived prompts can never be audited.
- Backfill note: existing rows get `status='active'` via server_default; existing create/import
  paths compute `normalized_text_hash` too (one shared `normalize_prompt_text()` helper).

### B3. Default-agent connector
- **New `backend/app/connectors/agent/client.py`** — `DefaultAgentClient` calling
  `{base_url}/chat/completions` (OpenAI-compatible; works for Mistral/OpenAI/Groq/etc.):
  `async complete(*, system, user) -> str` with `response_format={"type":"json_object"}`,
  Bearer auth, httpx timeout from settings, error classification reusing
  `connectors/answer_engines/errors.py` (`classify_provider_status`, `ProviderError`).
  Raises a typed `AgentNotConfiguredError` when the key is empty.

### B4. Domain service `domain/prompts/generation.py`
- `build_generation_request(brand_ctx, topics, existing_texts, count, intents, topic)` — pure,
  unit-testable prompt builder from the config template.
- `parse_generation_output(raw_json) -> list[SuggestedTopic]` — pure, strict parser
  (zod-style pydantic models; drops/flags malformed entries; enforces intents ∈
  `PROMPT_INTENTS`, non-empty text). Unit-tested against fixture model output (no live calls).
- `generate_prompts(session, *, workspace_id, prompt_set_id, payload)`:
  1. workspace-scope the set (404), validate `confirm_send_evidence` (422) and
     `count ≤ GENERATION_MAX_COUNT` (422);
  2. load brand context via the existing `domain/projects/shim.project_scoring_identity`
     (invariant 2 — no second brand serializer), existing topics + prompt texts;
  3. call `DefaultAgentClient`; on missing key → structured 503 `{code:'agent_not_configured'}`;
  4. get-or-create `Topic` rows by `(project_id, name)`; insert prompts with
     `origin='generated'`, `status='proposed'`, `topic_id`, `branded` auto-detected (text
     contains a brand/competitor alias), `theme=topic.name`, and full `generation_evidence`
     provenance (invariant 4): agent base-url host + model, `generation_run_id`,
     `generator_version`, brand-context hash, requested count/intents;
  5. dedupe via `ON CONFLICT (prompt_set_id, normalized_text_hash) DO NOTHING`; report
     inserted vs dropped counts.
- Review transitions in `domain/prompts/service.py`: extend `PromptUpdate` with `status`
  (+ validation of allowed values); add `bulk_set_status(session, ..., prompt_ids, status)`.

### B5. API (`api/prompts.py`, `api/projects.py`)
- **Flip the 501**: `POST /prompt-sets/{id}/generate` body
  `{count?, topic_id?, intents?, confirm_send_evidence}` → 201
  `{generated: PromptResponse[], topics: TopicResponse[], dropped_duplicates: int}`.
  Guard order preserved: scope 404 → confirmation/count 422 → agent 503.
- **Topics CRUD**: `GET/POST /projects/{project_id}/topics`,
  `PATCH/DELETE /topics/{topic_id}` (delete sets prompts' `topic_id` NULL via FK). `GET` list
  includes per-topic `active_count` / `proposed_count` (single grouped query) for the rail.
- **Bulk review**: `POST /prompt-sets/{id}/prompts/bulk-status` `{prompt_ids, status}`.
- `PromptResponse` gains `status`, `topic_id`. New `TopicResponse`.

## Frontend

### F1. Contract layer
- `lib/api/schemas.ts`: add `promptStatusSchema`, `topicSchema`, extend `promptSchema`
  (`status`, `topic_id`), `generateResponseSchema`. `lib/api/topics.ts` (topics CRUD) +
  extend `lib/api/prompts.ts` `generate` (real body + validated response) + `bulkStatus`.
  `queryKeys.topics.*`.

### F2. Prompts screen (extend `PromptLibrary`, don't rebuild)
- **Topics rail** (new `components/prompts/topic-rail.tsx`): "All topics" + per-topic rows with
  active/proposed count badges, "Add topic" inline form; selection filters the table.
- **Status tabs**: Active / Proposed / Archived above the table (client filter on `status`);
  Proposed tab shows per-row **Accept** / **Archive** actions + "Accept all" bulk action;
  Active rows get an Archive action in the existing dropdown.
- **Generate dialog** (replaces the disabled `AiSuggestPanel` "coming soon" card): count
  (bounded), optional target topic, intents; includes the brand-evidence consent copy and sends
  `confirm_send_evidence: true`; on success invalidates queries and switches to the Proposed
  tab. Agent-not-configured (503) renders an actionable alert naming the env vars.
- CSV/manual creation unchanged (stay `active`).

## Test plan (part of the loop)
- **Backend unit**: prompt-builder + JSON parser against fixture outputs (valid, malformed,
  wrong-intent, empty), normalization/hash rules, branded auto-detection.
- **Backend component** (existing async-Postgres fixtures): generate endpoint with a
  monkeypatched agent client (201 happy path incl. topic creation + provenance; 404 foreign set;
  422 missing confirmation / count over cap; 503 unconfigured; duplicate-drop on second run —
  exercises the DB constraint), topics CRUD + counts, bulk-status, `PromptUpdate.status`,
  planner excludes `proposed`/`archived` prompts. **Replace**
  `test_generate_is_not_implemented_stub`.
- **Frontend**: vitest pure-logic tests (status filtering, form mapping) + MSW page tests
  (generate flow → proposed tab, accept/archive, topics rail render, 503 state). Replace the
  "coming soon" panel test.
- Run: `pytest` (backend), `vitest run` + `tsc`/lint (frontend).

## Review + simplify passes (after build)
- Review against `docs/invariants.md`: 1 (no inline knobs), 2 (reused shim/`Prompt`
  table/error classifier — no duplicates), 4 (generation_evidence provenance), 5 (workspace
  scoping on every new query), 6 (agent key never in DTO/logs; brand list goes ONLY to the
  default agent behind explicit confirmation; measurement path untouched), 9 (no LLM in
  metrics; proposed prompts never audited).
- Simplify: collapse any duplicated status/count logic, ensure one owner per concept
  (normalization helper, status constants), prune dead `AiSuggestPanel` code, update
  `docs/roadmap/README.md` + `prompt-generation.md` status annotations and
  `docs/backend-architecture.md`/`frontend-architecture.md` surface maps.

## Out of scope (this iteration)
- Queued generation path / web-evidence fetching (`include_web_evidence`), BYOK
  `DiscoveryModelConfig` invocation, subscription-tier limits (env cap stands in), topic
  clustering of existing prompts (`docs/roadmap/topics.md`), sentiment/avg-position columns.

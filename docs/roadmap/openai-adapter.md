# Roadmap — Direct OpenAI adapter

> **Status: IMPLEMENTED (v2 direct-provider retirement).** This spec is retained as a design
> record; the direct OpenAI adapter has shipped and is now the **only** active route for
> `chatgpt`. Live owners:
> - Adapter + parser: `backend/app/connectors/answer_engines/openai.py` +
>   `openai_parser.py` (OpenAI Responses API).
> - Catalog: `backend/app/core/config/provider_catalog.py` —
>   `APPROVED_ROUTES[chatgpt] = {openai: "gpt-5.4"}`, `ACTIVE_TRANSPORTS = {openai, anthropic,
>   google}`.
> - Tests: `backend/tests/unit/test_answer_engine_adapters.py`,
>   `backend/tests/component/test_provider_retirement_migration.py`.
>
> The design below is preserved for context. It follows the same conventions as the rest of the
> codebase: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP LOCKED` task queue,
> immutable artifacts, and provenance on every derived row.

## 1. Goal & positioning

**As shipped**, the logical engine `chatgpt` is measured through the **direct OpenAI Responses
API only** (`transport_provider=openai`, model `gpt-5.4`), matching the shape of the direct
Gemini (`google`) and Anthropic (`anthropic`) adapters. The provider catalog exposes the
approved direct route for ChatGPT.

This added a **new transport for an existing logical engine** — it introduced **no new logical
engine** and changed nothing about deterministic scoring (non-goals §7).

## 2. Adapter (implements the existing contract — invariant 2)

Add `backend/app/connectors/answer_engines/openai.py` + `openai_parser.py`, mirroring
`gemini.py`/`gemini_parser.py` and `anthropic.py`/`anthropic_parser.py` (reuse the pattern, do
**not** duplicate the contract — invariant 2). It implements the same adapter surface the other
adapters expose against `connectors/answer_engines/contracts.py`:
`validate_connection()`, `estimate()`, `execute()`, `normalize_response()`,
`normalize_usage()`, `normalize_citations()`, `classify_error()`. Adapters **execute + normalize
only** — they never compute visibility.

Shape rules carried over from the existing direct adapters:
- Emit the provenance triple on `AnswerEngineResponse` (invariant 10):
  `logical_engine=chatgpt`, `transport_provider=openai`, `transport_model=<exact model id>`.
  Set `logical_engine = ENGINE_CHATGPT` and `transport_provider = TRANSPORT_OPENAI` as class
  attributes, exactly like `GeminiAnswerEngineAdapter` sets `ENGINE_GEMINI` / `TRANSPORT_GOOGLE`.
- **Stateless, brand-free request** — like `gemini.py`, each call disables server-side memory
  and uses a fixed neutral system instruction; the tracked **brand/competitor list is NEVER
  placed in the request** (invariant 6), only used in scoring afterward.
- Map HTTP/transport failures to the shared `ProviderError` + `config/provider_catalog.py`
  `ERROR_*` tokens (`ERROR_AUTH`, `ERROR_TIMEOUT`, `ERROR_RATE_LIMIT`, `ERROR_SERVER`, …) via
  `classify_provider_status` / `parse_retry_after` — reuse `errors.py`, do not add a new error
  taxonomy (invariant 2).
- Never log the response body verbatim; log status + a short reason token only (as `gemini.py`
  does).

## 3. BYOK key (invariant 6)

The OpenAI API key is BYOK: a workspace creates a `ProviderConnection` with
`transport_provider = openai`, its key **Fernet-encrypted** in `api_key_encrypted`
(`core/security.py` `encrypt_secret`). The decrypted key is resolved from the connection **at
execution time only** (never from env), passed straight into the adapter constructor, and
**never** placed in a DTO, log line, `request_snapshot`, or raw artifact (invariant 6) — same as
the Gemini/Anthropic adapters.

## 4. Catalog + factory registration (config — invariant 1)

All wiring is config + one factory branch; nothing is hard-coded in service code (invariant 1).
**As shipped:**
- **`config/provider_catalog.py`**: `TRANSPORT_OPENAI` is in `ACTIVE_TRANSPORTS`;
  `APPROVED_ROUTES[ENGINE_CHATGPT] = {openai: "gpt-5.4"}` is the **only** route for ChatGPT (the
  direct ChatGPT route is the sole approved entry);
  `openai_responses_url` lives on `ProviderCatalogSettings`. `is_route_approved`, `default_model`,
  `engines_for_transport`, and the `/provider-catalog` projection pick it up automatically.
- **`connectors/answer_engines/factory.py`** `build_adapter()`: the
  `transport_provider == TRANSPORT_OPENAI → OpenAIAnswerEngineAdapter(...)` branch mirrors the
  `TRANSPORT_GOOGLE`/`TRANSPORT_ANTHROPIC` branches; the `is_route_approved` guard allows the
  route because the catalog entry exists.

## 5. Grounding / citation differences

A direct OpenAI call uses OpenAI's own web-search/grounding surface (the Responses API
web-search tool). The `openai_parser` normalizes OpenAI's native annotations into the same
`CitationResult` / `SearchEventResult` / `AnswerEngineResponse` shapes the other parsers emit, so
downstream analysis + classification stay transport-agnostic:
- extract `search_used` + per-search `SearchEventResult`s from OpenAI's tool-call events;
- extract inline URL citations into `CitationResult` (ordinal, url, title, domain via
  `normalization.normalize_domain`, cited_text) — identical target shape to the Gemini/Anthropic
  parsers, so the deterministic citation classifier is unchanged.
Where OpenAI exposes no equivalent field (e.g. char offsets), record `None` — never invent it.
(The parser normalizes the OpenAI Responses payload.)

## 6. Frontend (transport selection)

**As shipped**, `frontend/lib/api/schemas.ts` has one wire schema
`transportProviderSchema = z.enum(['openai','anthropic','google'])` plus
`providerRouteSchema` for direct-provider provenance. `/providers` renders three per-engine cards with **one direct transport each**
(ChatGPT/OpenAI, Gemini/Google, Claude/Anthropic) — there is no route toggle and no reserved
"OpenAI (direct) — coming soon" option. Browser calls stay same-origin through the `/api/*`
proxy (invariant 12).

## 7. Suggested build order + tests

1. `config/provider_catalog.py`: `openai` in `ACTIVE_TRANSPORTS`, the sole `chatgpt→openai`
   route, and the endpoint URL knob.
2. `openai_parser.py` — pure, deterministic; **table-tested** against recorded fixture payloads
   (grounded answer + a no-search answer), mirroring
   `test_gemini_parser_grounding_citations_and_provenance` in
   `backend/tests/unit/test_answer_engine_adapters.py`. Assert the provenance triple
   (`chatgpt`/`openai`/`gpt-5.4`) and citation shapes.
3. `openai.py` adapter — payload builder (stateless, brand-free), `execute()` with mocked httpx,
   error mapping. Mirror `test_gemini_adapter_executes_and_records_provenance` /
   `test_gemini_adapter_maps_http_error` / `test_gemini_adapter_requires_key`.
4. `factory.py` branch + a test that `build_adapter(logical_engine='chatgpt',
   transport_provider='openai', ...)` returns the OpenAI adapter and that the route is now
   approved.
5. Frontend: the wire transport schema is `['openai','anthropic','google']` + `/providers` UI;
   connection-create/test flow.

## 8. Explicit non-goals

- **No change to deterministic scoring** (invariant 9) — this adapter only executes + normalizes;
  metrics remain a projection of persisted analysis (invariant 7).
- **No new logical engine** — `chatgpt` runs on the `openai` transport only; the
  logical/transport/model triple contract (invariant 10) is unchanged.
- **No new crypto, no new error taxonomy, no new contract** — reuse `encrypt_secret`,
  `errors.py`, and `contracts.py` (invariant 2).
- **Brand/competitor list is never sent to OpenAI** (invariant 6).

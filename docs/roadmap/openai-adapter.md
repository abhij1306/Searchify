# Roadmap — Direct OpenAI adapter

> **Status: roadmap / not yet coded.** This is a design spec for a future surface, written so
> an engineer (or agent) can start building without re-deriving the architecture. It follows
> the same conventions as the MVP: UUID PKs, workspace scoping, the Postgres `FOR UPDATE SKIP
> LOCKED` task queue, immutable artifacts, and provenance on every derived row. Read
> [`../../Agents.md`](../../Agents.md) and [`../invariants.md`](../invariants.md) first — every rule
> there applies here too.

## 1. Goal & positioning

At MVP the logical engine **`chatgpt` is measured through OpenRouter only** — there is no direct
OpenAI transport in the approved catalog (`config/provider_catalog.py`: `APPROVED_ROUTES[chatgpt]
= {openrouter: "openai/gpt-5.4"}`; `TRANSPORT_OPENAI` is defined but *reserved and disabled*).
This spec designs the documented **fast-follow**: a first-party OpenAI adapter so `chatgpt` can
also be reached directly (`transport_provider=openai`), matching the shape of the existing direct
Gemini (`google`) and Anthropic (`anthropic`) adapters.

This adds a **new transport for an existing logical engine** — it introduces **no new logical
engine** and changes nothing about deterministic scoring (non-goals §7).

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

All wiring is config + one factory branch; nothing is hard-coded in service code (invariant 1):
- **`config/provider_catalog.py`**: add `TRANSPORT_OPENAI` to `MVP_TRANSPORTS`; add a
  `chatgpt → {openai: "<exact-openai-model-id>"}` entry to `APPROVED_ROUTES[ENGINE_CHATGPT]`
  (alongside the existing `openrouter` entry); add the OpenAI endpoint URL (e.g.
  `openai_responses_url`) to `ProviderCatalogSettings`. `is_route_approved`, `default_model`,
  `engines_for_transport`, and the `/provider-catalog` projection then pick it up automatically.
- **`connectors/answer_engines/factory.py`** `build_adapter()`: add a
  `transport_provider == TRANSPORT_OPENAI → OpenAIAnswerEngineAdapter(...)` branch, mirroring
  the existing `TRANSPORT_GOOGLE`/`TRANSPORT_ANTHROPIC` branches. The existing
  `is_route_approved` guard at the top of `build_adapter` starts allowing the route once the
  catalog entry exists — remove the "direct OpenAI is not approved at MVP" assumption in the
  docstring.

## 5. Grounding / citation differences vs OpenRouter

OpenRouter returns a *normalized* Chat Completions payload with web-search annotations
(`openrouter_parser.py`). A direct OpenAI call uses OpenAI's own web-search/grounding surface
(the Responses API web-search tool), whose citation/annotation shape differs. The `openai_parser`
must normalize OpenAI's native annotations into the same `CitationResult` /
`SearchEventResult` / `AnswerEngineResponse` shapes the other parsers emit, so downstream
analysis + classification stay transport-agnostic:
- extract `search_used` + per-search `SearchEventResult`s from OpenAI's tool-call events;
- extract inline URL citations into `CitationResult` (ordinal, url, title, domain via
  `normalization.normalize_domain`, cited_text) — identical target shape to the Gemini/
  OpenRouter parsers, so the deterministic citation classifier is unchanged.
Where OpenAI exposes no equivalent field (e.g. char offsets), record `None` — never invent it.

## 6. Frontend (make the transport selectable)

The frontend already anticipates this: `frontend/lib/api/schemas.ts` splits an MVP
`transportProviderSchema = z.enum(['anthropic','google','openrouter'])` (used on the wire) from a
wider UI-only `uiTransportProviderSchema = z.enum([...,'openai'])`. Enabling the adapter means:
- add `'openai'` to the **wire** `transportProviderSchema` so the backend's `/provider-catalog` +
  `/provider-connections` responses (which will now include `openai`) pass `strictValidate`;
- once wire + UI schemas match, the UI-only widening becomes unnecessary — collapse to one
  schema (invariant 2, one owner) rather than keeping two in permanent drift.
- `/providers` then offers "OpenAI (direct)" as a BYOK connection + a selectable transport for
  the `chatgpt` engine route, alongside the existing OpenRouter option. Browser calls stay
  same-origin through the `/api/*` proxy (invariant 12).

## 7. Suggested build order + tests

1. `config/provider_catalog.py`: add `openai` to `MVP_TRANSPORTS`, the `chatgpt→openai` route,
   and the endpoint URL knob.
2. `openai_parser.py` — pure, deterministic; **table-tested** against recorded fixture payloads
   (grounded answer + a no-search answer), mirroring
   `test_gemini_parser_grounding_citations_and_provenance` /
   `test_openrouter_parser_chatgpt_provenance_and_citations` in
   `backend/tests/unit/test_answer_engine_adapters.py`. Assert the provenance triple
   (`chatgpt`/`openai`/`<model>`) and citation shapes.
3. `openai.py` adapter — payload builder (stateless, brand-free), `execute()` with mocked httpx,
   error mapping. Mirror `test_gemini_adapter_executes_and_records_provenance` /
   `test_gemini_adapter_maps_http_error` / `test_gemini_adapter_requires_key`.
4. `factory.py` branch + a test that `build_adapter(logical_engine='chatgpt',
   transport_provider='openai', ...)` returns the OpenAI adapter and that the route is now
   approved.
5. Frontend: widen the wire transport schema + `/providers` UI; connection-create/test flow.

## 8. Explicit non-goals

- **No change to deterministic scoring** (invariant 9) — this adapter only executes + normalizes;
  metrics remain a projection of persisted analysis (invariant 7).
- **No new logical engine** — `chatgpt` gains a second transport (`openai`) only; the
  logical/transport/model triple contract (invariant 10) is unchanged.
- **No new crypto, no new error taxonomy, no new contract** — reuse `encrypt_secret`,
  `errors.py`, and `contracts.py` (invariant 2).
- **Brand/competitor list is never sent to OpenAI** (invariant 6).

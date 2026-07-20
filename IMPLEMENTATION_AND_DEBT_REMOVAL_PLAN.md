# Searchify — Implementation & Debt Removal Plan (pending work only)

Updated 20 Jul 2026. Completed items removed — shipped in PR #9 (security hygiene: secrets
cleanup + detect-secrets baseline, python-multipart floor bump, CI workflow + pip-audit SCA,
worker heartbeat except-clause fixes) and PR #10 (all 90%-confidence dead-code flags, CI
pnpm/parser/baseline fixes). Remaining scope below, ordered by priority.

---

## Phase 0 — BUG (HIGH PRIORITY): Site Health crawl flow renders as disjointed screens

### Symptom (current, incorrect behavior)
1. User starts discovery → discovery-progress panel.
2. User cancels → panel is replaced by the discovered-URL selection list.
3. User selects a subset and starts a crawl → the panel **reverts to the same
   selection list** while the run is queued/starting.
4. When the crawl finishes → the panel **jumps to a separate dashboard view**.

Multiple full-panel swaps for what is one continuous flow; the user cannot tell
whether their action took effect or where they are in the lifecycle.

### Root cause (verified in code)
The flow is already a single route (`frontend/app/(app)/site-health/page.tsx`), but
`PhasePanel` (`frontend/components/site-health/phase-panel.tsx`) is a hard `switch` that
mounts **exactly one** of six full-panel components (`EmptyPhaseCard` / `DiscoveryProgress`
/ `InventorySelection` / `AnalysisProgress` / `TerminalPhaseCard` / `HealthDashboard`)
based on `resolveSiteHealthPhase` (`frontend/lib/site-health/status.ts:162`). Each phase
transition therefore unmounts the whole UI and mounts a different one:

- The "revert to the URL list" is precedence rule 9 (`starter + analysis pending →
  'selection'`) plus rule 6 (`cancelled + discovered URLs → 'selection'`): after the user
  clicks "Start analysis", the freshly created crawl re-resolves to `'selection'` (or
  `'discovering'`) until analysis actually starts, so the same list re-renders.
- The "jump to dashboard" is rules 2–4: the moment `score_summary` lands,
  `hasScoreData` flips the phase to `'dashboard'` and the entire panel is swapped.
- `recrawlStarting` in `use-site-health-screen.ts:117` already papers over one instance
  of this (retaining prior content behind a "starting" notice) — evidence the panel-swap
  model is fighting the product intent.

### Target design (expected behavior)
**One canonical dashboard screen** that always renders the same layout skeleton and
updates *data in place*; discovery/crawl/cancel are controls and status regions on that
screen, never navigations. Total screens in the flow: **3**.

1. **Canonical dashboard** (`/site-health`) — always shows:
   - Header with the primary action resolved from crawl state
     ("Start discovery" / "Cancel discovery" / "Start crawl" / "Cancel crawl" / "Re-crawl"),
     so start/pause/cancel is available at every point from the same place.
   - A **status/progress strip** (replaces `DiscoveryProgress` + `AnalysisProgress` as
     full panels): discovery counts while discovering, analysis progress while analyzing,
     run-outcome notice (`dashboardRunNotice`) when cancelled/partial/failed. This strip
     changes content, not the screen.
   - The **URL inventory/selection table** as a persistent section: read-only rows during
     discovery (rows stream in as they are found), checkbox-selectable once discovery
     stops (Starter), frozen/read-only while analyzing, enriched with scores as pages
     complete. `InventorySelection` and the pages table merge into this one region.
   - The **score/dashboard cards** (`HealthDashboard` content) as a persistent section:
     placeholder/skeleton until the first `score_summary` projection lands, then real
     data — appearing in place, not via a screen jump.
2. **Crawl detail view** — existing `/site-health/crawls/[crawlId]/pages/[siteUrlId]`
   (`url-detail.tsx`) stays as a separate screen.
3. **Issues screen** — existing `issues-screen.tsx` / `issues-catalog.tsx` stays.

### Implementation steps
1. **Reframe the phase model**: keep `resolveSiteHealthPhase` (the state resolution is
   sound and tested) but consume it as *section modifiers* (which controls are enabled,
   which strip content shows, whether the table is selectable) instead of a panel switch.
   Add a derived `primaryAction` + `sectionStates` view-model in `use-site-health-screen.ts`.
2. **Rebuild `PhasePanel` as a composed layout** (`SiteHealthDashboardLayout`): status
   strip + inventory section + score section, all always mounted. Delete the
   one-of-six `switch`; `EmptyPhaseCard`/`TerminalPhaseCard` become empty-states *inside*
   the sections (e.g. score cards show "Run a crawl to see scores").
3. **Fix the post-"Start analysis" bounce**: while `createMutation.isPending` or the new
   crawl has not yet reached `analysis running`, keep the selection table visible but
   frozen with an inline "Starting crawl…" state (generalize the existing
   `recrawlStarting` mechanism instead of special-casing it).
4. **Merge the two tables**: `InventorySelection` and the `AnalysisProgress` pages table
   render the same rows in different modes — unify into one inventory table component
   with `mode: 'discovering' | 'selectable' | 'frozen' | 'scored'`.
5. Keep polling/SSE wiring in `use-site-health-screen.ts` unchanged (polling-first model
   is orthogonal to this fix).
6. **Tests**: update `site-health-screen.test.tsx`, `inventory-selection.test.tsx`,
   `analysis-progress.test.tsx` for the composed layout; add a regression test for the
   exact reported sequence (discover → cancel → select → start crawl → finish) asserting
   the layout never unmounts between steps (e.g. stable test-id persists across all
   states) and the score section appears in place.

**Risk:** Medium (pure frontend; no API changes). **Estimate:** 2–3 days.

---

## Phase 2 (remaining) — Bug-risk fixes (HIGH)

### 2.3 Loose equality on null in frontend (`==`/`!=` → `===`/`!==`)
- `frontend/components/prompts/generate-prompts-dialog.tsx:168`
- `frontend/components/runs/executions-table.tsx:61`
- `frontend/components/setup/entry-list.tsx:111`
- `frontend/components/site-health/analysis-progress.tsx:62, 63, 64, 82`
- `frontend/lib/site-health/status.ts:138`

**Fix:** convert to strict equality; `value == null` is the only allowed loose form
(intentional null-or-undefined). Then enable `eqeqeq` as an **error** in ESLint.
Note: `analysis-progress.tsx` / `status.ts` sites may be reshaped by Phase 0 — do this
PR after (or as part of) the Phase 0 work to avoid churn.

### 2.4 Promise executor returns value (no-promise-executor-return)
- `frontend/components/site-health/url-detail.test.tsx:215`
- `frontend/e2e/helpers/real-stack.ts:87, 321`
- `frontend/lib/auth/session-guard.test.tsx:70, 92`
- `frontend/lib/theme.test.ts:52`

**Fix:** wrap in braces / call `resolve()` explicitly; verify the tests still assert
what they claim.

### 2.5 Constant-truthy assertion in test
- `frontend/lib/utils.test.ts:8` — assertion likely always passes. Rewrite.

### 2.6 Empty test stubs (S1186)
- `backend/tests/component/test_analysis_api.py:81`
- `backend/tests/component/test_analysis_http.py:45`
- `backend/tests/component/test_audit_worker.py:60`

**Fix:** intentional no-op fakes get a one-line comment; unwritten test bodies get
implemented or deleted.

---

## Phase 3 (remaining) — Dead code, medium-confidence (60%) — verify then remove

**Unused functions** (grep for dynamic usage/exports first):
- `backend/app/analysis/scoring.py:151` — `_any_alias_present`
- `backend/app/domain/site_health/discovery.py` — `monitored_hashes_for_project`,
  `compute_url_hash` (line numbers shifted by PR #10; re-locate)

**Unused local variables in scoring/selection logic:**
- `backend/app/analysis/site_health/scoring.py:76, 77, 90` — `passed_weight`,
  `failed_weight`, `dimensions` ⚠️ unused weight variables in a *scoring* module may mean
  the score formula is incomplete — verify intent, don't just delete
- `backend/app/domain/site_health/selection.py:134` — `workspace_limit` ⚠️ same concern:
  is a limit supposed to be enforced?
- `backend/app/domain/workspaces/service.py:14` — `WORKSPACE_ROLE_MEMBER`
- `backend/app/connectors/web_evidence/contracts.py:46` — `purpose`
- `backend/app/domain/analysis/schemas.py:70` — `audit_status`

**DO NOT auto-remove — SQLAlchemy model columns (schema change, needs migration):**
- `backend/app/models/audit.py:269` — `provider_route_snapshot`
- `backend/app/models/brand.py:70` — `brand_id`
- `backend/app/models/content.py:87` — `message_digest`
- `backend/app/models/provider.py:54, 121, 208` — `deactivation_reason` ×2, `parameters`
- `backend/app/models/site_health.py:327, 419, 564, 648, 890` — `first_seen_crawl_id`,
  `observed_url`, `source_task_id`, `target_host`, `supporting_artifact_ids`

Product decision per column: abandoned → Alembic migration in its own PR; otherwise
keep and suppress the finding.

---

## Phase 4 — Maintainability debt (MEDIUM)

### 4.1 Duplicated "not found" literals → shared error helper (S1192)
One helper (`raise_not_found(resource)` or `ResourceNotFound(resource)` mapped to 404):
- `backend/app/api/audits.py` — "Audit not found" ×5
- `backend/app/api/deps.py` — "Workspace not found" ×3
- `backend/app/api/projects.py` — "Project not found" ×6
- `backend/app/api/prompts.py` — "Project not found" ×3, "Prompt set not found" ×9
- `backend/app/domain/analysis/service.py` — "Audit not found" ×3
- `backend/app/domain/site_health/service.py` — "Crawl not found" ×3

Model-layer literals (`"audits.id"`, `"SET NULL"`, `"all, delete-orphan"` across
`models/*.py`): module-level constants. Low risk, mechanical, one PR.

### 4.2 Redundant exception classes (S5713)
- `backend/app/api/deps.py:50`
- `backend/app/connectors/web_evidence/url_policy.py:92`

### 4.2b Identical function implementations
- `frontend/lib/runs/status.ts:102` (`executionStatusLabel`) byte-identical to line 73
  (`auditStatusLabel`). Extract shared `titleCaseStatus`, keep the typed wrappers.
  (Also near-identical to `statusLabel` in `frontend/lib/site-health/status.ts:276` —
  fold all three into one shared helper.)

### 4.3 Float equality in tests → `pytest.approx` (S1244, ~40 instances)
Concentrated in: `test_analysis_api.py`, `test_analysis_http.py`, `test_audit_worker.py`,
`test_site_health_worker.py`, `test_analysis_scoring.py`, `test_site_health_scoring.py`
(bulk), `test_audit_guardrails.py`, `test_content_config.py`,
`test_discovery_models_mistral.py`, `test_site_health_rules.py`,
`test_visibility_projection_helpers.py`.

Mechanical: `assert x == 0.5` → `assert x == pytest.approx(0.5)`. Skip values exact by
construction (`0.0`, ints-as-float). One PR, tests only.

### 4.4 Complex functions — hotspot refactoring (40 flagged)
Named entry points confirmed against code:
- `backend/app/analysis/service.py:76` — `analyze_task`
- `backend/app/analysis/site_health/parser.py:443` — `extract_page_facts`
- `backend/app/analysis/site_health/structured_data.py:103` — `parse_jsonld_blocks`
- `backend/app/domain/audits/planner.py:239` — `create_audit`
- `backend/app/domain/site_health/service.py` — `presentation_status_for`, `get_issues`

Heat map for the rest: `site_health/service.py` (6 flagged — worst),
`site_health_worker.py` (3), `site_health/discovery.py` (2), provider parsers,
frontend screens (`site-health-screen.tsx`, `inventory-selection.tsx`, etc. — note
these are rebuilt by Phase 0 anyway; fold complexity reduction into that work).

**Approach:** boy-scout rule + dedicated refactor only for the top hotspot
(`site_health/service.py` + worker); add a complexity ratchet to CI (`radon`/`xenon`,
ESLint `complexity`).

### 4.5 Code duplication — targeted consolidation
- `backend/app/domain/site_health/service.py` — lines ~890–924 duplicate ~659–695
  (URL-listing filter/cursor/enrichment blocks). Extract shared helpers **as part of
  the 4.4 site_health refactor PR**.
- `backend/tests/component/test_site_health_worker.py` — repeated setup/parsing;
  consolidate into fixtures when next touched. Low priority.
- Frontend e2e `runs.spec.ts` / `content.spec.ts` / `providers.spec.ts` — extract shared
  helpers. Low risk.

Remaining ~100 duplicate groups: unusable (empty file paths in export) — re-scan first
(Phase 5).

---

## Phase 5 (remaining) — Guardrails

1. **CI lint gates:** promote `eqeqeq`, `no-promise-executor-return`,
   `no-constant-binary-expression` to errors; enable S2737/S1192/S1244 as blocking on
   new code. (Secrets scanning + pip-audit already shipped in PR #9.)
2. **Re-run duplicate-code analysis** with fixed scanner config (current export has
   empty file paths for 105 groups).
3. **Suppress triaged false positives** in scanner config: the 23 SAST FPs, the PEP 695
   `M` type-param use-before-define, the pydantic `@field_validator` "same return"
   blocker, Tailwind v4 at-rules, intentional `await`-in-loop sites (`client.ts` retry
   backoff, `use-crawl-events.ts` SSE reads, `real-stack.ts` polling).

---

## Excluded (deliberately not planned — unchanged rationale)

- `config/content.py:107` "always returns same value" blocker — pydantic validator
  contract; false positive.
- Docstring campaign (718 instances) — document public API surface only, as touched.
- All 23 SAST findings — triaged FPs (suppress via Phase 5.3).
- Style-only anti-patterns (`prefer-destructuring`, nested-ternary, etc.).
- CSS `@theme`/`@custom-variant` warnings — valid Tailwind v4 at-rules.
- `no-await-in-loop` — every flagged site is intentionally sequential.
- Test seeding helper with 22 params — refactor only if touched anyway.

---

## Suggested PR sequence (remaining)

| # | PR | Phase | Risk |
|---|----|-------|------|
| 1 | **Site Health single-dashboard flow redesign** | 0 | Med |
| 2 | Frontend strict equality + promise-executor + dead assertion (post-redesign) | 2.3–2.5 | Low |
| 3 | Empty test stubs triage | 2.6 | Low |
| 4 | Dead code removal (60% confidence, verified) — excl. ORM columns | 3 | Med |
| 5 | Not-found error helper + model FK constants + status-label dedup | 4.1–4.2b | Low |
| 6 | `pytest.approx` sweep | 4.3 | Low |
| 7 | site_health service/worker refactor + service.py dedup | 4.4–4.5 | Med–High |
| 8 | e2e/test-helper dedup (opportunistic) | 4.5 | Low |
| 9 | CI lint gates + scanner FP suppressions + duplicate re-scan | 5 | Low |

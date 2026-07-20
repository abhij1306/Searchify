# Searchify — Implementation & Debt Removal Plan (pending work only)

Updated 20 Jul 2026. Completed items removed — shipped in PR #9 (security hygiene: secrets
cleanup + detect-secrets baseline, python-multipart floor bump, CI workflow + pip-audit SCA,
worker heartbeat except-clause fixes), PR #10 (all 90%-confidence dead-code flags, CI
pnpm/parser/baseline fixes), and PR #11 (Phase 0 Site Health single-dashboard redesign:
canonical always-mounted layout with status strip + score section + inventory section,
project-scoped `crawlStarting`, first-page-only polling for cursor pages, plus the
Phase 2.3–2.5 lint fixes, 2.6 stub triage, Phase 3 verified dead-code removal, 4.1/4.2/4.2b
dedup helpers, the 4.3 `pytest.approx` sweep, and the Phase 5.1 CI lint gates).

The canonical Site Health screen's header primary action resolves to `start` / `cancel` /
`recrawl`; "Start analysis" is owned by the selection section of the inventory, not the
header. The per-URL crawl detail view and the issues screen remain the only other screens
in the flow.

Remaining scope below, ordered by priority.

---

## Phase 3 (remaining) — ORM columns flagged as unused (product decision required)

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

## Phase 4 (remaining) — Maintainability debt (MEDIUM)

### 4.4 Complex functions — hotspot refactoring (40 flagged)
Named entry points confirmed against code:
- `backend/app/analysis/service.py:76` — `analyze_task`
- `backend/app/analysis/site_health/parser.py:443` — `extract_page_facts`
- `backend/app/analysis/site_health/structured_data.py:103` — `parse_jsonld_blocks`
- `backend/app/domain/audits/planner.py:239` — `create_audit`
- `backend/app/domain/site_health/service.py` — `presentation_status_for`, `get_issues`

Heat map for the rest: `site_health/service.py` (6 flagged — worst),
`site_health_worker.py` (3), `site_health/discovery.py` (2), provider parsers.
(The frontend site-health screens were rebuilt by the PR #11 redesign.)

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

1. **Re-run duplicate-code analysis** with fixed scanner config (current export has
   empty file paths for 105 groups). (The CI lint gates — `eqeqeq`,
   `no-promise-executor-return`, `no-constant-binary-expression` as errors and
   `pnpm lint` in CI — shipped in PR #11; secrets scanning + pip-audit in PR #9.)
2. **Suppress triaged false positives** in scanner config: the 23 SAST FPs, the PEP 695
   `M` type-param use-before-define, the pydantic `@field_validator` "same return"
   blocker, Tailwind v4 at-rules, intentional `await`-in-loop sites (`client.ts` retry
   backoff, `use-crawl-events.ts` SSE reads, `real-stack.ts` polling).

---

## Excluded (deliberately not planned — unchanged rationale)

- `config/content.py:107` "always returns same value" blocker — pydantic validator
  contract; false positive.
- Docstring campaign (718 instances) — document public API surface only, as touched.
- All 23 SAST findings — triaged FPs (suppress via Phase 5.2).
- Style-only anti-patterns (`prefer-destructuring`, nested-ternary, etc.).
- CSS `@theme`/`@custom-variant` warnings — valid Tailwind v4 at-rules.
- `no-await-in-loop` — every flagged site is intentionally sequential.
- Test seeding helper with 22 params — refactor only if touched anyway.

---

## Suggested PR sequence (remaining)

| # | PR | Phase | Risk |
|---|----|-------|------|
| 1 | ORM-column triage (product decision + migrations where abandoned) | 3 | Med |
| 2 | site_health service/worker refactor + service.py dedup | 4.4–4.5 | Med–High |
| 3 | e2e/test-helper dedup (opportunistic) | 4.5 | Low |
| 4 | Scanner FP suppressions + duplicate re-scan | 5 | Low |

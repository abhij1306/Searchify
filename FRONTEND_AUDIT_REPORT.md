# Frontend Audit Report — Searchify

**Date:** 2026-07-18
**Tools:** [react-doctor v0.7.8](https://github.com/millionco/react-doctor) · [fallow](https://github.com/fallow-rs/fallow)
**Scope:** `frontend/` (Next.js App Router)

**Overall:** 37 react-doctor issues (2 security, 11 bugs, 9 perf, 15 maintainability) + 57 fallow dead-code issues, 8 clone groups. Fallow maintainability score: **90.9 (good)**. No circular dependencies, no boundary violations, no route collisions.

Raw JSON exports: `fallow-deadcode.json`, `fallow-dupes.json` (repo root).

---

## 🔴 Critical — Bugs (react-doctor)

| # | Issue | Location | Impact |
|---|---|---|---|
| B1 | Client-side redirect via `router.replace()` in `useEffect` | `frontend/app/(app)/page.tsx:34` | Flashes wrong page before redirecting; should use `redirect()` from `next/navigation` or handle server-side |
| B2 | `useSearchParams()` outside `<Suspense>` | `frontend/app/(app)/site-health/crawls/[crawlId]/pages/[siteUrlId]/page.tsx:18` | Forces entire page to client-side rendering |
| B3 | `useSearchParams()` outside `<Suspense>` | `frontend/app/(app)/visibility/page.tsx:42` | Same as B2 |
| B4 | `useMutation` with no cache invalidation | `frontend/components/layout/user-menu.tsx:34` | Users may see stale data after mutation runs |
| B5 | Array index used as React `key` | `frontend/components/visibility/mentions-citations.tsx:136` | Wrong rows shown/kept when list reorders or filters |
| B6 | Array index used as React `key` | `frontend/components/visibility/rankings-table.tsx:54` | Same as B5 |
| B7 | Array index used as React `key` | `frontend/components/visibility/visibility-trends.tsx:285` | Same as B5 |
| B8 | `toLocaleDateString()` during render (via `formatShortDate`) | `frontend/components/ui/history-drawer.tsx:110` | Hydration mismatch: server locale/timezone differs from client |
| B9 | State updates chained through effects | `frontend/lib/auth/session-guard.tsx:79` | Extra render per step; set related state together in the triggering handler |
| B10 | Effect re-subscribes on changing callback (`clearSession`) | `frontend/lib/auth/session-guard.tsx:94` | Effect tears down/re-subscribes whenever parent re-renders; wrap with `useEffectEvent` or stable ref |
| B11 | `preventDefault()` on `<form>` onSubmit | `frontend/components/prompts/prompt-form-dialog.tsx:81` | Form unusable without JS. Low priority for an authenticated SPA dialog — likely acceptable as-is |

## 🟠 Security (react-doctor)

| # | Issue | Location | Fix |
|---|---|---|---|
| S1 | Missing `minimumReleaseAge` | `frontend/pnpm-workspace.yaml` | Add `minimumReleaseAge: 10080` (7 days) to delay installs of freshly published (potentially malicious) versions |
| S2 | Missing `trustPolicy` | `frontend/pnpm-workspace.yaml` | Add `trustPolicy: no-downgrade` so pnpm rejects packages whose provenance/signature signals weaken between updates |

## 🟡 Performance (react-doctor)

| # | Issue | Location | Fix |
|---|---|---|---|
| P1 | `useState` (`hasObservedActiveRerun`) set but never rendered | `frontend/components/site-health/url-detail.tsx:75` | Use `useRef` — avoids a pointless re-render on every set |
| P2 | Pure function `readFileText` rebuilt every render | `frontend/components/prompts/csv-import-dialog.tsx:53` | Hoist to module scope |
| P3 | Pure functions rebuilt every render | `frontend/components/ui/trend-chart.tsx:92,97` | Hoist to module scope |
| P4 | `array.includes()` inside loop (O(n²)) | `frontend/components/prompts/prompt-toolbar.tsx:87` | Use a `Set` |
| P5 | `array.includes()` inside loop | `frontend/components/runs/launch-dialog.tsx:173` | Use a `Set` |
| P6 | `array.includes()` inside loop | `frontend/lib/prompts/filter.ts:40` | Use a `Set` |
| P7 | Chained `.filter().map()` double passes ×5 | `components/layout/nav-items.ts:75`, `lib/prompts/csv.ts:192`, `lib/setup/forms.ts:105`, `lib/site-health/selection.ts:60`, `lib/visibility/evidence.ts:56` | Single-pass `for...of`/`reduce`; micro-optimization, only worth it on hot paths |

## ⚪ Dead code (fallow — 57 issues)

### Unused files (2)
- `frontend/components/ui/index.ts` — barrel nothing imports
- `frontend/lib/api/index.ts` — barrel nothing imports

### Unused exports (24)

| File | Line | Export |
|---|---|---|
| `components/layout/nav-items.ts` | 74 | `LIVE_ROUTES` |
| `components/ui/card.tsx` | 84 | `CardFooter` |
| `components/ui/input.tsx` | 9 | `textareaClasses` |
| `components/ui/score-band.ts` | 22 | `scoreBandFill` |
| `components/ui/typography.tsx` | 37 | `Subtitle` |
| `lib/api/client.ts` | 224 | `isAbortError` |
| `lib/api/providers.ts` | 94 | `isTestOk` |
| `lib/api/providers.ts` | 101 | `logicalEngines` |
| `lib/api/schemas.ts` | 209 | `providerCatalogRouteSchema` |
| `lib/api/schemas.ts` | 216 | `providerCatalogEngineSchema` |
| `lib/api/schemas.ts` | 776 | `ruleEvaluationSchema` |
| `lib/api/schemas.ts` | 794 | `linkReferenceSchema` |
| `lib/theme.ts` | 7 | `THEME_TRANSITION_ATTR` |
| `lib/visibility/dashboard.ts` | 23 | `DASHBOARD_STATUSES` |
| `lib/visibility/dashboard.ts` | 44 | `DEFAULT_TAB` |
| `lib/visibility/dashboard.ts` | 61 | `PROMPT_TYPE_OPTIONS` |
| `lib/visibility/dashboard.ts` | 75 | `DEFAULT_FILTERS` |
| `lib/visibility/dashboard.ts` | 121 | `formatRunLabel` |
| `lib/visibility/trends.ts` | 15 | `engineLabel` |
| `lib/visibility/trends.ts` | 16 | `PLACEHOLDER` |
| `lib/visibility/trends.ts` | 65 | `formatPointLabel` |
| `lib/visibility/trends.ts` | 79 | `metricValue` |
| `lib/visibility/trends.ts` | 119 | `versionChangeNote` |
| `lib/visibility/trends.ts` | 148 | `brandMentionCount` |

### Unused exported types (29)
Concentrated in `lib/api/types.ts` (e.g. `Competitor`, `ProviderRoute`, `AuditEngineSnapshot`, `Citation`, `SiteHealthPlan`, `SiteHealthAccessMode`, `SiteCrawlTaskStatus`, `SiteUrlSource`, `SiteScoreSummary`, …), `lib/api/site-health.ts` (`CreateCrawlInput`, `CrawlListParams`, `IssueDetailParams`, `IssueHistoryParams`, `ReplaceMonitoredInput`), and `lib/api/providers.ts` (`ProviderConnectionInput`). Full list in `fallow-deadcode.json` → `unused_types`.

### Dependencies
- Unused devDependency: `eslint-config-next` (`frontend/package.json:48`)
- `lib/api/client.ts` is ~14% dead code per fallow's health pass

## 🔵 Duplication (fallow — 8 clone groups)

| # | Clone A | Clone B | Notes |
|---|---|---|---|
| D1 | `app/(auth)/login/page.tsx:38-77` | `app/(auth)/register/page.tsx:37-76` | ~40-line shared auth mutation block — extract shared hook/component |
| D2 | `components/site-health/inventory-selection.tsx:278-295` | `components/site-health/url-detail.tsx:431-450` | Shared flex layout block |
| D3 | `components/visibility/fanout-evidence.tsx:45-63` | `components/visibility/mentions-citations.tsx:38-56` | Identical props/header block |
| D4 | `components/visibility/rankings-table.tsx:35-49` | `components/visibility/visibility-trends.tsx:270-284` | Shared empty-state markup |
| D5 | `components/visibility/rankings-table.tsx:54-68` | `components/visibility/visibility-trends.tsx:299-313` | Shared table row markup |
| D6 | `components/visibility/visibility-dashboard.tsx:282-306` | `components/visibility/visibility-overview.tsx:70-94` | Shared return/layout block |
| D7 | `lib/api/schemas.ts:396-428` (`rankingRowSchema`) | `lib/api/schemas.ts:945-983` (`visibilityTrendRankingRowSchema`) | Near-identical Zod schemas — unify |
| D8 | `lib/api/schemas.ts:601-611` | `lib/api/schemas.ts:764-773` | Shared nullable-summary schema fields |

## 🏗️ Complexity hotspots (fallow refactoring targets)

| Priority | File | Finding |
|---|---|---|
| 25.8 | `lib/api/query-keys.ts` | 25 dependents amplify every change — consider splitting (effort: high, confidence: medium) |
| 22.2 | `lib/api/providers.ts` | 67% dead — remove 2 unused exports (quick win) |
| 20.7 | `components/site-health/site-health-screen.tsx` | Cognitive complexity 62 in 302-LOC component — extract subcomponents |
| 17.7 | `components/site-health/inventory-selection.tsx` | Cognitive complexity 36 in 322-LOC component |
| 17.3 | `components/visibility/visibility-dashboard.tsx` | Cognitive complexity 55 in 309-LOC component |

Top risk files by fallow health (untested complexity / CRAP): `lib/api/site-health.ts`, `app/(auth)/register/page.tsx`, `site-health-screen.tsx`, `issues-catalog.tsx`, `lib/api/client.ts`, `visibility-dashboard.tsx`.

## Maintainability (react-doctor, remaining)

- Non-component exports in component files (breaks Fast Refresh): `lib/auth/session-guard.tsx:112,121`, `lib/project/project-context.tsx:128,137`

---

## Suggested execution plan

1. **Critical (this pass):** B1–B10, S1–S2, P1
2. **Mechanical refactor (delegated):** dead-code removal (most auto-fixable via `npx fallow fix`), unused types, `eslint-config-next` removal, duplication extraction D1–D8, P2–P7
3. **Structural (separate PRs):** complexity extraction for `site-health-screen`, `visibility-dashboard`, `inventory-selection`; `query-keys.ts` split

Re-run checks: `npx react-doctor@latest --verbose` (in `frontend/`) and `npx fallow` after each batch.

---

## ✅ Critical-pass outcome (2026-07-18)

react-doctor: **37 → 27 issues** (bugs 11 → 4, security 2 → 0). Typecheck clean, all 328 vitest tests pass.

**Fixed:**
- **B1** — `LandingRedirect` now calls `redirect()` during render instead of `router.replace()` in an effect (`app/(app)/page.tsx`)
- **B2/B3** — `<UrlDetail>` and `<VisibilityDashboard>` (both read `useSearchParams`) wrapped in `<Suspense>` at their page boundaries
- **B5** — mention badges keyed by `kind-name-first_offset` instead of array index (`mentions-citations.tsx`)
- **B6/B7** — ranking rows keyed by `brand|competitor-name` instead of index (`rankings-table.tsx`, `visibility-trends.tsx`)
- **B8** — `formatShortDate` moved into a post-mount `<ShortDate>` component so server/client locale can't diverge (`history-drawer.tsx`)
- **P1** — `hasObservedActiveRerun` converted from `useState` to `useRef` (never rendered; rerun-polling tests still pass) (`url-detail.tsx`)
- **S1/S2** — `pnpm-workspace.yaml` hardened: `minimumReleaseAge: 10080`, `trustPolicy: no-downgrade`, `trustPolicyIgnoreAfter: 525600` (pre-provenance-era releases), pinned `trustPolicyExclude` for `undici-types@6.21.0` and `eslint-import-resolver-typescript@3.10.1`. Lockfile rebuilt under the new policy (several packages resolved to older, aged versions).

**Verified false positives / intentional (left as-is — do NOT "fix" in the mechanical pass):**
- **B4** (user-menu mutation invalidation) — `onSettled: clearSession` already calls `queryClient.clear()`, which supersedes any per-key invalidation
- **B9** (session-guard chained state) — the 401-from-`me` effect is reacting to external query state, not chaining local state; the `redirectingRef` latch is deliberate
- **B10** (effect re-subscribe on `clearSession`) — `clearSession` is a `useCallback` over stable deps (`queryClient`, `router`), so the QueryCache watchdog does not actually re-subscribe in practice
- **B11** (preventDefault in prompt-form-dialog) — authenticated SPA dialog; server-action form is an architecture change, not a bug fix

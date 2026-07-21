# Handoff — Marketing Site Expansion (2026-07-21)

> Temporary handoff doc — **delete before merging PR #12**. Context: session credits ran out mid-verification; all implementation is complete and pushed. Only e2e/docs/review-cycle steps remain.

## Status snapshot

- **Branch:** `vorflux/marketing-landing-page` → PR https://github.com/abhij1306/Searchify/pull/12
- **Post-rewrite expansion commit:** `467ac30` — "Expand marketing site: 5-item nav, 7 new pages, multi-column footer"; temporary handoff added at `47c8017`.
- **Approved plan:** `/code/.plans/v1-marketing-site-expansion.md` (+ `-summary.md`); **approved mockups:** `/code/.plans/designs/chrome-nav-footer.html` (authoritative for nav/footer) + `page-{pricing,enterprise,solutions,blog,blog-post,compare,compare-detail,faq}.html` (content below nav only).

## Done and verified

| Item | State |
|---|---|
| Nav: Product/Resources/Solutions dropdowns + Enterprise/Pricing links, 1080px compaction tier, mobile accordions | ✅ + `landing-nav.test.tsx` (5 tests) |
| Chrome (aurora/grain/nav/footer) moved to `(marketing)/layout.tsx`; anchors absolute (`/#features`…) | ✅ |
| `/pricing` (4 tiers + comparison table), `/enterprise`, `/solutions` (4 anchored segments), `/blog` + `/blog/[slug]`, `/compare` + `/compare/[competitor]`, `/faq` (5 groups, native details/summary) | ✅ all sync RSC, SSG/prerendered |
| Typed content modules `frontend/lib/marketing-content/{social,pricing,blog,compare,faq}.ts` | ✅ canonical content owner; blog/comparison collections are empty until reviewed content is approved |
| Multi-column footer + social row (GitHub real, others `#`) + legal row | ✅ + `landing-footer.test.tsx` (4 tests) |
| `pnpm format`, `tsc --noEmit`, `pnpm lint` (0 warnings), `pnpm check:policy` (3 guards) | ✅ green |
| `pnpm test` | ✅ **496/496** (60 files) |
| `pnpm build` | ✅ `/blog`, `/compare` static; `/blog/[slug]`, `/compare/[competitor]` SSG; all others prerendered |

## Pending (in order)

1. **Rewrite `frontend/e2e/landing-nav.spec.ts`** for the new nav (it still asserts the OLD 2-dropdown structure and **will fail** until rewritten): `drop-product` opens with 9 menuitems (6 features + 3-step how-it-works group), `drop-resources` 4, `drop-solutions` 4; mobile 375px accordions `acc-product`/`acc-resources`/`acc-solutions`; KEEP the unchanged cases (theme persistence, no-dark-leak-to-`/login`, evidence-row, scrolled class).
2. **New `frontend/e2e/marketing-pages.spec.ts`**: anonymous 200 + exactly one visible h1 for `/pricing`, `/enterprise`, `/solutions`, `/blog`, `/compare`, `/faq`; blog/comparison slugs 404 while their reviewed content collections are empty; nav dropdown opens from a subpage (`/pricing`); footer has 5 column headings plus real GitHub and MIT License links; dark-first theme holds on `/pricing`. Run: `pnpm exec playwright test e2e/` (playwright.config auto-starts dev server).
3. **Docs route map** (`docs/frontend-architecture.md` §2): add rows for all 8 new routes (marketing group, MVP); update the `/` row (chrome now in the group layout).
4. **Review cycle** (per repo workflow): review changes from expansion commit `467ac30`, including visual validation of the new pages vs mocks.
5. **Browser spot-check** (agent-browser, anonymous — logout first via `/api/v1/auth/logout` fetch if redirected): 3 dropdowns on desktop, mobile menu, one content page per type, footer. Preview: https://asbldnegklid.preview.us1.vorflux.com (dev server job `8c99b6a9` on :3000; `next.config.ts` `allowedDevOrigins` change already applied + restarted).
6. **PR body rewrite** (wholesale, preserve Session Details + CodeAnt sections verbatim — fetch body via credential-helper token, see git-credential trick below) + **test report resubmit** same title "Searchify landing page E2E verification" (→ version 4) + final commit/push.
7. **Fill real data** in `frontend/lib/marketing-content/*.ts` (search `[TODO(user)]`): prices, blog post, comparison columns/verdicts, social URLs, contact email, billing FAQ answers.

## Gotchas (hard-won, do not rediscover)

- **ALL conditional class lists via `cn()`** — prettier-plugin-tailwindcss silently strips leading spaces in conditional template literals. Sweep after any format: `grep -rn '\${[a-zA-Z][^}]*? '"'" "frontend/app/(marketing)" frontend/components/marketing` → expect clean.
- **No raw hex** in `app|components|lib` `.ts/.tsx` (guard); all marketing CSS lives in `frontend/app/(marketing)/marketing.css` under `.mkt` scope. New pages' CSS was merged there in marked sections; the shared content-page block (`.page-hero`/`.list-head`/`.todo-tag`/`.back-link`) is canonical — JSX uses the `.hero-inner`/`.hero-sub` dialect everywhere.
- **Pages must stay sync RSC** except the two dynamic templates (thin async wrapper + sync view: `BlogPostView`, `CompareDetailView`).
- **Exactly one h1 per page; no h2–h6 may contain "Searchify"** (test-enforced).
- Staged-CSS originals from the build wave remain at `/var/tmp/marketing-css-sections/*.css` (already merged; reference only).

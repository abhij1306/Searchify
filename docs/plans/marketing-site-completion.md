# Marketing Site Completion Plan

> Target: `vorflux/marketing-landing-page` / PR #12. Derived from
> `HANDOFF_MARKETING_SITE.md` and validated against the checked-out branch on
> 2026-07-21.

## Current state

The marketing implementation is present: the shared marketing layout and chrome,
landing page, pricing, enterprise, solutions, blog, comparison, and FAQ routes all
exist, and the handoff records green unit, type, lint, policy, and build gates.
Remaining work is launch content, stale/new browser coverage, route documentation,
visual/review passes, and PR cleanup.

The authoritative post-rewrite expansion commit is `467ac30`, with the temporary
handoff added at `47c8017`. User-fillable marketing content is owned by
`frontend/lib/marketing-content/*`.

## Completion standard

PR #12 is ready to merge when:

- all public routes render anonymously with one visible `h1`, correct metadata,
  working shared navigation/footer, and intentional 404s for unknown dynamic slugs;
- no visitor-facing `[TODO(user)]`, empty contact target, or `#` placeholder link
  remains;
- pricing, support, competitor, and other commercial claims have explicit owner
  approval and are not guessed from third-party material;
- desktop, compaction, and mobile layouts work in both themes, with keyboard and
  reduced-motion behavior preserved;
- unit, type, lint, policy, build, and Playwright gates are green; and
- the temporary handoff is deleted, PR documentation reflects the final scope, and
  all required GitHub checks/review threads are resolved.

## Phase 1 — Finalize launch content

1. Collect owner-approved values for:
   - Starter/Pro prices, quotas, project limits, support, Enterprise volumes/SLA;
   - public contact email and LinkedIn, X/Twitter, YouTube, and Instagram URLs;
   - Privacy and Terms URLs;
   - the first blog post's date, author, read time, and body;
   - billing FAQ answers; and
   - first-party Profound, Otterly AI, Scrunch AI, and Peec AI comparison data,
     review date, balanced narratives, and verdicts.
2. Keep fillable copy in `frontend/lib/marketing-content/*`. Move the remaining
   visitor-facing values currently embedded in `enterprise.tsx`,
   `compare-detail.tsx`, and `landing-footer.tsx` into that content owner before
   filling them, so content is not split across rendering components.
3. Replace all launch placeholders and wire contact/legal/social CTAs to their real
   destinations. Do not invent prices, SLAs, or competitor claims.
4. Update placeholder-specific Vitest assertions to verify the approved values and
   links instead of placeholder counts.
5. Run `rg -n "TODO\\(user\\)|\\[TODO|href=[\"']#[\"']"` over the marketing
   routes, components, and content modules. Any remaining match must be a deliberate
   non-visitor-facing comment with an owner and follow-up.

## Phase 2 — Finish browser contracts

1. Rewrite `frontend/e2e/landing-nav.spec.ts` for the expanded navigation:
   - Product exposes 9 menuitems across features and How it works;
   - Resources exposes 4 menuitems;
   - Solutions exposes 4 menuitems;
   - each desktop dropdown supports hover/focus open and Escape close;
   - the 375 px menu exposes Product, Resources, and Solutions accordions with the
     matching link counts; and
   - retain theme persistence, marketing-theme reset on `/login`, evidence-row
     non-overlap, and scrolled-nav cases.
2. Add `frontend/e2e/marketing-pages.spec.ts` covering:
   - successful anonymous renders and exactly one visible `h1` for `/pricing`,
     `/enterprise`, `/solutions`, `/blog`, `/compare`, and `/faq`;
   - 404 behavior for blog and competitor slugs while their content arrays remain
     intentionally empty, plus unknown slugs after content is published;
   - a dropdown opening from `/pricing` to prove absolute/shared navigation;
   - five footer column headings, final real social/contact/legal links, GitHub, and
     MIT License; and
   - dark-first behavior on a marketing subpage.
3. Add a 1080 px compaction assertion if the responsive tier is not already pinned
   by component tests. Prefer behavior/visibility assertions over pixel snapshots;
   keep the existing bounding-box assertion only where overlap is the regression.
4. Run the two marketing specs with retries disabled locally once to expose flakes,
   then run the normal configured suite.

## Phase 3 — Align documentation

Update the route map in `docs/frontend-architecture.md`:

- revise `/` to state that shared marketing chrome is owned by
  `app/(marketing)/layout.tsx`;
- add `/pricing`, `/enterprise`, `/solutions`, `/blog`, `/blog/[slug]`, `/compare`,
  `/compare/[competitor]`, and `/faq` as public MVP marketing routes; and
- note that unknown dynamic slugs resolve through `notFound()` and that authenticated
  `/` visitors retain the `/visibility` or `/setup` redirect contract.

## Phase 4 — Verify the final implementation

Run from `frontend/`, in this order:

```bash
pnpm test -- components/marketing app/\(marketing\)
pnpm exec tsc --noEmit
pnpm lint
pnpm check:policy
pnpm build
pnpm exec playwright test e2e/smoke.spec.ts e2e/landing-nav.spec.ts e2e/marketing-pages.spec.ts
pnpm exec playwright test e2e/
```

After formatting, re-run the conditional-class regression sweep described in the
handoff and `pnpm check:policy`. Confirm the production build reports `/blog/[slug]`
and `/compare/[competitor]` as generated routes and all other marketing routes as
prerendered.

## Phase 5 — Visual and accessibility pass

Browser-check anonymous sessions at 1440 px desktop, the 1080 px compact tier, and
375 px mobile in dark and light themes:

- landing hero/dashboard, all three dropdowns, mobile menu/accordions, and footer;
- one representative page for pricing/table, enterprise, solutions/anchors,
  blog/index+detail, compare/index+detail, and FAQ/details;
- no clipping, horizontal overflow, overlapping evidence/footer rows, layout shift,
  or unreadable placeholder copy;
- visible focus, truthful `aria-expanded`, Escape behavior, correct external-link
  targets, and usable native FAQ disclosure controls; and
- reduced-motion and marketing-theme cleanup when navigating to `/login`.

Fix any defect found, then repeat its focused check and add regression coverage when
the failure could recur unnoticed.

## Phase 6 — Review and PR handoff

1. Run independent simplification, code-review, and test/visual-review passes over
   `main...HEAD`; resolve valid findings and document intentional exceptions.
2. Re-run affected gates after every review fix and confirm PR #12 has no failing or
   unresolved required checks.
3. Rewrite the PR body to describe the final route surface, content status, test
   results, and browser evidence while preserving the existing Session Details and
   CodeAnt sections required by the handoff.
4. Submit the updated “Searchify landing page E2E verification” report as version 4
   if that reporting channel is still required.
5. Delete `HANDOFF_MARKETING_SITE.md`, verify the temporary preview-only assumptions
   are not required for production, and perform a final `git diff --check` plus clean
   status review (apart from the user's unrelated `.claude/` directory).
6. Commit and push the completion changes, wait for GitHub checks, then mark PR #12
   merge-ready.

## External inputs and blockers

The only expected product blocker is owner-approved launch content. Engineering can
complete docs, E2E scaffolding, and review preparation in parallel, but final assertions
and merge readiness should use the approved content rather than the current visible
placeholders. The marketing work requires no backend schema or API changes.

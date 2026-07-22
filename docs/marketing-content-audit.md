# Marketing content audit — pre-launch

> Scope: the public marketing surface (`app/(marketing)`, `components/marketing`,
> `lib/marketing-content`). Priorities: **P0** = applied in Phase A (GitHub/open-source/MIT
> removal — the repo goes private), **P1** = pre-launch, **P2** = backlog. Only P0 edits have
> landed in code; P1/P2 rows are suggestions only.
>
> Semantic rule applied for P0: links to our repo, open-source framing, and MIT-license rows
> are removed. Self-host stays as a deployment option (reworded, MIT dropped). GitHub as a
> competitor product would stay — none exists (`COMPETITORS` is empty).

## Hero (landing `/`)

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Subcopy leans on "scored deterministically" jargon before the visitor knows the product | Keep — it is the category differentiator, but consider a plainer first sentence for cold traffic | P2 |
| Ghost CTA "See how it works" jumps mid-page; no product-tour alternative | Keep anchor; pair with the product visual's `id="product"` entry point if a tour ever ships | P2 |

No P0 items — the hero never referenced the repo, open source, or MIT.

## Product visual

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Fictional "Acme Corp" dashboard (68/100, 240 runs, ▲ 12 pts) reads as a real customer result | Add an "illustrative data" microcopy tag, or swap to the visitor's own sample crawl once signup flows allow | P1 |
| Static panels can't be drilled into | Link the visual to `/register` or a live demo workspace when one exists | P2 |

No P0 items.

## Engine strip

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| "Measured where answers happen" + three engine chips is bare — no per-engine grounding | Add a one-line caption that one audit runs the same prompts across all three (mirrors the FAQ answer) | P2 |

No P0 items.

## Features grid

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Six cards, all peers — no visual ranking of what matters most | Mark the evidence explorer / deterministic scoring cards as the lead pair once analytics show which converts | P2 |

No P0 items.

## How-it-works

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Step 02 says runs execute "on your own API keys" — BYOK is a pricing fact visitors may not expect | Cross-link the BYOK strip on `/pricing` from this step | P2 |

No P0 items.

## Evidence band

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| The drill-down ("drills to run #1,247") is fictional but formatted like a real run id | Keep the device; label the visual as a sample, same fix as the product visual | P1 |

No P0 items.

## Final CTA (landing)

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Only conversion path is `/register` — no "talk to us" escape for larger teams | Add an Enterprise ghost link only if sales-led demand appears; current single-primary hierarchy is intentional | P2 |

No P0 items.

## Footer

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Brand line read "Open-source AI visibility and site intelligence platform." | **Applied:** now "AI visibility and site intelligence platform." | P0 |
| Resources column linked "Documentation" to the repo; Company column linked "GitHub" | **Applied:** both links removed; all footer links stay on-site | P0 |
| Legal row linked "MIT License" to the repo LICENSE blob | **Applied:** anchor removed; legal row is "© 2026 Searchify · A CUBE27 product" | P0 |
| Social row rendered a GitHub chip from `SOCIAL_LINKS` | **Applied:** `.social-row` renders only when `SOCIAL_LINKS.length > 0`; array is empty | P0 |
| No legal pages at all (Privacy/Terms absent) | Add `/privacy` + `/terms` before launch; legal row has room for two more links | P1 |
| `CONTACT_EMAIL` empty — the Contact link is hidden | Set the public contact email in `lib/marketing-content/social.ts` | P1 |

## Pricing + comparison table

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Enterprise feature read "Self-host option — MIT-licensed codebase, Docker Compose quick start" | **Applied:** "Self-host option — Docker Compose deployment inside your network" | P0 |
| Support row, Free cell read "Community — GitHub issues" | **Applied:** "Community — [TODO(user)]" (community channel TBD) | P0 |
| Starter/Pro prices are "Custom", Enterprise is "Custom · annual agreement" — no real numbers | Fill prices + cadences in `lib/marketing-content/pricing.ts`; decide whether "Custom" or real numerals convert better | P1 |
| Free cell for Support is now an unresolved placeholder | Decide the free-tier community channel (chat, forum, email) and replace `[TODO(user)]` | P1 |
| Tier CTAs all route to `/register` — no sales path from the table itself | Add "Contact sales" as the Enterprise card CTA once `CONTACT_EMAIL` exists (currently routes to `/enterprise`) | P2 |

## Enterprise

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Hero subcopy ended "self-hosted from the MIT-licensed codebase." | **Applied:** "self-hosted inside your network." | P0 |
| Hero ghost CTA "View the codebase" linked to the repo | **Applied:** now an internal "Compare plans" link to `/pricing` | P0 |
| Trust chip read "MIT open source" | **Applied:** Sigma icon + "Deterministic scoring" | P0 |
| Ops pillar "Self-host & openness" claimed "Audit every line — full source on GitHub" | **Applied:** pillar is now "Self-host & control" with "Versioned scoring rules — every projection traces to persisted evidence" | P0 |
| Self-hosted deploy card linked "Full source on GitHub" / "MIT license" | **Applied:** links array deleted; blurb is "The same platform, inside your network." | P0 |
| Capabilities band said claims map "to the open-source codebase" | **Applied:** claims map "to the running platform" | P0 |
| Verdict cited "an MIT-licensed codebase" | **Applied:** "a self-hostable platform, deterministic scoring, and evidence your team can audit line by line" | P0 |
| Final ghost CTA "Read the architecture docs" derived from the repo URL | **Applied:** now "Read the FAQ" (`/faq`); `DOCS_URL` deleted | P0 |
| "Contact sales" falls back to `href="#"` while `CONTACT_EMAIL` is empty | Set the contact email (P1 inventory); until then the primary CTA is a dead anchor | P1 |
| Custom-limits dials all render "Custom" | Fill real ranges (runs, URLs, projects, seats, retention, SLA) for the sales deck, even if the page keeps "Custom" | P1 |
| Security section invites a review but offers no packet | Prepare a security/architecture one-pager to attach to sales replies | P2 |

## Solutions

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Founders mapping read "MIT open source — self-host when you outgrow the cloud" | **Applied:** "Self-host when you outgrow the cloud — Docker Compose, inside your network" | P0 |
| Segment panels reuse the fictional Acme dataset | Label as illustrative, same fix as the landing product visual | P1 |
| All four segment CTAs route to `/register` with different labels | A/B the labels against a single "Get started" once traffic exists | P2 |

## Blog

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Empty-state body pointed readers at "the repo on GitHub" | **Applied:** now "the best way to follow along is to register and try the product yourself" | P0 |
| Hero subcopy read "evidence-first, open source, and straight from the team…" | **Applied:** "evidence-first, and straight from the team building Searchify." | P0 |
| Both ghost CTAs were external "Star on GitHub" buttons | **Applied:** internal "Read the FAQ" (`/faq`) links | P0 |
| Trust chip read "Open source · MIT" | **Applied:** Sigma icon + "Deterministic scoring" | P0 |
| `POSTS` is empty — the index renders only the designed empty state | Write the first posts (AEO, evidence-first scoring, measuring AI visibility per the new empty-state promise) | P1 |
| Featured-post cover slot renders "[post cover image — user supplied]" | Supply cover art with the first post | P1 |

## Compare

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Fairness point led with "Open source, MIT — scoring rules anyone can audit" | **Applied:** "Deterministic scoring — versioned rules over persisted evidence" | P0 |
| Fact row was "Source · MIT · self-host with Docker Compose" | **Applied:** "Deployment · Cloud or self-host with Docker Compose" | P0 |
| Honesty band claimed claims "can be checked against the code" (open source) | **Applied:** claims now "traced to persisted evidence"; band keeps the unverified-cell promise | P0 |
| Detail-page Searchify column had an "Open source / self-host" dimension and "Open source under MIT" price cell | **Applied:** dimension is "Self-host"; price cell is "Free self-host option on your own infrastructure — hosted plans [TODO(user)]" | P0 |
| Detail narrative hint listed "open source" as a talking point | **Applied:** hint dropped it | P0 |
| Final ghost CTAs were external "Star on GitHub" buttons | **Applied:** internal "Read the FAQ" (`/faq`); blog-post ghost is "All posts" | P0 |
| `COMPETITORS` is empty — the index shows only the research-in-progress note | Research Profound, Otterly, Scrunch, Peec first-party; fill rows, taglines, verdicts | P1 |
| Detail pages hard-code "Last reviewed · [TODO(user): date]" | Stamp the review date when each competitor ships | P1 |
| Detail hero says the column comes "straight from our docs and source code" | Reword to "our docs and the running platform" when a competitor page first ships (no competitor page renders today, so not P0) | P2 |

## FAQ

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| Whole "Open source" group (3 items) leaned on the public repo | **Applied:** group removed; `faq-open-source` anchor removed | P0 |
| "What is Searchify?" opened with "open-source AI visibility…" | **Applied:** "an AI visibility and site intelligence platform" | P0 |
| Cost answer cited "open source under the MIT license" | **Applied:** "Self-hosting the full stack is free on your own infrastructure — you pay only your own AI provider usage on your own keys." | P0 |
| Self-host answer pointed at the repo README quick start | **Applied:** reworded and moved under Account & billing — "delivered under the Enterprise plan — contact us for access" | P0 |
| Page metadata + hero line said "…and the open-source bits." | **Applied:** "…and self-hosting." (both occurrences) | P0 |
| Refund/invoice answers defer to "hosted-plan availability" | Publish real refund + invoice terms with hosted plans | P1 |
| "Contact us for access" has no mailto while `CONTACT_EMAIL` is empty | Set the contact email (P1 inventory) | P1 |

## Page metadata / OG

| Current copy issue | Concrete suggestion | Priority |
| --- | --- | --- |
| `/pricing` description called Searchify "the open-source AI visibility…platform" | **Applied:** "the AI visibility and site intelligence platform" | P0 |
| `/blog` description said "evidence-first, open source, and straight from the team…" | **Applied:** "evidence-first, and straight from the team building Searchify." | P0 |
| `/enterprise` description ended "cloud or self-hosted under MIT." | **Applied:** "cloud or self-hosted inside your network." | P0 |
| `/compare/[competitor]` description listed "open source" as a dimension | **Applied:** "BYOK privacy, and site-health auditing" | P0 |
| No `metadataBase` and no `openGraph.images` anywhere (noted in every page file) | Add both once the production domain exists; OG images must be absolute URLs | P1 |
| Twitter cards are all `summary` | Commission a `summary_large_image` OG asset with the brand | P2 |

## `[TODO(user)]` placeholder inventory

Everything below renders verbatim (or hides a link) until the user fills it in:

| Placeholder | Location | Blocks |
| --- | --- | --- |
| Starter / Pro / Enterprise prices + cadences ("Custom") | `lib/marketing-content/pricing.ts` → `/pricing` tier cards | Launch pricing |
| Support row, Free cell: "Community — [TODO(user)]" | `lib/marketing-content/pricing.ts` → `/pricing` table | Launch pricing |
| Enterprise limit dials ("Custom" × 6) | `components/marketing/enterprise.tsx` (`LIMIT_CELLS`) | Sales conversations |
| Hosted plans cell: "hosted plans [TODO(user)]" | `lib/marketing-content/compare.ts` (`SEARCHIFY_COLUMN`) | Compare pages |
| `COMPETITORS` array (empty) + per-competitor tagline, rows, verdict | `lib/marketing-content/compare.ts` → `/compare`, `/compare/[slug]`, footer Compare column | Compare launch |
| "Last reviewed · [TODO(user): date]" chips + narrative/verdict frames | `components/marketing/compare-detail.tsx` | Compare launch (renders only once a competitor exists) |
| `POSTS` array (empty) + per-post fields, cover image slot | `lib/marketing-content/blog.ts`, `components/marketing/blog.tsx` | Blog launch |
| `SOCIAL_LINKS` (empty — social row hidden) | `lib/marketing-content/social.ts` → footer | Launch |
| `CONTACT_EMAIL` (empty — footer Contact link hidden; Enterprise "Contact sales" falls back to `#`; FAQ "contact us" has no target) | `lib/marketing-content/social.ts` | Enterprise/sales path |
| Refund + invoice answers ("published with hosted-plan availability") | `lib/marketing-content/faq.ts` | Hosted plans |

## CTA-hierarchy review

- **One primary per page, always `/register`** — except `/enterprise`, where the primary is
  "Contact sales" (currently a `mailto:` placeholder that degrades to `#` until
  `CONTACT_EMAIL` is set — P1). This holds after Phase A; no page gained a second primary.
- **Ghosts are now 100% internal.** Before Phase A, five surfaces used an external
  "Star on GitHub" / "View the codebase" / "Read the architecture docs" ghost; they now route
  to `/faq` (blog index ×2, blog post → `/blog`, compare, compare detail, enterprise final CTA)
  or `/pricing` (enterprise hero). `/faq` becomes the trust destination the repo links used to
  serve — appropriate, since the FAQ carries the deterministic-scoring and self-host answers.
- **Repetition watch (P2):** the blog index shows "Read the FAQ" twice on one page when
  `POSTS` is empty (empty state + final CTA). Harmless while the blog is empty; revisit when
  posts ship — the featured/final CTA ghost could then point at the newest post.
- **Nav hierarchy unchanged:** "Get started" (primary) + "Sign in" (text link); dropdowns lost
  their only off-site row, so every nav destination is now in-site (pinned by a unit test).
- **Anchor ghosts on the landing** ("See how it works", "See the product") still scroll
  rather than navigate — good for a single-column story; keep.

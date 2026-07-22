# Design System — Searchify

> A **written** design system with **concrete token values**. Everything a build agent needs to
> author `frontend/app/globals.css` and lay out the seven MVP screens is here — **no screenshot
> is required**. Task **F1 consumes these token values verbatim.**
> Companion docs: [`../Agents.md`](../Agents.md), [`invariants.md`](invariants.md),
> [`backend-architecture.md`](backend-architecture.md), [`frontend-architecture.md`](frontend-architecture.md).

## 1. Overview

- **`frontend/app/globals.css` is the single source of truth** for tokens. Components consume
  **bridged Tailwind semantic tokens only** — never raw hex (enforced by the no-raw-hex guard).
- **Aesthetic**: dense, clean **B2B analytics** in the **CUBE27 midnight** language — a
  near-black canvas (dark-first), tight 4px rhythm, hairline borders, restrained shadows, one
  confident **blue accent** reserved for links, active states, focus rings, and charts, and
  **tabular monospace numerals** for all metrics. The light theme is the warm-paper sibling.
- **Both themes are always defined.** Dark is the default; light is a full sibling hierarchy.
  Every text-on-surface pair meets **WCAG AA contrast ≥ 4.5:1**.

## 2. Theme model

Two explicit surface hierarchies, lowest → highest elevation. `:root` = light,
`html[data-theme='dark']` = dark. A pre-hydration script sets `data-theme` to avoid flashing.
**Dark is the default**: with no stored choice the bootstrap resolves to dark (stored choice →
dark; the OS preference is not consulted — only an explicit toggle opts into light). Light is a
full sibling hierarchy, not an afterthought.

**Dark surface hierarchy (CUBE27 midnight):** `bg-base #050505` → `bg-alt #0a0a0a` →
`bg-panel #111114` → `bg-elevated #16161a` → `bg-well #1c1c21`. Sidebar `#0a0a0a`.

**Light surface hierarchy (warm paper):** `bg-base #fafaf7` → `bg-alt #efefea` →
`bg-panel #ffffff` → `bg-elevated #ffffff` → `bg-well #f2f2ec`. Sidebar `#f5f5f0`.

Accent is a **blue** (`#2f58ff` light / `#6b8aff` dark) — the CUBE27 brand accent, shared with
the marketing landing — reserved for links, active states, focus rings, and data visualization.
Semantic hues (status, sentiment, citation, run, score-band) are unchanged semantically; owned
citations keep their green identity in both themes.

## 3. Token values — LIGHT (`:root`)

```css
:root {
  color-scheme: light;

  /* Fonts */
  --font-primary-family: var(--font-sans), system-ui, sans-serif;
  --font-mono-family: var(--font-mono), ui-monospace, 'Cascadia Code', 'Fira Code', monospace;
  /* Display family for wordmarks + page-level headings (Bricolage Grotesque
     600/700 loaded as --font-brand-display in app/layout.tsx). */
  --font-display-family: var(--font-brand-display), 'Bricolage Grotesque', var(--font-primary-family);

  /* Surfaces */
  --bg-base: #fafaf7;
  --bg-alt: #efefea;
  --bg-panel: #ffffff;
  --bg-elevated: #ffffff;
  --bg-well: #f2f2ec;
  --bg-sidebar: #f5f5f0;
  --surface-overlay: rgba(250, 250, 247, 0.88);

  /* Borders */
  --border-subtle: #edece5;
  --border: #e2e0d8;
  --border-strong: #cfcbc0;
  --border-focus: var(--accent);

  /* Text — all verified ≥ 4.5:1 on bg-base/bg-panel */
  --text-primary: #0f1117;   /* 18.9:1 on #ffffff, 18.0:1 on #fafaf7 */
  --text-secondary: #4b4d58; /* 8.4:1 on #ffffff, 8.0:1 on #fafaf7  */
  --text-muted: #6d6f7a;     /* 5.0:1 on #ffffff, 4.8:1 on #fafaf7  */
  --text-subtle: #a6a6ad;    /* decorative only (icons/dividers), not body text */

  /* Accent — blue */
  --accent: #2f58ff;
  --accent-hover: #1e3fd0;
  --accent-fg: #ffffff;         /* on accent bg: 5.3:1 */
  --accent-subtle: rgba(47, 88, 255, 0.10);
  --accent-soft: rgba(47, 88, 255, 0.06);
  --accent-border: rgba(47, 88, 255, 0.30);
  --accent-text: #1e3fd0;       /* accent as text on light: 7.8:1 on #ffffff */

  /* Semantic status — text values ≥ 4.5:1 on their bg */
  --success: #059669; --success-bg: #ecfdf5; --success-border: #a7f3d0; --success-text: #065f46;
  --warning: #d97706; --warning-bg: #fffbeb; --warning-border: #fcd34d; --warning-text: #92400e;
  --danger:  #dc2626; --danger-bg:  #fef2f2; --danger-border:  #fca5a5; --danger-text:  #991b1b;
  --info:    #2563eb; --info-bg:    #eff6ff; --info-border:    #bfdbfe; --info-text:    #1d4ed8;
  --neutral-bg: rgba(15, 17, 23, 0.045);

  /* Sentiment (positive / neutral / negative) */
  --sentiment-positive: #059669; --sentiment-positive-bg: #ecfdf5; --sentiment-positive-text: #065f46;
  --sentiment-neutral:  #6f756f; --sentiment-neutral-bg:  #f1f2f1; --sentiment-neutral-text:  #4c524f;
  --sentiment-negative: #dc2626; --sentiment-negative-bg: #fef2f2; --sentiment-negative-text: #991b1b;
  /* MVP: sentiment is NOT computed — render the neutral "—" placeholder token below. */
  --value-placeholder: var(--text-subtle);

  /* Citation classification (owned / competitor / third-party) */
  --citation-owned: #0f9d76;      --citation-owned-bg: rgba(15, 157, 118, 0.10);  --citation-owned-text: #0b6f52;
  --citation-competitor: #d97706; --citation-competitor-bg: #fffbeb;              --citation-competitor-text: #92400e;
  --citation-third-party: #6366f1;--citation-third-party-bg: #eef2ff;             --citation-third-party-text: #4338ca;

  /* Run status (audit lifecycle) */
  --run-draft:      #6f756f; --run-draft-bg:      #f1f2f1;
  --run-queued:     #2563eb; --run-queued-bg:     #eff6ff;
  --run-running:    #0f9d76; --run-running-bg:    rgba(15, 157, 118, 0.12);
  --run-analyzing:  #7c3aed; --run-analyzing-bg:  #f5f3ff;
  --run-completed:  #059669; --run-completed-bg:  #ecfdf5;
  --run-partial:    #d97706; --run-partial-bg:    #fffbeb;
  --run-failed:     #dc2626; --run-failed-bg:     #fef2f2;
  --run-cancelled:  #6f756f; --run-cancelled-bg:  #f1f2f1;

  /* Score bands (visibility %) — low→high */
  --score-low:      #dc2626; --score-low-bg:      #fef2f2;  /* 0–24%   */
  --score-mid:      #d97706; --score-mid-bg:      #fffbeb;  /* 25–49%  */
  --score-good:     #2563eb; --score-good-bg:     #eff6ff;  /* 50–74%  */
  --score-high:     #059669; --score-high-bg:     #ecfdf5;  /* 75–100% */

  /* Shadows / elevation */
  --shadow-xs-value: 0 1px 0 rgba(18, 22, 20, 0.04);
  --shadow-sm-value: 0 1px 2px rgba(18, 22, 20, 0.05);
  --shadow-card-value: 0 12px 28px rgba(18, 22, 20, 0.04);
  --shadow-elevated-value: 0 18px 42px rgba(18, 22, 20, 0.08), 0 0 0 1px rgba(18, 22, 20, 0.04);
  --shadow-lg-value: 0 22px 56px rgba(18, 22, 20, 0.10), 0 0 0 1px rgba(18, 22, 20, 0.05);
  --shadow-modal: 0 24px 60px rgba(18, 22, 20, 0.16);
  --focus-ring: 0 0 0 3px rgba(47, 88, 255, 0.22);
  --overlay-scrim: rgba(0, 0, 0, 0.32);

  /* Skeleton */
  --skeleton-base: #edece4;
  --skeleton-highlight: #fafaf7;
}
```

## 4. Token values — DARK (`html[data-theme='dark']`)

Only the tokens that change are overridden; the type scale, spacing, radii, and structural
tokens (§5–§7) are shared. Dark surfaces are the CUBE27 midnight near-black scale.

```css
html[data-theme='dark'] {
  color-scheme: dark;

  /* Surfaces */
  --bg-base: #050505;
  --bg-alt: #0a0a0a;
  --bg-panel: #111114;
  --bg-elevated: #16161a;
  --bg-well: #1c1c21;
  --bg-sidebar: #0a0a0a;
  --surface-overlay: rgba(5, 5, 5, 0.92);

  /* Borders */
  --border-subtle: rgba(255, 255, 255, 0.05);
  --border: rgba(255, 255, 255, 0.08);
  --border-strong: rgba(255, 255, 255, 0.14);

  /* Text — verified ≥ 4.5:1 on bg-base/bg-panel */
  --text-primary: #fafafa;   /* 18.1:1 on #111114, 19.5:1 on #050505 */
  --text-secondary: #a1a1aa; /* 7.4:1 on #111114, 8.0:1 on #050505  */
  --text-muted: #85858f;     /* 5.2:1 on #111114, 5.6:1 on #050505  */
  --text-subtle: #52525b;    /* decorative only */

  /* Accent — blue */
  --accent: #6b8aff;
  --accent-hover: #9db2ff;
  --accent-fg: #0a0a0a;         /* dark text on bright accent: 6.3:1 */
  --accent-subtle: rgba(107, 138, 255, 0.14);
  --accent-soft: rgba(107, 138, 255, 0.08);
  --accent-border: rgba(107, 138, 255, 0.34);
  --accent-text: #9db2ff;       /* accent as text on dark: 9.9:1 on #050505 */

  /* Semantic status */
  --success: #3ecf8e; --success-bg: rgba(62, 207, 142, 0.12); --success-border: rgba(62, 207, 142, 0.28); --success-text: #5fdaa2;
  --warning: #f5a623; --warning-bg: rgba(245, 166, 35, 0.12); --warning-border: rgba(245, 166, 35, 0.28); --warning-text: #f7b854;
  --danger:  #ff6b6b; --danger-bg:  rgba(255, 107, 107, 0.12);--danger-border:  rgba(255, 107, 107, 0.28);--danger-text:  #ff8f8f;
  --info:    #6ba5ff; --info-bg:    rgba(107, 165, 255, 0.12);--info-border:    rgba(107, 165, 255, 0.28);--info-text:    #8fbcff;
  --neutral-bg: rgba(255, 255, 255, 0.04);

  /* Sentiment */
  --sentiment-positive: #3ecf8e; --sentiment-positive-bg: rgba(62, 207, 142, 0.12); --sentiment-positive-text: #5fdaa2;
  --sentiment-neutral:  #9ca39c; --sentiment-neutral-bg:  rgba(255, 255, 255, 0.05); --sentiment-neutral-text:  #9ca39c;
  --sentiment-negative: #ff6b6b; --sentiment-negative-bg: rgba(255, 107, 107, 0.12); --sentiment-negative-text: #ff8f8f;
  --value-placeholder: var(--text-subtle);

  /* Citation classification */
  --citation-owned: #2dd4a7;       --citation-owned-bg: rgba(45, 212, 167, 0.12);  --citation-owned-text: #58e0bb;
  --citation-competitor: #f5a623;  --citation-competitor-bg: rgba(245, 166, 35, 0.12); --citation-competitor-text: #f7b854;
  --citation-third-party: #8b8fff; --citation-third-party-bg: rgba(139, 143, 255, 0.12); --citation-third-party-text: #a9adff;

  /* Run status */
  --run-draft:      #9ca39c; --run-draft-bg:      rgba(255, 255, 255, 0.05);
  --run-queued:     #6ba5ff; --run-queued-bg:     rgba(107, 165, 255, 0.12);
  --run-running:    #2dd4a7; --run-running-bg:    rgba(45, 212, 167, 0.14);
  --run-analyzing:  #a78bfa; --run-analyzing-bg:  rgba(167, 139, 250, 0.14);
  --run-completed:  #3ecf8e; --run-completed-bg:  rgba(62, 207, 142, 0.12);
  --run-partial:    #f5a623; --run-partial-bg:    rgba(245, 166, 35, 0.12);
  --run-failed:     #ff6b6b; --run-failed-bg:     rgba(255, 107, 107, 0.12);
  --run-cancelled:  #9ca39c; --run-cancelled-bg:  rgba(255, 255, 255, 0.05);

  /* Score bands */
  --score-low:  #ff6b6b; --score-low-bg:  rgba(255, 107, 107, 0.12);
  --score-mid:  #f5a623; --score-mid-bg:  rgba(245, 166, 35, 0.12);
  --score-good: #6ba5ff; --score-good-bg: rgba(107, 165, 255, 0.12);
  --score-high: #3ecf8e; --score-high-bg: rgba(62, 207, 142, 0.12);

  /* Shadows — deep blacks */
  --shadow-xs-value: 0 1px 2px rgba(0, 0, 0, 0.5);
  --shadow-sm-value: 0 2px 4px rgba(0, 0, 0, 0.6);
  --shadow-card-value: 0 2px 8px rgba(0, 0, 0, 0.4);
  --shadow-elevated-value: 0 12px 28px rgba(0, 0, 0, 0.7);
  --shadow-lg-value: 0 20px 40px rgba(0, 0, 0, 0.8);
  --shadow-modal: 0 32px 64px rgba(0, 0, 0, 0.9);
  --focus-ring: 0 0 0 3px rgba(107, 138, 255, 0.35);
  --overlay-scrim: rgba(0, 0, 0, 0.55);

  /* Skeleton */
  --skeleton-base: #16161a;
  --skeleton-highlight: #1c1c21;
}
```

## 5. Type scale (px), weights, tracking, line-heights

Sans = **Public Sans** (`--font-sans` → `--font-primary-family`); mono = **IBM Plex Mono**
400/500/600 (`--font-mono` → `--font-mono-family`) with **tabular numerals**
(`font-variant-numeric: tabular-nums`) — mono is used for **every metric, percentage, count,
position, and timestamp** so columns align. Display = **Bricolage Grotesque** 600/700, loaded
in `app/layout.tsx` as the `--font-brand-display` next/font variable and exposed to components
via `--font-display-family` → the bridged `font-display` utility — for the wordmark, page-level
headings, and the auth brand headline (the same trio as the marketing landing).

| Token | Size | Line-height | Tracking | Weight | Use |
|---|---|---|---|---|---|
| `--text-2xs` | 10px (0.625rem) | 1.2 | 0.06em | 600 | uppercase micro-labels |
| `--text-xs` | 11px (0.6875rem) | 1.35 | 0.025em | 500 | captions, timestamps, table headers |
| `--text-sm` | 13px (0.8125rem) | 1.45 | 0em | 400 | secondary body, table cells |
| `--text-base` | 14px (0.875rem) | 1.5 | 0em | 400 | primary body |
| `--text-lg` | 17px (1.0625rem) | 1.35 | -0.01em | 600 | section / card headings |
| `--text-xl` | 21px (1.3125rem) | 1.25 | -0.02em | 600 | page headings |
| `--text-2xl` | 29px (1.8125rem) | 1.15 | -0.02em | 700 | display (Visibility Score, hero) |

- **display** = `--text-2xl`; **heading** = `--text-xl`/`--text-lg`; **body** =
  `--text-base`/`--text-sm`; **label** = `--text-xs`/`--text-2xs` (uppercase).
- Weights: `--weight-normal:400`, `--weight-medium:500`, `--weight-semibold:600`,
  `--weight-bold:700`.
- Tracking tokens: `--tracking-tight:-0.02em`, `--tracking-normal:0em`, `--tracking-wide:0.025em`,
  `--tracking-wider:0.06em`.
- Line-height tokens: `--leading-none:1`, `--leading-tight:1.2`, `--leading-snug:1.35`,
  `--leading-normal:1.5`.

## 6. Spacing (4px grid), radii, controls

**Spacing steps** (`--space-N` = 4px × N):
`--space-1:4px`, `--space-2:8px`, `--space-3:12px`, `--space-4:16px`, `--space-5:20px`,
`--space-6:24px`, `--space-7:28px`, `--space-8:32px`, `--space-10:40px`, `--space-12:48px`,
`--space-14:56px`, `--space-16:64px`, `--space-20:80px`. `--card-padding:20px`;
`--content-gutter:32px`.

**Radii:** `--radius-xs:3px`, `--radius-sm:5px`, `--radius-md:7px`, `--radius-lg:10px`,
`--radius-xl:14px`, `--radius-2xl:20px`, `--radius-full:9999px` (**pill** — badges, toggles,
segmented control, avatar).

**Controls:** `--control-height-sm:30px`, `--control-height:34px`, `--control-height-lg:38px`,
`--interactive-border-width:1px`. Table: `--table-row-height:40px`, `--table-header-height:32px`,
`--table-font-size: var(--text-sm)`, `--table-header-font-size: var(--text-xs)`.

## 7. Tailwind v4 bridge (`@theme inline`)

Bridge the raw variables to semantic Tailwind utilities so components reference **only** the
bridged names (`bg-background`, `text-foreground`, `border-border`, `bg-accent`, `text-success`,
`bg-citation-owned`, `text-run-completed`, `bg-score-high`, `shadow-card`, `rounded-full`,
`font-mono`, etc.). Example shape:

```css
@theme inline {
  --font-sans: var(--font-primary-family);
  --font-mono: var(--font-mono-family);
  --font-display: var(--font-display-family); /* Bricolage via --font-brand-display */
  --color-background: var(--bg-base);
  --color-panel: var(--bg-panel);
  --color-foreground: var(--text-primary);
  --color-secondary: var(--text-secondary);
  --color-muted: var(--text-muted);
  --color-border: var(--border);
  --color-accent: var(--accent);
  --color-success: var(--success); /* + warning/danger/info + *-bg/*-border/*-text */
  --color-sentiment-positive: var(--sentiment-positive); /* + neutral/negative */
  --color-citation-owned: var(--citation-owned);         /* + competitor/third-party */
  --color-run-completed: var(--run-completed);           /* + every run-status */
  --color-score-high: var(--score-high);                 /* + low/mid/good */
  --shadow-card: var(--shadow-card-value);               /* + xs/sm/elevated/lg/modal */
  /* type sizes, radii, tracking, line-heights bridged here too (see §5–§6) */
}
```

**Implementation rules** (F1): raw hex lives **only** in `:root`/`[data-theme='dark']`;
components use bridged tokens only (no-raw-hex guard); **both themes are always fully defined**;
`data-theme` is set pre-hydration.

## 8. Component-primitive inventory

All CVA-driven, token-only, Radix where relevant, lucide icons.

| Primitive | Notes |
|---|---|
| `button` | variants: primary (accent), secondary, neutral, ghost, destructive, topbar; sizes sm/md/lg; `asChild`; icon slot. |
| `badge` | variants map to tokens: `status` (success/warning/danger/info), `sentiment` (positive/neutral/negative), `classification` (owned/competitor/third-party), `run-status` (all 8). Pill radius. |
| `table` (dense) | sticky 32px header (`--text-xs` uppercase, `--tracking-wide`), 40px rows, `--text-sm` cells, mono tabular numerals for numeric columns, hover row highlight, sortable header carets. |
| `card` | `bg-panel`, `border`, `--radius-lg`, `--card-padding`, `shadow-card`; header/title/description/content/footer slots. |
| `score-ring` | circular progress; color from score-band token; center = mono display number; ARIA label with %. |
| `donut` | segmented ring for per-engine / citation-share; legend; ARIA. |
| `trend-chart` | line/area chart. **Built but unused in MVP** (roadmap trend view); render + ARIA only. |
| `tabs` / `segmented` | Radix tabs + a pill segmented control (`--segmented-bg`, active = accent-fg on accent). |
| `sidebar` | grouped nav (Analytics / Prompts / Actions / On Page); active item = accent-subtle bg + accent-text; disabled items = muted + "soon". |
| `top-bar` | search placeholder, Export hook, Learn link, project-switcher, theme-toggle, user-menu. |
| `input` / `field` | 34px height, `border`, `--radius-md`, focus = `--focus-ring`; `field` wraps label + help + error. |
| `dialog` | Radix modal; `--overlay-scrim`, `bg-elevated`, `shadow-modal`, `--radius-xl`. |
| `dropdown` | Radix menu; `bg-elevated`, `border`, `shadow-elevated`. |
| `tooltip` | Radix; `bg-well`/inverse, `--text-xs`. |
| `skeleton` | `--skeleton-base` → `--skeleton-highlight` shimmer. |
| `history-drawer` | right-side Radix drawer for run history / execution list. |

## 9. Per-screen layout prose (seven application screens)

The app is a fixed **left sidebar (240px)** + **top bar (52px)** + scrolling content region
(max content width ~1440px, `--content-gutter` padding). Auth screens are the exception
(centered, no shell).

### 9.1 Auth (`/login`, `/register`)
**Split-screen shell** (shared `(auth)` route-group layout). At ≥900px a two-column grid
[5fr 6fr]: the **brand panel** (`bg-panel`, hairline right border, token-driven aurora glows +
grain overlay) carries the LogoCube + display-font "Searchify" wordmark with a mono uppercase
"By CUBE27" chip, a mono uppercase eyebrow, a `font-display` brand headline with an
accent-gradient phrase, three proof points, a static decorative mini stat card (visibility
score sparkline + share-of-voice bars, aria-hidden), and a mono footer strip. The **form
panel** centers the auth card (max-width 400px, `shadow-card`); below 900px only the form
panel renders with a compact wordmark row above the card. Three OAuth `button`s (Google,
GitHub, Apple — coming-soon wired, 503 → accessible inline notice) sit above an email divider;
fields (`field` primitive): email, password (+ confirm/name on register). Primary full-width
`button`. Inline `ApiError` above the form (danger alert). Below: a link toggling
login/register. No sidebar/top-bar. Theme toggle in the top-right corner. The pages own the
single h1 — brand-panel wordmarks are spans and the headline is a `<p>`.

### 9.2 App Shell (`(app)/layout.tsx`)
**Left sidebar (240px, `bg-sidebar`)**: top = project-switcher (brand avatar + name, dropdown).
A "Getting Started N of 6" progress card. Grouped nav sections — **Analytics** (Visibility =
live; LLM Analytics, Traffic = disabled "soon"), **Prompts** (Your Prompts = live; Prompt
Research = live), **Actions** (Content, Opportunities = disabled), **On Page** (Site Health,
Issues, Knowledge Base = live). Bottom = user-menu (avatar + email). **Top bar (52px)**:
left = "Find anything…" search placeholder; right = Export hook, Learn link, theme-toggle.
Active nav item uses accent-subtle bg + accent-text; disabled items are muted with a "soon"
pill. Content region scrolls independently.

### 9.3 Brand/Project setup (`/setup`)
Single scrolling form on `bg-base`, sections as `card`s. **Profile card**: brand avatar,
Brand Name, website URL (with derived domain shown muted), Alternative names (alias chips with
✕ + "Add alternative name" input, Enter-to-add), an "Exact match only" toggle. **Location**:
country_code select + a Geographic Reach segmented control (Global / Primary market / Nationwide
/ Regional / Local). **Domains**: owned domains + unintended domains chip inputs. **Competitors**:
repeatable rows (name + aliases + domains, add/remove). **Audit defaults**: `benchmark_mode`
segmented (consumer_like / controlled_localized / forced_grounded) + default repetitions
stepper. Sticky footer with Cancel + primary Save/Create. Create sets active project → routes
to `/visibility`. Setup does not own the curated knowledge profile; that lives at **Knowledge
Base**. react-hook-form + zod; inline field errors.

### 9.3.1 Knowledge Base (`/knowledge-base`)
For a persisted project, **Knowledge Base** owns description, positioning, products/services,
and target audience, with manual save plus a consent-gated “Draft with AI” review flow. An AI
draft never applies immediately; unchanged accepted fields retain AI provenance and edits become
manual. This knowledge grounds assisted competitor and prompt generation without making those
facts part of basic project setup.

### 9.4 Prompt library (`/prompts` Your Prompts + `/prompt-research` Prompt Research)
**Your Prompts (`/prompts`)** — read-only, score-annotated view of the active configuration:
a summary banner ("N visibility prompts across M topics" + "Go to Prompt Research" button),
search, and a dense table grouped by topic with expandable group rows. Columns: expander,
Prompt, Visibility Score (mono %, score-band color, derived from persisted audit evidence),
Avg Position (`—` at MVP), Sentiment (`—`), Topic (badge), Branded (badge). Topic group rows
show the prompt count and the mean of measured prompt scores.

**Prompt Research (`/prompt-research`)** — the management workspace. Two-pane. **Left rail
(280px)**: Topics list with counts + "Add topic"; "All topics" at top.
**Main**: toolbar (Columns, Filter, search prompts, "Bulk upload" = CSV import, "Add Prompts",
and "Generate Prompts & Topics" (opens the AI-generation dialog: count, optional target topic,
and an explicit consent checkbox before brand evidence is sent to the default agent; results
land in the Proposed tab for review). Below the toolbar, segmented tabs: **Active / Proposed / Archived** with counts. Dense
`table`: checkbox, Prompt text, Visibility Score (mono %, score-band badge), Avg Position
(mono, `—` placeholder at MVP), Sentiment (mono, `—` placeholder), Topic (badge), Branded
(badge), Enabled toggle. Row actions: edit, delete, enable/disable. CSV import opens a dialog
that parses in-browser, previews rows, then persists via `/prompt-sets/{id}/import`. Empty
state: centered prompt-to-add card.

### 9.5 Provider Settings (`/providers`)
Grid of three per-engine `card`s, one **direct** transport each (ChatGPT/OpenAI,
Gemini/Google, Claude/Anthropic). Each card shows its fixed route + default model, an API-key
`input` (masked, write-only — never shows a stored secret), a "Test connection" `button`
surfacing success/error inline, and a `configured` status badge. After the v2 direct-provider
retirement each engine has exactly ONE route, so the **route segmented toggle and the reserved
"Direct OpenAI — coming soon" option are removed**. Each card displays only its approved
direct-provider route. Below the engine cards, a separate **Discovery / analysis model**
selection card (plumbing-only; stored, not invoked). Unconfigured engines show a muted "Not
configured" state.

### 9.6 Visibility workspace (`/visibility`)
ONE workspace shell: a **shared filter bar** (run selector defaulting to the latest completed
audit, logical engine, prompt, date range, granularity) above an accessible **four-tab**
tablist — **Overview** (default), **Trends**, **Mentions & Citations**, **Query Fanout**. Only
one panel renders at a time; the active tab is mirrored in `?tab=`. There are **no Sources /
Topics / Sentiment tabs** and no disabled / "coming soon" tabs.
- **Overview**: two-column grid. **Left card — Visibility**: large mono **Visibility Score**
  (`--text-2xl`) with a `score-ring`, subtitle "Your brand's visibility across LLMs for this
  run". **Right card — Rankings**: dense `table` of brand + competitors — columns `#`, Brand
  (avatar + name, "You" pill on own brand), Visibility% (mono + score-band), SOV% (mono),
  Sentiment (mono `—` placeholder), Avg Position (mono `—` placeholder). Below: a **per-engine
  comparison** card and a **Share of Voice** card (brand-vs-competitor donut).
- **Trends**: cross-run metrics + charts (`trend-chart`) over the selected date range /
  granularity.
- **Mentions & Citations** and **Query Fanout**: the shared persisted execution-evidence
  dataset — brand/competitor mention chips, classified citations, and frozen prompts + generated
  queries with `queries_available | count_only | no_search` states.

Empty state (no completed runs): a "Launch your first audit" card linking to `/runs`.

### 9.7 Run/Executions explorer (`/runs`, `/runs/[runId]`, `.../executions/[executionId]`)
**`/runs`**: list `table` of audits (status badge via run-status token, requested/completed/
failed mono counts, created timestamp) + a "Launch audit" `button` opening a dialog (select
prompts/prompt-set + engines/providers + repetitions → `POST /audits`). **`/runs/[runId]`**:
a progress panel (requested / completed / failed counts + run-status badge + a **Cancel**
button via `POST /audits/{id}/cancel`), **polling while active** (SSE optional), CSV/MD export
links, then an executions `table` (prompt, engine badge with logical+transport, status, latency
mono). **`.../executions/[executionId]`**: an evidence `card` — answer text, `search_used`
badge, **citations** listed with owned/competitor/third-party classification badges, brand &
competitor mention chips, and the per-response `score` dict (mono key/value). Sentiment shows
the `—` placeholder.

## 10. Motion + accessibility

- **Motion**: `--transition-fast:100ms`, `--transition-base:180ms`, `--transition-slow:280ms`,
  all `cubic-bezier(0.4,0,0.2,1)`. Respect `prefers-reduced-motion` (disable non-essential
  transitions/shimmer). Skeleton shimmer ~1.2s loop.
- **Accessibility**: every text/background pair meets **AA ≥ 4.5:1** (subtle/decorative tokens
  are never used for body text). Focus is always visible via `--focus-ring`. `score-ring`,
  `donut`, and `trend-chart` carry ARIA labels with the numeric value. `forced-colors` mode
  falls back to system colors; badges keep a text label (never color-only meaning). Interactive
  targets ≥ 30px height.

## 11. Implementation rules (F1 checklist)

1. Author `:root` (light) + `html[data-theme='dark']` (dark) with **all** tokens from §3–§4.
2. Add the `@theme inline` bridge (§7) — components use bridged tokens only.
3. **No raw hex outside the two theme blocks** (no-raw-hex guard).
4. **Both themes always defined**; `data-theme` set pre-hydration; **dark is the default**
   (stored choice → dark; the OS preference is not consulted).
5. Mono font gets `font-variant-numeric: tabular-nums`; all metrics use mono.
6. Ship `prefers-reduced-motion`, `forced-colors`, and `print` rules.
7. Load Public Sans / IBM Plex Mono / Bricolage Grotesque via next/font in `app/layout.tsx`
   (`--font-sans`, `--font-mono`, `--font-brand-display`); expose Bricolage through the
   `--font-display-family` token → bridged `font-display` utility. Never name a next/font
   variable `--font-display` — that name is the bridged `@theme` token.


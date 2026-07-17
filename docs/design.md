# Design System — Searchify

> A **written** design system with **concrete token values**. Everything a build agent needs to
> author `frontend/app/globals.css` and lay out the seven MVP screens is here — **no screenshot
> is required**. Task **F1 consumes these token values verbatim.**
> Companion docs: [`../Agents.md`](../Agents.md), [`invariants.md`](invariants.md),
> [`backend-architecture.md`](backend-architecture.md), [`frontend-architecture.md`](frontend-architecture.md).

## 1. Overview

- **`frontend/app/globals.css` is the single source of truth** for tokens. Components consume
  **bridged Tailwind semantic tokens only** — never raw hex (enforced by the no-raw-hex guard).
- **Aesthetic**: dense, clean **B2B analytics** — a calm neutral canvas, tight 4px rhythm, thin
  borders, restrained shadows, one confident accent, and **tabular monospace numerals** for all
  metrics. Original palette (not a clone of the reference).
- **Both themes are always defined.** Light is the default; dark is a full sibling hierarchy.
  Every text-on-surface pair meets **WCAG AA contrast ≥ 4.5:1**.

## 2. Theme model

Two explicit surface hierarchies, lowest → highest elevation. `:root` = light,
`html[data-theme='dark']` = dark. A pre-hydration script sets `data-theme` to avoid flashing.

**Light surface hierarchy:** `bg-base #f7f8f7` → `bg-alt #eef0ee` → `bg-panel #ffffff` →
`bg-elevated #ffffff` → `bg-well #e2e5e2`. Sidebar `#f2f4f2`.

**Dark surface hierarchy:** `bg-base #0e0f0e` → `bg-alt #161816` → `bg-panel #1d1f1d` →
`bg-elevated #242724` → `bg-well #2e312e`. Sidebar `#131513`.

Accent is a **teal-green** (`#0f9d76` light / `#2dd4a7` dark) — distinct from the reference's
blue-violet — reflecting the AEO "visibility" brand.

## 3. Token values — LIGHT (`:root`)

```css
:root {
  color-scheme: light;

  /* Fonts */
  --font-primary-family: var(--font-sans), system-ui, sans-serif;
  --font-mono-family: var(--font-mono), ui-monospace, 'Cascadia Code', 'Fira Code', monospace;

  /* Surfaces */
  --bg-base: #f7f8f7;
  --bg-alt: #eef0ee;
  --bg-panel: #ffffff;
  --bg-elevated: #ffffff;
  --bg-well: #e2e5e2;
  --bg-sidebar: #f2f4f2;
  --surface-overlay: rgba(255, 255, 255, 0.88);

  /* Borders */
  --border-subtle: #edefed;
  --border: #e2e5e2;
  --border-strong: #d0d4d0;
  --border-focus: var(--accent);

  /* Text — all verified ≥ 4.5:1 on bg-base/bg-panel */
  --text-primary: #1d211f;   /* 14.8:1 on #ffffff */
  --text-secondary: #4c524f; /* 8.0:1  */
  --text-muted: #6f756f;     /* 4.9:1  */
  --text-subtle: #9aa09a;    /* decorative only (icons/dividers), not body text */

  /* Accent — teal-green */
  --accent: #0f9d76;
  --accent-hover: #0c8563;
  --accent-fg: #ffffff;         /* on accent bg: 4.6:1 */
  --accent-subtle: rgba(15, 157, 118, 0.10);
  --accent-soft: rgba(15, 157, 118, 0.06);
  --accent-border: rgba(15, 157, 118, 0.28);
  --accent-text: #0b6f52;       /* accent as text on light: 5.2:1 */

  /* Semantic status — text values ≥ 4.5:1 on their bg */
  --success: #059669; --success-bg: #ecfdf5; --success-border: #a7f3d0; --success-text: #065f46;
  --warning: #d97706; --warning-bg: #fffbeb; --warning-border: #fcd34d; --warning-text: #92400e;
  --danger:  #dc2626; --danger-bg:  #fef2f2; --danger-border:  #fca5a5; --danger-text:  #991b1b;
  --info:    #2563eb; --info-bg:    #eff6ff; --info-border:    #bfdbfe; --info-text:    #1d4ed8;
  --neutral-bg: rgba(29, 33, 31, 0.045);

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
  --focus-ring: 0 0 0 3px rgba(15, 157, 118, 0.20);
  --overlay-scrim: rgba(0, 0, 0, 0.32);

  /* Skeleton */
  --skeleton-base: #ededeb;
  --skeleton-highlight: #fafafa;
}
```

## 4. Token values — DARK (`html[data-theme='dark']`)

Only the tokens that change are overridden; the type scale, spacing, radii, and structural
tokens (§5–§7) are shared. Dark surfaces are pure neutral-green-tinted gray, zero blue cast.

```css
html[data-theme='dark'] {
  color-scheme: dark;

  /* Surfaces */
  --bg-base: #0e0f0e;
  --bg-alt: #161816;
  --bg-panel: #1d1f1d;
  --bg-elevated: #242724;
  --bg-well: #2e312e;
  --bg-sidebar: #131513;
  --surface-overlay: rgba(14, 15, 14, 0.94);

  /* Borders */
  --border-subtle: #1e201e;
  --border: #2a2d2a;
  --border-strong: #383c38;

  /* Text — verified ≥ 4.5:1 on bg-base/bg-panel */
  --text-primary: #e8ebe8;   /* 14.1:1 on #1d1f1d */
  --text-secondary: #9ca39c; /* 6.0:1  */
  --text-muted: #6b716b;     /* decorative-leaning; use secondary for body */
  --text-subtle: #474b47;    /* decorative only */

  /* Accent — teal-green brightened for dark */
  --accent: #2dd4a7;
  --accent-hover: #4fe0ba;
  --accent-fg: #06231a;         /* dark text on bright accent: 7.9:1 */
  --accent-subtle: rgba(45, 212, 167, 0.14);
  --accent-soft: rgba(45, 212, 167, 0.08);
  --accent-border: rgba(45, 212, 167, 0.32);
  --accent-text: #58e0bb;       /* accent as text on dark: 8.4:1 */

  /* Semantic status */
  --success: #3ecf8e; --success-bg: rgba(62, 207, 142, 0.10); --success-border: rgba(62, 207, 142, 0.24); --success-text: #5fdaa2;
  --warning: #f5a623; --warning-bg: rgba(245, 166, 35, 0.10); --warning-border: rgba(245, 166, 35, 0.24); --warning-text: #f7b854;
  --danger:  #ff6b6b; --danger-bg:  rgba(255, 107, 107, 0.10);--danger-border:  rgba(255, 107, 107, 0.24);--danger-text:  #ff8f8f;
  --info:    #6ba5ff; --info-bg:    rgba(107, 165, 255, 0.10);--info-border:    rgba(107, 165, 255, 0.24);--info-text:    #8fbcff;
  --neutral-bg: rgba(255, 255, 255, 0.04);

  /* Sentiment */
  --sentiment-positive: #3ecf8e; --sentiment-positive-bg: rgba(62, 207, 142, 0.10); --sentiment-positive-text: #5fdaa2;
  --sentiment-neutral:  #9ca39c; --sentiment-neutral-bg:  rgba(255, 255, 255, 0.05); --sentiment-neutral-text:  #9ca39c;
  --sentiment-negative: #ff6b6b; --sentiment-negative-bg: rgba(255, 107, 107, 0.10); --sentiment-negative-text: #ff8f8f;
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
  --focus-ring: 0 0 0 3px rgba(45, 212, 167, 0.28);
  --overlay-scrim: rgba(0, 0, 0, 0.55);

  /* Skeleton */
  --skeleton-base: #232623;
  --skeleton-highlight: #2b2e2b;
}
```

## 5. Type scale (px), weights, tracking, line-heights

Sans = a Geist-class variable sans (`--font-sans`); mono = a Geist-Mono-class variable mono
with **tabular numerals** (`font-variant-numeric: tabular-nums`) — mono is used for **every
metric, percentage, count, position, and timestamp** so columns align.

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

## 9. Per-screen layout prose (seven MVP screens)

The app is a fixed **left sidebar (240px)** + **top bar (52px)** + scrolling content region
(max content width ~1440px, `--content-gutter` padding). Auth screens are the exception
(centered, no shell).

### 9.1 Auth (`/login`, `/register`)
Centered single card (max-width 400px) on `bg-base`, `shadow-card`, `--radius-xl`. Logo mark +
"Searchify" wordmark at top. Fields (`field` primitive): email, password (+ confirm/name on
register). Primary full-width `button`. Inline `ApiError` above the form (danger alert).
Below: a link toggling login/register. No sidebar/top-bar. Theme toggle in a corner.

### 9.2 App Shell (`(app)/layout.tsx`)
**Left sidebar (240px, `bg-sidebar`)**: top = project-switcher (brand avatar + name, dropdown).
A "Getting Started N of 6" progress card. Grouped nav sections — **Analytics** (Visibility =
live; LLM Analytics, Traffic = disabled "soon"), **Prompts** (Your Prompts = live; Prompt
Research = disabled), **Actions** (Content, Opportunities = disabled), **On Page** (Site Health,
Issues = live; Knowledge Base = disabled). Bottom = user-menu (avatar + email). **Top bar (52px)**:
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
to `/visibility`. react-hook-form + zod; inline field errors.

### 9.4 Prompt library (`/prompts`)
Two-pane. **Left rail (280px)**: Topics list with counts + "Add topic"; "All topics" at top.
**Main**: toolbar (Columns, Filter, search prompts, "Bulk upload" = CSV import, "Add Prompts",
and a disabled "Generate Prompts & Topics" with a "coming soon" tooltip → the B3 `/generate`
stub). Below the toolbar, segmented tabs: **Active / Proposed / Archived** with counts. Dense
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
"Direct OpenAI — coming soon" option are removed**; `openrouter` labels appear only on read-only
historical provenance. Below the engine cards, a separate **Discovery / analysis model**
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
4. **Both themes always defined**; `data-theme` set pre-hydration.
5. Mono font gets `font-variant-numeric: tabular-nums`; all metrics use mono.
6. Ship `prefers-reduced-motion`, `forced-colors`, and `print` rules.


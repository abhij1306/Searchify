/**
 * Competitor-comparison content for /compare and /compare/[competitor].
 *
 * Honest-framing rule: every `searchify` cell is grounded in README.md or
 * docs/site-health.md; every `competitor` cell stays '[TODO(user)]' until the
 * user verifies the competitor's current capabilities first-party. `tagline`
 * and `verdict` are likewise user-fillable.
 */

export type ComparisonRow = {
  dimension: string;
  searchify: string;
  competitor: string;
};

export type Competitor = {
  slug: string;
  name: string;
  tagline: string;
  rows: readonly ComparisonRow[];
  verdict: string;
};

/**
 * The Searchify column, shared by every comparison — the dimensions are the
 * same on each detail page, and every competitor cell starts as the
 * unverified placeholder. When you research a competitor, inline their rows
 * with real `competitor` cells (and mark the review date on the page).
 */
const SEARCHIFY_COLUMN: readonly { dimension: string; searchify: string }[] = [
  {
    dimension: 'Engines covered',
    searchify:
      'ChatGPT, Gemini, and Claude — one audit runs the same prompts across all three, ' +
      'side by side, on your own provider keys.',
  },
  {
    dimension: 'Scoring model',
    searchify:
      'Deterministic and versioned — explicit analyzers and scoring rules over persisted ' +
      'evidence, with analyzer and scoring-rule versions attached to every projection. ' +
      'Same data, same score.',
  },
  {
    dimension: 'Evidence drill-down',
    searchify:
      'Every headline metric links to the exact persisted run — raw response text, ' +
      'classified citations, and query-fanout evidence included.',
  },
  {
    dimension: 'Bring your own keys',
    searchify:
      'BYOK only — provider keys are Fernet-encrypted at rest, resolved only at execution ' +
      'time, and never returned in API responses, logged, or sent as part of a prompt.',
  },
  {
    dimension: 'Open source / self-host',
    searchify:
      'MIT-licensed — self-host the full stack (Postgres, FastAPI backend, audit workers, ' +
      'Next.js frontend) with Docker Compose.',
  },
  {
    dimension: 'Site health / AEO auditing',
    searchify:
      'Built in — first-party SSRF-bounded crawler, Technical + AEO scores, grouped issues ' +
      'with remediation, per-URL diagnostics, and workspace-scoped CSV/Markdown exports.',
  },
  {
    dimension: 'Price transparency',
    searchify: 'Open source under MIT and free to self-host — hosted plans [TODO(user)].',
  },
];

function placeholderRows(): readonly ComparisonRow[] {
  return SEARCHIFY_COLUMN.map((row) => ({ ...row, competitor: '[TODO(user)]' }));
}

export const COMPETITORS: readonly Competitor[] = [
  {
    slug: 'profound',
    name: 'Profound',
    tagline: '[TODO(user)]',
    rows: placeholderRows(),
    verdict: '[TODO(user)]',
  },
  {
    slug: 'otterly-ai',
    name: 'Otterly AI',
    tagline: '[TODO(user)]',
    rows: placeholderRows(),
    verdict: '[TODO(user)]',
  },
  {
    slug: 'scrunch-ai',
    name: 'Scrunch AI',
    tagline: '[TODO(user)]',
    rows: placeholderRows(),
    verdict: '[TODO(user)]',
  },
  {
    slug: 'peec-ai',
    name: 'Peec AI',
    tagline: '[TODO(user)]',
    rows: placeholderRows(),
    verdict: '[TODO(user)]',
  },
];

/**
 * Pricing content for /pricing (tier cards + comparison table).
 *
 * Grounding rule: every Searchify-side claim comes from README.md or
 * docs/site-health.md. Anything the repo cannot ground — prices, quota
 * numbers, support levels, SLAs — stays '[TODO(user)]' until the user fills
 * it in. Prices render verbatim, so the placeholder shows on the page.
 */

export type PricingTableRow = {
  dimension: string;
  free: string;
  starter: string;
  pro: string;
  enterprise: string;
};

export type PricingTierKey = Exclude<keyof PricingTableRow, 'dimension'>;

export type PricingTier = {
  key: PricingTierKey;
  name: string;
  price: string;
  cadence: string;
  blurb: string;
  cta: { label: string; href: string };
  features: readonly string[];
  highlighted?: boolean;
  primaryCta?: boolean;
};

export const PRICING_TIERS: readonly PricingTier[] = [
  {
    key: 'free',
    name: 'Free sample',
    price: '$0',
    cadence: 'forever',
    blurb: 'See what the evidence looks like before you commit.',
    cta: { label: 'Start free', href: '/register' },
    features: [
      // Grounded in docs/site-health.md — the `free` entitlement.
      'Site Health sample crawl — deterministic, seeded, and read-only',
      'Technical + AEO scores for every sampled page',
      'Grouped issues with severity and remediation guidance',
      'Discovered-site total never disclosed',
    ],
  },
  {
    key: 'starter',
    name: 'Starter',
    price: 'Custom',
    cadence: '/mo',
    blurb: 'Monitor the pages that matter, on a cadence you control.',
    cta: { label: 'Get started', href: '/register' },
    primaryCta: true,
    features: [
      'Everything in Free sample, plus:',
      // Grounded in docs/site-health.md — the `starter` entitlement.
      'Full progressive URL inventory — discovered totals disclosed',
      'Monitored URL set — you pick the pages, quota-controlled',
      'Authenticated CSV + Markdown exports, scoped to your workspace',
    ],
  },
  {
    key: 'pro',
    name: 'Pro',
    price: 'Custom',
    cadence: '/mo',
    blurb: 'For teams benchmarking competitors and reporting trends.',
    cta: { label: 'Get started', href: '/register' },
    highlighted: true,
    primaryCta: true,
    features: [
      'Everything in Starter, plus:',
      'Higher monitored-URL quotas',
      'Multiple projects per workspace',
      'Cross-run trends — engine, time-range, and granularity controls',
      'Competitor benchmarking + share of voice',
    ],
  },
  {
    key: 'enterprise',
    name: 'Enterprise',
    price: 'Custom',
    cadence: 'annual agreement · sized to your volumes',
    blurb: 'Custom volumes, security review, and self-hosting.',
    cta: { label: 'Explore Enterprise', href: '/enterprise' },
    features: [
      'Everything in Pro, plus:',
      'Self-host option — MIT-licensed codebase, Docker Compose quick start',
      'Strict workspace isolation with UUID identifiers throughout',
      'Custom volumes, seats, and retention',
      'Support options tailored to your team',
    ],
  },
];

export const PRICING_TABLE_ROWS: readonly PricingTableRow[] = [
  {
    dimension: 'Three-engine audits — ChatGPT · Gemini · Claude',
    free: '✓',
    starter: '✓',
    pro: '✓',
    enterprise: '✓',
  },
  {
    dimension: 'BYOK provider keys — Fernet-encrypted at rest',
    free: '✓',
    starter: '✓',
    pro: '✓',
    enterprise: '✓',
  },
  {
    dimension: 'Deterministic scoring — versioned analyzers + rules',
    free: '✓',
    starter: '✓',
    pro: '✓',
    enterprise: '✓',
  },
  {
    dimension: 'Evidence explorer — drill to the raw response',
    free: '✓',
    starter: '✓',
    pro: '✓',
    enterprise: '✓',
  },
  {
    dimension: 'Site Health crawl mode',
    free: 'Sample — deterministic, seeded, read-only',
    starter: 'Full progressive inventory',
    pro: 'Full progressive inventory',
    enterprise: 'Full progressive inventory',
  },
  {
    dimension: 'Monitored URL set',
    free: '—',
    starter: 'Quota-controlled selection',
    pro: 'Expanded selection',
    enterprise: 'Custom',
  },
  {
    dimension: 'Authenticated CSV + Markdown exports',
    free: '✓',
    starter: '✓',
    pro: '✓',
    enterprise: '✓',
  },
  {
    dimension: 'Support',
    free: 'Community — GitHub issues',
    starter: 'Product support',
    pro: 'Priority support',
    enterprise: 'Tailored support',
  },
];

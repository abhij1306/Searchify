import { GITHUB_URL } from './social';

/**
 * FAQ content for /faq — five groups matching the approved mockup.
 *
 * Answers are grounded in README.md, docs/site-health.md, and the MIT
 * LICENSE; account/billing answers stay '[TODO(user)]' until the user fills
 * them in.
 */

export type FaqItem = {
  q: string;
  a: string;
};

export type FaqGroup = {
  heading: string;
  items: readonly FaqItem[];
};

export const FAQ_GROUPS: readonly FaqGroup[] = [
  {
    heading: 'Product',
    items: [
      {
        q: 'What is Searchify?',
        a:
          'Searchify is an open-source AI visibility and site intelligence platform. It runs ' +
          'repeatable audits of your brand across ChatGPT, Gemini, and Claude using your own ' +
          'provider keys, scores the results deterministically, and keeps the persisted evidence ' +
          'behind every number. A built-in, security-bounded crawler also audits your site’s ' +
          'technical and AEO health, with grouped issues and remediation guidance — so you can ' +
          'measure the answers and improve the pages they rely on.',
      },
      {
        q: 'Which answer engines does Searchify cover?',
        a:
          'ChatGPT, Gemini, and Claude. One audit runs the same prompts across all three, side ' +
          'by side, with comparable scores. Each engine runs through exactly one approved ' +
          'transport, and every run records all three identities: logical engine, transport ' +
          'provider, and exact transport model.',
      },
      {
        q: 'What does deterministic scoring mean?',
        a:
          'Headline metrics — mentions, citations, share of voice — are computed from persisted ' +
          'evidence with explicit, versioned rules: the same data always produces the same score. ' +
          'Every projection carries its analyzer and scoring-rule versions, so any number can be ' +
          'traced back to the exact run and rule set that produced it. Metrics that aren’t ' +
          'supported stay null and render as — instead of a fabricated zero.',
      },
      {
        q: 'What is query fanout?',
        a:
          'When an answer engine grounds its response, it expands your prompt into generated ' +
          'search queries — the query fanout. Those generated queries decide which pages get ' +
          'retrieved and cited. Searchify persists the fanout as evidence, so you can see which ' +
          'generated searches shaped the answer.',
      },
      {
        q: 'What is share of voice?',
        a:
          'Share of voice measures how present your brand is in engine responses compared with ' +
          'the competitors you track. It is computed from persisted runs and tracked over time, ' +
          'engine by engine, in the visibility workspace.',
      },
    ],
  },
  {
    heading: 'Privacy & keys',
    items: [
      {
        q: 'Do I need my own API keys?',
        a:
          'Yes — audits are BYOK. You connect your own OpenAI, Anthropic, and Google keys, pay ' +
          'your providers directly, and keep full control over usage and spend. Searchify never ' +
          'resells model calls.',
      },
      {
        q: 'How are my keys stored?',
        a:
          'Provider secrets are Fernet-encrypted at rest and resolved only at execution time. A ' +
          'key is never returned in an API response, never logged, and never sent as part of a ' +
          'prompt.',
      },
      {
        q: 'What data does the site crawler keep?',
        a:
          'The first-party crawler is SSRF-bounded and resource-capped, and raw fetched HTML is ' +
          'never retained. Site Health stores delivery facts, normalized page facts, evidence, ' +
          'links, and issue history — not your page source.',
      },
    ],
  },
  {
    heading: 'Site health',
    items: [
      {
        q: 'Free sample vs Starter monitoring — what’s the difference?',
        a:
          'Free runs a deterministic, seeded sample crawl that is read-only: a sample set is ' +
          'auto-selected and analyzed, and the discovered full-site total is never disclosed. ' +
          'Starter runs the full progressive inventory and lets you pick a quota-limited set of ' +
          'URLs to monitor — that set is analyzed and dashboarded.',
      },
      {
        q: 'What do the Technical and AEO scores mean?',
        a:
          'Each analyzed page gets Technical, AEO, and combined scores computed from a ' +
          'transparent, versioned rule catalog — every rule outcome is inspectable, and missing ' +
          'or failed scores render as — rather than a fabricated zero. Technical covers delivery ' +
          'and on-page fundamentals; AEO covers how ready a page is to be retrieved, cited, and ' +
          'quoted by answer engines.',
      },
      {
        q: 'How does issue remediation work?',
        a:
          'Issues are grouped by severity and dimension across your pages, and each group ' +
          'carries remediation guidance plus navigation into the affected pages. Per-URL ' +
          'diagnostics show delivery facts, normalized page facts, evidence, links, and issue ' +
          'history. Authenticated CSV and Markdown exports, scoped to your workspace, let you ' +
          'hand the work to whoever fixes it.',
      },
    ],
  },
  {
    heading: 'Account & billing',
    items: [
      {
        q: 'How much does Searchify cost?',
        a:
          'Searchify is open source under the MIT license and free to self-host — you pay only ' +
          'your own AI provider usage on your own keys. Hosted plan details will be published before launch.',
      },
      {
        q: 'What’s the refund policy?',
        a: 'Refund terms will be published with hosted-plan availability.',
      },
      {
        q: 'How do I get an invoice?',
        a: 'Invoice options will be published with hosted-plan availability.',
      },
    ],
  },
  {
    heading: 'Open source',
    items: [
      {
        q: 'Is Searchify really open source?',
        a:
          'Yes — MIT licensed, all of it. Backend, frontend, workers, scoring rules, ' +
          `migrations, and docs live in one public repo: ${GITHUB_URL}. Star it, fork it, or ` +
          'read the scoring code before you trust a number.',
      },
      {
        q: 'Can I self-host Searchify?',
        a:
          'Yes. Docker Compose brings up Postgres, the FastAPI backend, the workers, and the ' +
          'Next.js frontend. Run the Alembic migrations, start the frontend, register a user — ' +
          'a workspace is created automatically — then connect a provider key and launch your ' +
          'first audit. The full quick start is in the repo README.',
      },
      {
        q: 'Where do I report bugs or request features?',
        a:
          `File an issue on GitHub — ${GITHUB_URL}/issues. Bugs, feature requests, and docs ` +
          'fixes all land in the same tracker. Contributions are welcome; the conventions and ' +
          'review checklist are in CONTRIBUTING.md.',
      },
    ],
  },
];

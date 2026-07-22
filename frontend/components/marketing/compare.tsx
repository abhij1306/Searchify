import { ArrowRight, Check } from 'lucide-react';
import Link from 'next/link';

import { ByokTrust } from '@/components/marketing/byok-trust';
import { COMPETITORS } from '@/lib/marketing-content/compare';

const FAIRNESS_POINTS = [
  'Deterministic scoring — versioned rules over persisted evidence',
  'BYOK — audits run on your own provider keys',
  'Deterministic, evidence-first scoring — no LLM-as-judge',
] as const;

const FACT_ROWS = [
  { key: 'Engines', value: 'ChatGPT · Gemini · Claude — one audit' },
  { key: 'Scoring', value: 'Deterministic rules, versioned projections' },
  { key: 'Evidence', value: 'Every metric drills to the raw run' },
  { key: 'Keys', value: 'BYOK · Fernet-encrypted at rest' },
  { key: 'Site health', value: 'Technical + AEO auditing built in' },
  { key: 'Deployment', value: 'Cloud or self-host with Docker Compose' },
] as const;

/**
 * CompareIndex — `/compare`: the shared page hero, a competitor-card grid
 * rendered from COMPETITORS (initial-letter tile — no logo assets yet;
 * tagline stays '[TODO(user)]' until first-party research lands), the honest
 * "How we compare fairly" band, and the closing CTA.
 */
export function CompareIndex() {
  return (
    <>
      <header className="page-hero">
        <div className="hero-inner container">
          <span className="eyebrow">Comparisons</span>
          <h1>
            How <span className="grad-text">Searchify</span> compares.
          </h1>
          <p className="hero-sub">
            Side-by-side notes on Searchify and other AI visibility tools — what each covers, how
            scoring works, and where the evidence lives. Maintained by the Searchify team, marked
            wherever we still need to verify.
          </p>
        </div>
      </header>

      <section className="vs-list" aria-label="Competitors">
        <div className="container">
          <div className="list-head">
            <span className="panel-label">Choose a tool</span>
            <span className="vs-count">{COMPETITORS.length} comparisons</span>
          </div>
          <div className="vs-grid">
            {COMPETITORS.map((competitor) => (
              <Link
                className="card vs-card"
                href={`/compare/${competitor.slug}`}
                key={competitor.slug}
              >
                <span className="vs-top">
                  <span className="vs-logo" aria-hidden="true">
                    {competitor.name.charAt(0)}
                  </span>
                  <span className="vs-name">{competitor.name}</span>
                </span>
                <span className="vs-desc">{competitor.tagline}</span>
                <span className="vs-link">
                  Searchify vs {competitor.name}
                  <ArrowRight className="arr" size={14} strokeWidth={2.2} aria-hidden />
                </span>
              </Link>
            ))}
            {COMPETITORS.length === 0 ? (
              <div className="empty-state">
                <p>
                  Comparison research is in progress. We publish pages only after every claim is
                  verified.
                </p>
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <section className="band" aria-label="How we compare fairly">
        <div className="band-inner container">
          <div className="band-copy">
            <span className="eyebrow">How we compare fairly</span>
            <h2 className="h2">
              Compared honestly,
              <br />
              in the <span className="grad-text">open.</span>
            </h2>
            <p>
              Searchify scores deterministically — explicit analyzer and scoring-rule versions ride
              with every projection, so every claim on these pages can be traced to persisted
              evidence. Where a competitor fact isn’t verified, the cell says so instead of
              guessing.
            </p>
            <div className="audit-list">
              {FAIRNESS_POINTS.map((point) => (
                <span className="audit-item" key={point}>
                  <span className="audit-check">
                    <Check strokeWidth={3} aria-hidden />
                  </span>
                  {point}
                </span>
              ))}
            </div>
          </div>
          <div className="card rim fact-card">
            <span className="panel-label">Searchify at a glance</span>
            {FACT_ROWS.map((row) => (
              <div className="fact-row" key={row.key}>
                <span className="fact-k">{row.key}</span>
                <span className="fact-v">{row.value}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="final-cta" aria-label="Get started">
        <div className="container">
          <h2>
            Don’t compare pages.
            <br />
            <span className="grad-text">Compare evidence.</span>
          </h2>
          <p>
            Run the same prompts across ChatGPT, Gemini, and Claude — and read the raw responses
            yourself.
          </p>
          <div className="hero-ctas">
            <Link className="btn btn-primary" href="/register">
              Get started
              <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
            </Link>
            <Link className="btn btn-ghost" href="/faq">
              Read the FAQ
            </Link>
          </div>
          <ByokTrust />
        </div>
      </section>
    </>
  );
}

import { ArrowRight, Check } from 'lucide-react';
import Link from 'next/link';

import { ByokTrust } from './byok-trust';
import { SOV_ROWS, SovRow } from './product-visual';

const AUDIT_POINTS = [
  'Mentions & citations counted from raw text',
  'Every metric links to its run',
  'No LLM-as-judge scoring',
] as const;

/** EvidenceBand — section #evidence: "Numbers you can audit." + drill-down visual. */
export function EvidenceBand() {
  return (
    <section className="band" id="evidence" aria-label="Auditable numbers">
      <div className="band-inner container">
        <div className="band-copy">
          <span className="eyebrow">Evidence</span>
          <h2 className="h2">
            Numbers you
            <br />
            can <span className="grad-text">audit.</span>
          </h2>
          <p>
            Every score links back to the raw response it was computed from — the exact prompt,
            engine, and run. If a metric can’t drill to evidence, it doesn’t ship.
          </p>
          <div className="audit-list">
            {AUDIT_POINTS.map((point) => (
              <span className="audit-item" key={point}>
                <span className="audit-check">
                  <Check strokeWidth={3} aria-hidden />
                </span>
                {point}
              </span>
            ))}
          </div>
        </div>
        <div className="drill">
          <div className="card drill-metric rim">
            <div className="drill-label">Share of voice · ChatGPT</div>
            <SovRow {...SOV_ROWS[0]} />
          </div>
          <div className="drill-connector">
            <span className="line" />
            <span>drills to run #1,247 · ChatGPT</span>
            <span className="line" />
          </div>
          <div className="card drill-raw rim">
            <p className="raw-text">
              “…For early-stage startups, <mark>Acme</mark> is a strong pick — its usage-based
              pricing and same-day setup beat legacy CRMs like Northwind on total cost…”
            </p>
            <div className="raw-cites">
              <span className="chip">[1] acme.com/pricing</span>
              <span className="chip">[2] acme.com/getting-started</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/** FinalCta — the closing call to action with the BYOK trust microcopy. */
export function FinalCta() {
  return (
    <section className="final-cta" id="get-started" aria-label="Get started">
      <div className="container">
        <h2>
          Make AI visibility
          <br />
          <span className="grad-text">measurable.</span>
        </h2>
        <p>Start your first audit in minutes. Bring your own keys, keep the evidence.</p>
        <div className="hero-ctas">
          <Link className="btn btn-primary" href="/register">
            Get started
            <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
          </Link>
          <a className="btn btn-ghost" href="#product">
            See the product
          </a>
        </div>
        <ByokTrust />
      </div>
    </section>
  );
}

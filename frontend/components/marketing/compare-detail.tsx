import { ArrowLeft, ArrowRight, Info } from 'lucide-react';
import Link from 'next/link';

import { ByokTrust } from '@/components/marketing/byok-trust';
import type { Competitor } from '@/lib/marketing-content/compare';
import { GITHUB_URL } from '@/lib/marketing-content/social';

/**
 * CompareDetailView — sync view for `/compare/[competitor]`. The route's
 * default export is a thin async wrapper (Next 16 `params` is a Promise); it
 * resolves the slug and hands the module entry here, so this view stays sync
 * and tests render it directly.
 *
 * Honest framing, per the approved plan: the Searchify column is real copy
 * grounded in the repo docs; the competitor column stays '[TODO(user)]'
 * (rendered as a marked `.todo-tag` pill) until each row is verified
 * first-party, and the page says so under the table. The mockup's
 * "Where Searchify is different." h2 is deliberately NOT copied — h2–h6 may
 * not contain the product name (heading-query convention), so the narrative
 * slots use compliant headings instead.
 */
export function CompareDetailView({ competitor }: Readonly<{ competitor: Competitor }>) {
  return (
    <>
      <header className="vs-hero">
        <div className="content-col container">
          <Link className="back-link" href="/compare">
            <ArrowLeft size={13} strokeWidth={2.2} aria-hidden />
            All comparisons
          </Link>
          <span className="eyebrow">Comparison</span>
          <h1>
            Searchify vs <span className="grad-text">{competitor.name}.</span>
          </h1>
          <p className="hero-sub">
            Two ways to measure brand presence in AI answers. The Searchify column comes straight
            from our docs and source code; the {competitor.name} column stays marked until we verify
            each row.
          </p>
          <div className="vs-meta">
            <span className="chip">{competitor.tagline}</span>
            <span className="chip">Last reviewed · [TODO(user): date]</span>
          </div>
        </div>
      </header>

      <section className="facts-wrap" aria-label="Quick facts">
        <div className="content-col container">
          <div className="facts-head">
            <span className="panel-label">Quick facts</span>
            <span className="facts-note">Searchify column sourced from our docs</span>
          </div>
          <div className="card rim fact-table-card">
            <table className="fact-table">
              <thead>
                <tr>
                  <th scope="col">Dimension</th>
                  <th scope="col" className="col-brand">
                    Searchify
                  </th>
                  <th scope="col">{competitor.name}</th>
                </tr>
              </thead>
              <tbody>
                {competitor.rows.map((row) => (
                  <tr key={row.dimension}>
                    <td className="dim">{row.dimension}</td>
                    <td className="brand">{row.searchify}</td>
                    <td className="comp">
                      {row.competitor.startsWith('[TODO') ? (
                        <span className="todo-tag">{row.competitor}</span>
                      ) : (
                        row.competitor
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="disclaimer" aria-label="About this comparison">
        <div className="content-col container">
          <p className="disclaimer-inner">
            <Info strokeWidth={1.9} aria-hidden />
            <span>
              This comparison is maintained by the Searchify team. The {competitor.name} column is
              pending first-party research — verify current competitor features before quoting. Last
              reviewed [TODO(user): date].
            </span>
          </p>
        </div>
      </section>

      <section className="narrative" aria-label="Narrative comparison">
        <div className="content-col container">
          <div className="todo-block">
            <span className="frame-tag">[TODO(user): narrative]</span>
            <h2>Where we’re different.</h2>
            <p className="slot-hint">
              {
                '// 2–3 paragraphs: deterministic scoring, evidence drill-down, BYOK privacy, open source. Link back to the quick-facts rows above.'
              }
            </p>
          </div>
          <div className="todo-block">
            <span className="frame-tag">[TODO(user): narrative]</span>
            <h2>Where {competitor.name} may fit better.</h2>
            <p className="slot-hint">
              {
                '// 1–2 paragraphs, honest trade-offs only — verified capabilities, no speculation. Mark anything unverified before publishing.'
              }
            </p>
          </div>
          <div className="todo-block">
            <span className="frame-tag">[TODO(user): verdict]</span>
            <h2>Our verdict.</h2>
            <p className="slot-hint">{competitor.verdict}</p>
          </div>
        </div>
      </section>

      <section className="final-cta" aria-label="Get started">
        <div className="container">
          <h2>
            See your own numbers
            <br />
            <span className="grad-text">instead.</span>
          </h2>
          <p>Start your first audit in minutes. Bring your own keys, keep the evidence.</p>
          <div className="hero-ctas">
            <Link className="btn btn-primary" href="/register">
              Get started
              <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
            </Link>
            <a className="btn btn-ghost" href={GITHUB_URL} target="_blank" rel="noreferrer">
              Star on GitHub
            </a>
          </div>
          <ByokTrust />
        </div>
      </section>
    </>
  );
}

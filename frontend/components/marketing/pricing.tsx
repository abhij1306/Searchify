import { ArrowRight, Check, Minus } from 'lucide-react';
import Link from 'next/link';

import { PRICING_TABLE_ROWS, PRICING_TIERS } from '@/lib/marketing-content/pricing';
import type { PricingTier } from '@/lib/marketing-content/pricing';
import { cn } from '@/lib/utils';

/**
 * Pricing page sections (`/pricing`). All plan data renders verbatim from
 * `@/lib/marketing-content/pricing` — prices the user has not filled in yet
 * show the '[TODO(user)]' placeholder on purpose. Structure, copy, and class
 * names follow the approved mockup (`docs` design `page-pricing.html`); the
 * mockup's `.tier`/`.tier-featured` card is `.tier-card`/`.popular` here.
 */

/** Table column order — matches the PricingTableRow fields one to one. */
const TIER_COLUMN_KEYS = PRICING_TIERS.map((tier) => tier.key);
const TIERS_BY_KEY = new Map(PRICING_TIERS.map((tier) => [tier.key, tier]));

function isHighlighted(key: PricingTier['key']) {
  return Boolean(TIERS_BY_KEY.get(key)?.highlighted);
}

/** PricingTiers — the four plan cards driven by PRICING_TIERS. */
export function PricingTiers() {
  return (
    <section className="tiers" aria-label="Plans">
      <div className="container">
        <div className="tiers-grid">
          {PRICING_TIERS.map((tier) => (
            <TierCard key={tier.key} tier={tier} />
          ))}
        </div>
      </div>
    </section>
  );
}

function TierCard({ tier }: { tier: PricingTier }) {
  // Mockup: the "Custom" price drops the per-label; its cadence moves down
  // to the note line under the price.
  const isCustom = tier.price === 'Custom';
  const primaryCta = tier.primaryCta === true;
  return (
    <div className={cn('card tier-card', tier.highlighted && 'popular rim')}>
      {tier.highlighted && <span className="tier-tag">Recommended</span>}
      <div className="tier-head">
        <h3 className="tier-name">{tier.name}</h3>
        <p className="tier-pos">{tier.blurb}</p>        <div className="tier-price">
          <span className="amount">{tier.price}</span>
          {!isCustom && <span className="per">{tier.cadence}</span>}
        </div>
        {isCustom && <div className="tier-note">{tier.cadence}</div>}
      </div>
      <ul className="tier-list">
        {tier.features.map((feature) =>
          feature.startsWith('Everything in') ? (
            <li className="lead" key={feature}>
              {feature}
            </li>
          ) : (
            <li key={feature}>
              <span className="tick">
                <Check strokeWidth={3} aria-hidden />
              </span>
              {feature}
            </li>
          ),
        )}
      </ul>
      <div className="tier-cta">
        <Link className={cn('btn', primaryCta ? 'btn-primary' : 'btn-ghost')} href={tier.cta.href}>
          {tier.cta.label}
          {primaryCta && <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />}
        </Link>
      </div>
    </div>
  );
}

/** PricingTable — the plan comparison grid driven by PRICING_TABLE_ROWS. */
export function PricingTable() {
  return (
    <section className="compare" aria-label="Plan comparison">
      <div className="container">
        <div className="section-head">
          <span className="eyebrow">Compare plans</span>
          <h2 className="h2">
            Same evidence engine.
            <br />
            <span className="grad-text">Different dials.</span>
          </h2>
          <p>
            Capabilities ship to everyone; the tiers differ on volumes, monitoring scope, and
            support.
          </p>
        </div>
        <div className="card compare-card rim">
          <div className="compare-scroll">
            <table className="cmp">
              <thead>
                <tr>
                  <th scope="col">Capability</th>
                  {PRICING_TIERS.map((tier) => (
                    <th
                      className={cn('col-name', tier.highlighted && 'hl')}
                      key={tier.key}
                      scope="col"
                    >
                      {tier.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {PRICING_TABLE_ROWS.map((row) => (
                  <tr key={row.dimension}>
                    <th scope="row">{row.dimension}</th>
                    {TIER_COLUMN_KEYS.map((key) => (
                      <TableCell highlighted={isHighlighted(key)} key={key} value={row[key]} />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

/**
 * One comparison-table cell. The module encodes boolean cells as '✓' / '—'
 * glyphs; those render as lucide Check/Minus icons (with sr-only text so the
 * cell still has an accessible value), everything else renders as text —
 * '[TODO(user)]'-style placeholders get the mockup's mono `.tbd` treatment.
 */
function TableCell({ value, highlighted }: { value: string; highlighted: boolean }) {
  if (value === '✓') {
    return (
      <td className={cn('yes', highlighted && 'hl')}>
        <Check size={14} strokeWidth={2.4} aria-hidden />
        <span className="sr-only">Included</span>
      </td>
    );
  }
  if (value === '—') {
    return (
      <td className={cn('no', highlighted && 'hl')}>
        <Minus size={14} strokeWidth={2.4} aria-hidden />
        <span className="sr-only">Not included</span>
      </td>
    );
  }
  return <td className={cn(value.startsWith('[') && 'tbd', highlighted && 'hl')}>{value}</td>;
}

/** PricingCta — the closing band: start free, or talk to Enterprise. */
export function PricingCta() {
  return (
    <section className="final-cta" aria-label="Get started">
      <div className="container">
        <h2>
          Start with a free sample.
          <br />
          <span className="grad-text">Upgrade on evidence.</span>
        </h2>
        <p>
          Run a deterministic sample crawl of your site — then decide which pages deserve
          monitoring.
        </p>
        <div className="hero-ctas">
          <Link className="btn btn-primary" href="/register">
            Get started
            <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
          </Link>
          <Link className="btn btn-ghost" href="/enterprise">
            Explore Enterprise
          </Link>
        </div>
      </div>
    </section>
  );
}

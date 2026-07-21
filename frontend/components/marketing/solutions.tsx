import { ArrowRight, Briefcase, Building2, Check, Download, Megaphone, Rocket } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

import { ByokTrust } from './byok-trust';
import { SOV_ROWS, SovRow } from './product-visual';

/**
 * SolutionsSections — the full `/solutions` content: shared subpage hero with
 * the segment-chip nav, four audience segment sections (ids `agencies`,
 * `in-house`, `founders`, `pr` — the nav Solutions dropdown targets them), and
 * the closing CTA band. Copy and panel data are verbatim from the approved
 * mockup (/code/.plans/designs/page-solutions.html); the demo brand data
 * ("Acme" et al.) mirrors the landing's fictional dashboard. All visuals are
 * pure CSS/JSX panels — no image assets. SVG paint uses the token classes
 * from marketing.css so this file stays hex-free.
 */

const SEGMENT_CHIPS = [
  { href: '#agencies', label: 'Agencies', Icon: Briefcase },
  { href: '#in-house', label: 'In-house teams', Icon: Building2 },
  { href: '#founders', label: 'Founders', Icon: Rocket },
  { href: '#pr', label: 'PR & communications', Icon: Megaphone },
] as const;

/** SolutionsHero — eyebrow, the page's single <h1>, subcopy, segment chips. */
function SolutionsHero() {
  return (
    <header className="page-hero">
      <div className="hero-inner container">
        <span className="eyebrow">Solutions</span>
        <h1>
          One evidence layer for
          <br />
          <span className="grad-text">every team behind the brand.</span>
        </h1>
        <p className="hero-sub">
          Searchify measures how answer engines talk about you — then hands each team the proof, in
          the format it reports in.
        </p>
        <nav className="seg-nav" aria-label="Solutions by team">
          {SEGMENT_CHIPS.map(({ href, label, Icon }) => (
            <a className="seg-chip" href={href} key={href}>
              <Icon strokeWidth={1.8} aria-hidden />
              {label}
            </a>
          ))}
        </nav>
      </div>
    </header>
  );
}

/** SegmentSection — one audience segment: copy column + product-panel visual. */
function SegmentSection({
  id,
  label,
  eyebrow,
  title,
  pains,
  mappings,
  cta,
  flip = false,
  children,
}: {
  id: string;
  label: string;
  eyebrow: string;
  title: ReactNode;
  pains: readonly string[];
  mappings: readonly string[];
  cta: string;
  flip?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="seg" id={id} aria-label={label}>
      <div className={cn('seg-inner container', flip && 'flip')}>
        <div className="seg-copy">
          <span className="eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
          <div className="col-label">The pain</div>
          <ul className="pain-list">
            {pains.map((pain) => (
              <li key={pain}>
                <span className="pain-dash">—</span>
                {pain}
              </li>
            ))}
          </ul>
          <div className="col-label">How Searchify maps</div>
          <div className="audit-list">
            {mappings.map((mapping) => (
              <span className="audit-item" key={mapping}>
                <span className="audit-check">
                  <Check strokeWidth={3} aria-hidden />
                </span>
                {mapping}
              </span>
            ))}
          </div>
          <div className="seg-cta">
            <Link className="btn btn-ghost btn-sm" href="/register">
              {cta}
              <ArrowRight className="arr" size={14} strokeWidth={2.2} aria-hidden />
            </Link>
          </div>
        </div>
        {children}
      </div>
    </section>
  );
}

/* ── Segment copy (verbatim from the approved mockup) ───────────────────── */

const AGENCIES_PAINS = [
  '“What did this retainer actually get us?” deserves a better answer than screenshots.',
  'AI visibility is a new line item clients can’t verify in their usual dashboards.',
  'Every client runs a different mix of engines, competitors, and prompts.',
] as const;

const AGENCIES_MAPPINGS = [
  'Multi-project workspaces — one project per client, isolated by UUID scoping',
  'Per-client evidence exports — authenticated CSV + Markdown downloads',
  'Competitor benchmarking per market — mentions, citation ownership, share of voice',
  'Deterministic scores the client can re-check — every metric drills to its raw run',
] as const;

const IN_HOUSE_PAINS = [
  'AI answers shape pipeline, but there’s no number that survives the board deck.',
  'Visibility shifts week to week, and nobody trusts the explanation.',
  'Technical and AEO fixes live scattered across crawlers, docs, and gut feel.',
] as const;

const IN_HOUSE_MAPPINGS = [
  'Cross-run trends with engine, time-range, and granularity controls',
  'Site Health + AEO scores with grouped issues, severity, and remediation',
  'Per-URL diagnostics — delivery facts, page facts, evidence, issue history',
  'Share-of-voice benchmarks against the competitors leadership names',
] as const;

const FOUNDERS_PAINS = [
  'Buyers ask ChatGPT, Gemini, and Claude before they ever reach your site.',
  'Enterprise AEO platforms are priced for companies ten times your size.',
  'You need a number you can sanity-check, not another black-box score.',
] as const;

const FOUNDERS_MAPPINGS = [
  'Free sample Site Health crawl — deterministic, seeded, capped URLs',
  'BYOK keeps audit usage on your own provider accounts, at provider rates',
  'Deterministic scoring you can recompute from the raw response',
  'MIT open source — self-host when you outgrow the cloud',
] as const;

const PR_PAINS = [
  'Coverage lands, but you can’t see whether AI answers pick it up.',
  'Engines cite competitor pages for the narratives your team owns.',
  'Impact reports stop at reach and sentiment — nothing about answers.',
] as const;

const PR_MAPPINGS = [
  'Mention + citation tracking with raw-response evidence for every claim',
  'Citation ownership benchmarking — whose pages get cited, per prompt',
  'Query-fanout evidence — how one question expands into real engine queries',
  'CSV + Markdown exports that drop straight into coverage reports',
] as const;

/* ── Segment visuals (product panels, pure CSS/JSX) ─────────────────────── */

/** AgenciesVisual — per-client share-of-voice report with evidence exports. */
function AgenciesVisual() {
  return (
    <div className="seg-viz">
      <div className="seg-viz-glow" aria-hidden="true" />
      <div className="panel rim">
        <div className="panel-head">
          <span className="panel-label">Client report — share of voice</span>
          <span className="chip">Acme · Q3</span>
        </div>
        <div className="sov-rows">
          {SOV_ROWS.map((row) => (
            <SovRow key={row.name} {...row} />
          ))}
        </div>
        <div className="export-row">
          <span className="export-chip">
            <Download strokeWidth={2} aria-hidden />
            acme-q3-mentions.csv
          </span>
          <span className="export-chip">
            <Download strokeWidth={2} aria-hidden />
            acme-q3-evidence.md
          </span>
        </div>
      </div>
    </div>
  );
}

/** Trend sparkline path; the area fill closes it along the baseline. */
const SPARK_LINE =
  'M0 62 L27 58 L54 60 L81 52 L108 54 L135 45 L162 47 L189 37 L216 39 L243 29 L270 25 L300 16';

const HEALTH_GAUGES = [
  { name: 'Technical', value: 82 },
  { name: 'AEO', value: 64 },
] as const;

/** GaugeRows — Technical/AEO score bars shared by the Site Health panels. */
function GaugeRows() {
  return (
    <div className="gauge-rows">
      {HEALTH_GAUGES.map(({ name, value }) => (
        <div className="gauge-row" key={name}>
          <span className="gauge-name">{name}</span>
          <span className="gauge-track">
            <span className="gauge-fill" style={{ width: `${value}%` }} />
          </span>
          <span className="gauge-val">{value}</span>
        </div>
      ))}
    </div>
  );
}

/** IssueLine — grouped-issues severity summary for the Site Health panels. */
function IssueLine() {
  return (
    <div className="issue-line">
      <span>
        <b>3</b> critical
      </span>
      <span aria-hidden="true">·</span>
      <span>
        <b>5</b> high
      </span>
      <span aria-hidden="true">·</span>
      <span>
        <b>12</b> medium issues grouped
      </span>
    </div>
  );
}

/** InHouseVisual — cross-run trend panel + this-crawl Site Health panel. */
function InHouseVisual() {
  return (
    <div className="seg-viz">
      <div className="seg-viz-glow" aria-hidden="true" />
      <div className="panel rim">
        <div className="panel-head">
          <span className="panel-label">Visibility trend — last 90 days</span>
          <span className="delta">▲ 12 pts vs prior period</span>
        </div>
        <svg className="spark" viewBox="0 0 300 76" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient
              id="sparkStrokeSol"
              x1="0"
              y1="0"
              x2="300"
              y2="0"
              gradientUnits="userSpaceOnUse"
            >
              <stop className="spark-stop-a" offset="0" />
              <stop className="spark-stop-b" offset="1" />
            </linearGradient>
          </defs>
          <path className="spark-area" d={`${SPARK_LINE} L300 76 L0 76 Z`} />
          <path
            d={SPARK_LINE}
            fill="none"
            stroke="url(#sparkStrokeSol)"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <circle className="spark-dot" cx="300" cy="16" r="3.6" />
        </svg>
        <div className="mini-stats">
          <span>
            <b>128</b> mentions
          </span>
          <span>
            <b>96</b> citations
          </span>
          <span>
            <b>240</b> runs
          </span>
          <span>
            <b>3</b> engines
          </span>
        </div>
      </div>
      <div className="panel rim">
        <div className="panel-head">
          <span className="panel-label">Site Health — this crawl</span>
          <span className="chip">monitored set</span>
        </div>
        <GaugeRows />
        <IssueLine />
      </div>
    </div>
  );
}

/** FoundersVisual — the free-tier deterministic sample crawl panel. */
function FoundersVisual() {
  return (
    <div className="seg-viz">
      <div className="seg-viz-glow" aria-hidden="true" />
      <div className="panel rim">
        <div className="panel-head">
          <span className="panel-label">Site Health — sample crawl</span>
          <span className="chip">free tier</span>
        </div>
        <GaugeRows />
        <IssueLine />
        <div className="export-row">
          <span className="chip">deterministic sample</span>
          <span className="chip">seeded · read-only</span>
        </div>
      </div>
    </div>
  );
}

const NARRATIVE_ROWS = [
  {
    prompt: '“best payroll tools for remote-first teams”',
    engine: 'ChatGPT',
    dot: 'dot-1',
    mentioned: true,
    citations: '2 citations',
  },
  {
    prompt: '“most secure payroll platforms ranked”',
    engine: 'Claude',
    dot: 'dot-3',
    mentioned: false,
    citations: '0 citations',
  },
] as const;

/** PrVisual — narrative evidence table with raw citations + query fanout. */
function PrVisual() {
  return (
    <div className="seg-viz">
      <div className="seg-viz-glow" aria-hidden="true" />
      <div className="evidence rim">
        <div className="evidence-head">
          <span className="panel-label">Narrative evidence — latest runs</span>
          <span className="chip">Acme</span>
        </div>
        {NARRATIVE_ROWS.map(({ prompt, engine, dot, mentioned, citations }) => (
          <div className="evidence-row" key={prompt}>
            <span className="ev-prompt">{prompt}</span>
            <span className="ev-engine">
              <span className={cn('engine-dot', dot)} />
              {engine}
            </span>
            <span className={cn('badge', mentioned ? 'badge-yes' : 'badge-no')}>
              {mentioned ? '✓ Mentioned' : 'Not mentioned'}
            </span>
            <span className="ev-meta">{citations}</span>
          </div>
        ))}
        <div className="raw-cites">
          <span className="chip">[1] acme.com/press/series-c</span>
          <span className="chip">[2] techjournal.com/payroll-review</span>
        </div>
        <div className="fanout-line">
          ↳ query fanout: <b>6 related queries</b> captured for this prompt
        </div>
      </div>
    </div>
  );
}

/** SolutionsFinalCta — the closing band: role-agnostic pitch + trust copy. */
function SolutionsFinalCta() {
  return (
    <section className="final-cta" id="get-started" aria-label="Get started">
      <div className="container">
        <h2>
          Pick your workflow.
          <br />
          <span className="grad-text">Keep the evidence.</span>
        </h2>
        <p>
          Every role reads the same underlying runs — the difference is the report you build from
          them.
        </p>
        <div className="hero-ctas">
          <Link className="btn btn-primary" href="/register">
            Get started
            <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
          </Link>
          <Link className="btn btn-ghost" href="/pricing">
            See pricing
          </Link>
        </div>
        <ByokTrust />
      </div>
    </section>
  );
}

/** SolutionsSections — hero + the four segment sections + closing CTA band. */
export function SolutionsSections() {
  return (
    <>
      <SolutionsHero />
      <div className="segs">
        <SegmentSection
          id="agencies"
          label="For agencies"
          eyebrow="For agencies"
          title={
            <>
              Client-proof reporting,
              <br />
              down to the <span className="grad-text">raw answer.</span>
            </>
          }
          pains={AGENCIES_PAINS}
          mappings={AGENCIES_MAPPINGS}
          cta="Start a client workspace"
        >
          <AgenciesVisual />
        </SegmentSection>
        <SegmentSection
          id="in-house"
          label="For in-house teams"
          eyebrow="For in-house teams"
          title={
            <>
              Show leadership the trend.
              <br />
              <span className="grad-text">Then fix what drags it.</span>
            </>
          }
          pains={IN_HOUSE_PAINS}
          mappings={IN_HOUSE_MAPPINGS}
          cta="Start monitoring"
          flip
        >
          <InHouseVisual />
        </SegmentSection>
        <SegmentSection
          id="founders"
          label="For founders"
          eyebrow="For founders"
          title={
            <>
              Find out if AI recommends you —
              <br />
              <span className="grad-text">on a startup budget.</span>
            </>
          }
          pains={FOUNDERS_PAINS}
          mappings={FOUNDERS_MAPPINGS}
          cta="Run a free sample"
        >
          <FoundersVisual />
        </SegmentSection>
        <SegmentSection
          id="pr"
          label="For PR and communications"
          eyebrow="For PR & communications"
          title={
            <>
              See which stories the
              <br />
              <span className="grad-text">answer engines repeat.</span>
            </>
          }
          pains={PR_PAINS}
          mappings={PR_MAPPINGS}
          cta="Track your narrative"
          flip
        >
          <PrVisual />
        </SegmentSection>
      </div>
      <SolutionsFinalCta />
    </>
  );
}

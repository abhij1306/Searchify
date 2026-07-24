import { cn } from '@/lib/utils';

/**
 * ProductVisual — the hero's floating dashboard mock. Fictional "Acme" data,
 * self-consistent by construction (68 ≈ avg(74, 66, 63); SOV 42+27+19+12=100;
 * 240 runs = 8 prompts × 3 engines × 10 reps). SVG paint uses token classes
 * from marketing.css so this file stays hex-free.
 */
export const SOV_ROWS = [
  { name: 'Acme', value: 42, you: true },
  { name: 'Northwind', value: 27, you: false },
  { name: 'Contoso', value: 19, you: false },
  { name: 'Globex', value: 12, you: false },
] as const;

/** SovRow — one share-of-voice bar row; also used by the evidence band drill. */
export function SovRow({
  name,
  value,
  you,
}: Readonly<{ name: string; value: number; you?: boolean }>) {
  return (
    <div className={cn('sov-row', you && 'you')}>
      <span className="sov-name">{name}</span>
      <span className="sov-track">
        <span className="sov-fill" style={{ width: `${value}%` }} />
      </span>
      <span className="sov-val">{value}%</span>
    </div>
  );
}

const ENGINES = [
  { name: 'ChatGPT', dot: 'dot-1', score: 74, delta: '+9' },
  { name: 'Gemini', dot: 'dot-2', score: 66, delta: '+14' },
  { name: 'Claude', dot: 'dot-3', score: 63, delta: '+11' },
] as const;

/** Sparkline stroke path; the area fill closes it along the baseline. */
const SPARK_LINE =
  'M0 62 L27 58 L54 60 L81 52 L108 54 L135 45 L162 47 L189 37 L216 39 L243 29 L270 25 L300 16';

const EVIDENCE_ROWS = [
  {
    prompt: '“best crm for early-stage startups”',
    engine: 'ChatGPT',
    dot: 'dot-1',
    mentioned: true,
    citations: '2 citations',
    ago: '2m ago',
  },
  {
    prompt: '“acme vs northwind for sales teams”',
    engine: 'Gemini',
    dot: 'dot-2',
    mentioned: true,
    citations: '3 citations',
    ago: '14m ago',
  },
  {
    prompt: '“top pipeline tools ranked by cost”',
    engine: 'Claude',
    dot: 'dot-3',
    mentioned: false,
    citations: '0 citations',
    ago: '31m ago',
  },
] as const;

export function ProductVisual() {
  return (
    <section className="viz" id="product" aria-label="Searchify visibility dashboard preview">
      <div className="viz-stage container">
        <div className="viz-glow" aria-hidden="true" />
        <div className="viz-grid" aria-hidden="true" />
        <div className="dash rim">
          <div className="dash-topbar">
            <span className="ws-chip">
              <span className="ws-avatar">A</span>Acme Corp <span className="caret">▾</span>
            </span>
            <span className="chip">Q3 audit · 240 runs</span>
            <span className="spacer" />
            <span className="chip">
              <span className="live-dot" />
              Last 30 days
            </span>
          </div>
          <div className="dash-grid">
            <div className="panel">
              <div className="panel-label">Visibility score</div>
              <div className="score-row">
                <span className="score-num">68</span>
                <span className="score-denom">/100</span>
                <span className="delta">▲ 12 pts vs prior 30 days</span>
              </div>
              <svg
                className="spark"
                viewBox="0 0 300 76"
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <defs>
                  <linearGradient
                    id="sparkStroke"
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
                  stroke="url(#sparkStroke)"
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
              </div>
            </div>
            <div className="panel">
              <div className="sov-head">
                <div className="panel-label">Share of voice</div>
                <span className="sov-cap">mentions across engines</span>
              </div>
              <div className="sov-rows">
                {SOV_ROWS.map((row) => (
                  <SovRow key={row.name} {...row} />
                ))}
              </div>
            </div>
          </div>
          <div className="engine-tiles">
            {ENGINES.map(({ name, dot, score, delta }) => (
              <div className="engine-tile" key={name}>
                <span className="engine-name">
                  <span className={`engine-dot ${dot}`} />
                  {name}
                </span>
                <div className="engine-score">
                  <b>{score}</b>
                  <span>{delta}</span>
                </div>
                <div className="engine-bar">
                  <i style={{ width: `${score}%` }} />
                </div>
              </div>
            ))}
          </div>
          <div className="evidence">
            <div className="evidence-head">
              <span className="panel-label">Evidence — latest runs</span>
              <span className="view-link">View all runs →</span>
            </div>{' '}
            {EVIDENCE_ROWS.map(({ prompt, engine, dot, mentioned, citations, ago }) => (
              <div className="evidence-row" key={prompt}>
                <span className="ev-prompt">{prompt}</span>
                {/* `display: contents` on desktop (children stay grid items);
                    a flex `meta` cell at ≤640px so engine/badge/meta flow
                    inline instead of overlapping in one grid area. */}
                <span className="ev-meta-row">
                  <span className="ev-engine">
                    <span className={`engine-dot ${dot}`} />
                    {engine}
                  </span>
                  <span className={cn('badge', mentioned ? 'badge-yes' : 'badge-no')}>
                    {mentioned ? '✓ Mentioned' : 'Not mentioned'}
                  </span>
                  <span className="ev-meta">{citations}</span>
                  <span className="ev-meta">{ago}</span>
                </span>
                <span className="ev-chev">›</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

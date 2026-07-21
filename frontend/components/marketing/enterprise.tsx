import {
  ArrowRight,
  Check,
  Cloud,
  CodeXml,
  Layers,
  Server,
  Shield,
  ShieldCheck,
  Sigma,
  type LucideIcon,
} from 'lucide-react';
import { Fragment } from 'react';

import { CONTACT_EMAIL, GITHUB_URL, LICENSE_URL } from '@/lib/marketing-content/social';

import { ByokTrust } from './byok-trust';

/**
 * Sales contact href — renders `mailto:` only once the user sets a public
 * contact email in the social content module; a placeholder anchor until then.
 */
const CONTACT_HREF = CONTACT_EMAIL ? `mailto:${CONTACT_EMAIL}` : '#';

/** Architecture docs, derived from the canonical repo URL (docs/ ships in-repo). */
const DOCS_URL = `${GITHUB_URL}/tree/main/docs`;

type OpsCard = {
  icon: LucideIcon;
  title: string;
  blurb: string;
  points: readonly string[];
};

/**
 * Ops grid — every claim is grounded in the README's "Built for trustworthy
 * operations" list (UUID workspace isolation, immutable artifacts +
 * provenance, PostgreSQL durable queues, same-origin proxying, typed
 * contracts). No certification/compliance claims — nothing the repo can't
 * ground.
 */
const OPS_CARDS: readonly OpsCard[] = [
  {
    icon: Shield,
    title: 'Security & privacy',
    blurb: 'Provider credentials stay secret, and backend topology stays server-side.',
    points: [
      'Strict workspace isolation — UUID identifiers throughout',
      'BYOK keys Fernet-encrypted at rest, write-only after save',
      'Same-origin API proxying — backend topology never reaches the client bundle',
    ],
  },
  {
    icon: Sigma,
    title: 'Audit-ready evidence',
    blurb: 'Numbers your compliance team can re-derive, not just read.',
    points: [
      'Deterministic scoring — analyzer + rule versions on every projection',
      'Immutable artifacts + provenance-carrying analyses, written once',
      'Unsupported metrics render as —, never fabricated zeros',
    ],
  },
  {
    icon: Layers,
    title: 'Scale & reliability',
    blurb: 'Orchestration that survives worker restarts and Monday-morning queues.',
    points: [
      'PostgreSQL durable queues — FOR UPDATE SKIP LOCKED, no Redis dependency',
      'Leases, heartbeats, retries, and idempotency on every task',
      'Custom audit + crawl volumes on enterprise plans — [TODO(user)]',
    ],
  },
  {
    icon: Server,
    title: 'Self-host & openness',
    blurb: 'The whole platform, under the MIT license.',
    points: [
      'Audit every line — full source on GitHub',
      'Docker Compose topology — frontend, API, workers, PostgreSQL',
      'Typed contracts validated at runtime — Zod + Pydantic',
    ],
  },
];

type DeployCard = {
  icon: LucideIcon;
  title: string;
  blurb: string;
  points: readonly string[];
  links?: readonly { label: string; href: string }[];
};

const DEPLOY_CARDS: readonly DeployCard[] = [
  {
    icon: Cloud,
    title: 'Managed cloud',
    blurb: 'Managed by CUBE27 — you bring the keys.',
    points: [
      'Managed workers, queues, and upgrades',
      'Workspace isolation with UUID scoping throughout',
      'Custom volumes, seats, and retention — [TODO(user)]',
      'Support + SLA options — [TODO(user)]',
    ],
  },
  {
    icon: Server,
    title: 'Self-hosted',
    blurb: 'The same MIT codebase, inside your network.',
    points: [
      'Docker Compose quickstart — web, workers, PostgreSQL',
      'Your ENCRYPTION_KEY wraps every BYOK secret',
      'Crawler + provider traffic stays inside your egress rules',
      'Typed /api/v1 contracts for internal integrations',
    ],
    links: [
      { label: 'Full source on GitHub', href: GITHUB_URL },
      { label: 'MIT license', href: LICENSE_URL },
    ],
  },
];

/** Data flow shown under the deployment cards (grounded in docs/architecture). */
const ARCH_FLOW: readonly { node: string; arrow: string }[] = [
  { node: 'Browser', arrow: '→' },
  { node: 'Next.js same-origin proxy', arrow: '→' },
  { node: 'FastAPI', arrow: '→' },
  { node: 'PostgreSQL', arrow: '⇄' },
  { node: 'Workers', arrow: '→' },
];

type LimitCell = {
  label: string;
  desc: string;
};

/** Enterprise agreement dials — every value is user-fillable business data. */
const LIMIT_CELLS: readonly LimitCell[] = [
  { label: 'Monthly audit runs', desc: 'prompt × engine × repetition, aggregated across projects' },
  { label: 'Monitored URLs', desc: 'total monitored set across all projects' },
  { label: 'Projects', desc: 'per workspace, each with its own prompts + competitors' },
  { label: 'Seats', desc: 'workspace members with access to audits + evidence' },
  { label: 'Evidence retention', desc: 'immutable artifacts, runs, and derived projections' },
  { label: 'Support & SLA', desc: 'response targets, channels, and escalation path' },
];

function CheckItem({ children }: { children: string }) {
  return (
    <span className="audit-item">
      <span className="audit-check">
        <Check strokeWidth={3} aria-hidden />
      </span>
      {children}
    </span>
  );
}

/**
 * EnterpriseHero — the shared `.page-hero` block (eyebrow, the page's single
 * h1, subcopy, CTAs, trust microcopy). Copy is verbatim from the approved
 * mockup; the contact CTA falls back to a placeholder anchor until
 * CONTACT_EMAIL is set.
 */
export function EnterpriseHero() {
  return (
    <header className="page-hero">
      <div className="hero-inner container">
        <span className="eyebrow">Enterprise</span>
        <h1>
          AI visibility, with
          <br />
          <span className="grad-text">enterprise-grade evidence.</span>
        </h1>
        <p className="hero-sub">
          The platform security teams can verify: deterministic scoring over immutable,
          provenance-carrying evidence — deployed in our cloud, or self-hosted from the MIT-licensed
          codebase.
        </p>
        <div className="hero-ctas">
          <a className="btn btn-primary" href={CONTACT_HREF}>
            Contact sales
            <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
          </a>
          <a className="btn btn-ghost" href={GITHUB_URL} target="_blank" rel="noreferrer">
            View the codebase
          </a>
        </div>
        <div className="trust">
          <span>
            <CodeXml strokeWidth={1.8} aria-hidden />
            MIT open source
          </span>
          <span className="sep" aria-hidden="true">
            ·
          </span>
          <span>
            <ShieldCheck strokeWidth={1.8} aria-hidden />
            Workspace-isolated by UUID
          </span>
          <span className="sep" aria-hidden="true">
            ·
          </span>
          <span>
            <Sigma strokeWidth={1.8} aria-hidden />
            No LLM-as-judge scoring
          </span>
        </div>
      </div>
    </header>
  );
}

/** EnterpriseOps — the trustworthy-operations capability grid. */
export function EnterpriseOps() {
  return (
    <section className="capabilities" id="capabilities" aria-label="Enterprise capabilities">
      <div className="container">
        <div className="section-head">
          <span className="eyebrow">Capabilities</span>
          <h2 className="h2">
            Built for teams that
            <br />
            <span className="grad-text">audit their tools.</span>
          </h2>
          <p>Every claim below maps to the open-source codebase — bring your security review.</p>
        </div>
        <div className="cap-grid">
          {OPS_CARDS.map((card) => (
            <div className="card cap-card" key={card.title}>
              <span className="f-icon">
                <card.icon strokeWidth={1.8} aria-hidden />
              </span>
              <h3>{card.title}</h3>
              <p>{card.blurb}</p>
              <div className="audit-list">
                {card.points.map((point) => (
                  <CheckItem key={point}>{point}</CheckItem>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/**
 * EnterpriseSelfHost — deployment band: managed cloud vs. self-hosting the
 * MIT-licensed codebase (real GitHub/license links), plus the platform data
 * flow.
 */
export function EnterpriseSelfHost() {
  return (
    <section className="band" id="deployment" aria-label="Deployment options">
      <div className="container">
        <div className="section-head">
          <span className="eyebrow">Deployment</span>
          <h2 className="h2">
            Our cloud, or
            <br />
            <span className="grad-text">your infrastructure.</span>
          </h2>
          <p>Same codebase, same deterministic pipeline — pick where it runs.</p>
        </div>
        <div className="deploy-grid">
          {DEPLOY_CARDS.map((card) => (
            <div className="card deploy-card rim" key={card.title}>
              <div className="deploy-head">
                <span className="f-icon">
                  <card.icon strokeWidth={1.8} aria-hidden />
                </span>
                <h3>{card.title}</h3>
              </div>
              <p className="pos">{card.blurb}</p>
              <div className="audit-list">
                {card.points.map((point) => (
                  <CheckItem key={point}>{point}</CheckItem>
                ))}
              </div>
              {card.links && (
                <div className="deploy-links">
                  {card.links.map((link) => (
                    <a
                      className="view-link"
                      href={link.href}
                      target="_blank"
                      rel="noreferrer"
                      key={link.label}
                    >
                      {link.label}
                    </a>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="arch-flow" aria-label="Platform data flow">
          {ARCH_FLOW.map((step) => (
            <Fragment key={step.node}>
              <span className="arch-node">{step.node}</span>
              <span className="arch-arr" aria-hidden="true">
                {step.arrow}
              </span>
            </Fragment>
          ))}
          <span className="arch-node">AI providers · BYOK</span>
        </div>
      </div>
    </section>
  );
}

/** EnterpriseLimits — the custom-limits dials; all values are [TODO(user)]. */
export function EnterpriseLimits() {
  return (
    <section className="limits" id="limits" aria-label="Custom limits">
      <div className="container">
        <div className="section-head">
          <span className="eyebrow">Custom limits</span>
          <h2 className="h2">
            Shaped around
            <br />
            <span className="grad-text">your requirements.</span>
          </h2>
          <p>
            Every enterprise agreement starts from these dials — tell us the volumes and we size the
            plan.
          </p>
        </div>
        <div className="limits-grid">
          {LIMIT_CELLS.map((cell) => (
            <div className="card limit-cell" key={cell.label}>
              <div className="panel-label">{cell.label}</div>
              {/* TODO(user): enterprise limit value */}
              <div className="limit-val">[TODO(user)]</div>
              <div className="limit-desc">{cell.desc}</div>
            </div>
          ))}
        </div>
        <p className="trust-note">
          Searchify does not claim SOC 2 or ISO certifications today.{' '}
          <b>What it offers is verifiable:</b> an MIT-licensed codebase, deterministic scoring, and
          evidence your team can audit line by line.
        </p>
      </div>
    </section>
  );
}

/** EnterpriseContactCta — closing contact band (mailto once CONTACT_EMAIL is set). */
export function EnterpriseContactCta() {
  return (
    <section className="final-cta" id="contact" aria-label="Contact sales">
      <div className="container">
        <h2>
          Bring AI visibility
          <br />
          <span className="grad-text">in-house.</span>
        </h2>
        <p>
          Tell us your volumes, constraints, and review process — we’ll shape an enterprise plan
          around them.
        </p>
        <div className="hero-ctas">
          <a className="btn btn-primary" href={CONTACT_HREF}>
            Contact sales
            <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
          </a>
          <a className="btn btn-ghost" href={DOCS_URL} target="_blank" rel="noreferrer">
            Read the architecture docs
          </a>
        </div>
        <ByokTrust />
      </div>
    </section>
  );
}

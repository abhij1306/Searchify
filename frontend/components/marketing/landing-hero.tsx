import { ArrowRight } from 'lucide-react';
import Link from 'next/link';

import { ByokTrust } from './byok-trust';

/**
 * LandingHero — eyebrow, the page's single <h1>, subcopy, primary/ghost CTAs
 * and the BYOK trust microcopy. Copy is verbatim from the approved mockup.
 */
export function LandingHero() {
  return (
    <header className="hero">
      <div className="hero-inner container">
        <div className="hero-badge">
          <span className="eyebrow">Answer-engine optimization</span>
        </div>
        <h1>
          See how <span className="grad-text">AI{'\u00a0'}answers</span> talk about your brand.
        </h1>
        <p className="hero-sub">
          Searchify audits ChatGPT, Gemini, and Claude with the prompts your buyers actually ask.
          Every response is scored deterministically, with the raw evidence attached.
        </p>
        <div className="hero-ctas">
          <Link className="btn btn-primary" href="/register">
            Get started
            <ArrowRight className="arr" size={15} strokeWidth={2.2} aria-hidden />
          </Link>
          <a className="btn btn-ghost" href="#how-it-works">
            See how it works
          </a>
        </div>
        <ByokTrust />
      </div>
    </header>
  );
}

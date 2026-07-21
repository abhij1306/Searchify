import { BarChart3, Eye, Globe, KeyRound, Sigma, TrendingUp } from 'lucide-react';

const FEATURES = [
  {
    icon: Globe,
    title: 'Three-engine coverage',
    body: 'One audit queries ChatGPT, Gemini, and Claude side by side. Same prompts, same repetitions, comparable scores.',
  },
  {
    icon: Sigma,
    title: 'Deterministic scoring',
    body: 'Mentions, citations, and share-of-voice are computed from the raw response text. Same data, same score.',
  },
  {
    icon: Eye,
    title: 'Evidence explorer',
    body: 'Every metric links to the exact run it came from. Open the raw response in Runs and check the math yourself.',
  },
  {
    icon: BarChart3,
    title: 'Competitor benchmarking',
    body: 'Track the competitors that matter. Watch share-of-voice shift across engines, prompt by prompt.',
  },
  {
    icon: KeyRound,
    title: 'BYOK privacy',
    body: 'Audits run on your own provider keys. Fernet-encrypted at rest, write-only, never returned by the API.',
  },
  {
    icon: TrendingUp,
    title: 'Repeatable trends',
    body: 'Rerun audits on your cadence. Watch visibility move period over period, engine over engine.',
  },
] as const;

/** FeaturesGrid — section #features: what an audit gives you, six cards. */
export function FeaturesGrid() {
  return (
    <section className="features" id="features" aria-label="Features">
      <div className="container">
        <div className="section-head">
          <span className="eyebrow">What you get</span>
          <h2 className="h2">
            See every answer.
            <br />
            <span className="grad-text">Know what to do next.</span>
          </h2>
          <p>
            One workspace for your brand, your competitors, and the prompts that decide who gets
            recommended.
          </p>
        </div>
        <div className="features-grid">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div className="card feature-card" key={title}>
              <span className="f-icon">
                <Icon strokeWidth={1.8} aria-hidden />
              </span>
              <h3>{title}</h3>
              <p>{body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

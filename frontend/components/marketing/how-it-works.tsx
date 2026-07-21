const STEPS = [
  {
    num: '01',
    title: 'Define your workspace',
    body: 'Your brand, the competitors you watch, and the prompts your buyers ask answer engines.',
  },
  {
    num: '02',
    title: 'Run the audit',
    body: 'Each prompt executes across ChatGPT, Gemini, and Claude. Repeated runs on your own API keys.',
  },
  {
    num: '03',
    title: 'Read the evidence',
    body: 'Scores roll up to a visibility dashboard. Every number drills down to the raw response behind it.',
  },
] as const;

/** HowItWorks — section #how-it-works: prompts to proof in three steps. */
export function HowItWorks() {
  return (
    <section className="how" id="how-it-works" aria-label="How it works">
      <div className="container">
        <div className="section-head">
          <span className="eyebrow">How it works</span>
          <h2 className="h2">
            From prompts to proof
            <br />
            in <span className="grad-text">three steps.</span>
          </h2>
        </div>
        <div className="steps">
          {STEPS.map(({ num, title, body }) => (
            <div className="card step-card" key={num}>
              <span className="step-num">{num}</span>
              <h3>{title}</h3>
              <p>{body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

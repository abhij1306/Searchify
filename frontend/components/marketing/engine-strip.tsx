const STRIP_ENGINES = [
  { name: 'ChatGPT', dot: 'dot-1' },
  { name: 'Gemini', dot: 'dot-2' },
  { name: 'Claude', dot: 'dot-3' },
] as const;

/** EngineStrip — the three answer engines every audit runs across. */
export function EngineStrip() {
  return (
    <section className="engine-strip" aria-label="Supported answer engines">
      <div className="container">
        <span className="eyebrow">Measured where answers happen</span>
        <div className="engine-chips">
          {STRIP_ENGINES.map(({ name, dot }) => (
            <span className="engine-chip rim" key={name}>
              <span className={`engine-dot ${dot}`} />
              {name}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

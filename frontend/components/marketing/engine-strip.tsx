import { EngineMark } from './engine-mark';

const STRIP_ENGINES = [
  { name: 'ChatGPT', brand: 'chatgpt' },
  { name: 'Gemini', brand: 'gemini' },
  { name: 'Claude', brand: 'claude' },
] as const;

/** EngineStrip — the three answer engines every audit runs across. */
export function EngineStrip() {
  return (
    <section className="engine-strip" aria-label="Supported answer engines">
      <div className="container">
        <span className="eyebrow">Measured where answers happen</span>
        <div className="engine-chips">
          {STRIP_ENGINES.map(({ name, brand }) => (
            <span className={`engine-chip engine-chip-${brand} rim`} key={name}>
              <EngineMark name={name} />
              <span className="engine-wordmark">{name}</span>
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

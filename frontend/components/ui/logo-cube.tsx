/**
 * LogoCube — the CUBE27 isometric-cube mark (geometry lifted from the company
 * favicon) rendered with theme-aware paint classes so this file stays hex-free
 * (logo-tile / logo-stroke / logo-facet). The default paints live in
 * app/globals.css (token-driven, theme-inverting); the marketing `.mkt` rules
 * in marketing.css stay more specific and keep the landing's dark tile.
 */
export function LogoCube({ size = 28 }: Readonly<{ size?: number }>) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <rect className="logo-tile" x="1" y="1" width="62" height="62" rx="13" strokeWidth="2" />
      <path
        className="logo-stroke"
        d="M32 10 54 22v20L32 54 10 42V22L32 10Z"
        fill="none"
        strokeWidth="4"
        strokeLinejoin="round"
      />
      <path
        className="logo-stroke"
        d="m10 22 22 12 22-12M32 34v20"
        fill="none"
        strokeWidth="4"
        strokeLinejoin="round"
      />
      <path className="logo-facet" d="m32 34 11-6v12l-11 6V34Z" />
    </svg>
  );
}

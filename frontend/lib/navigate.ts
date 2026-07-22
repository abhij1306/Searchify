/**
 * Hard-navigation seam. Runtime behavior is exactly
 * `window.location.assign(url)`; the indirection exists because jsdom 26
 * defines `Location#assign` (and `window.location` itself) as
 * non-configurable, so tests cannot stub the method on the Location instance
 * — they `vi.mock` this module instead. Used by the OAuth start flow, which
 * hands the browser to the provider's authorize URL.
 */
export function assignLocation(url: string): void {
  window.location.assign(url);
}

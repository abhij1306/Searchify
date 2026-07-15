import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, 'globals.css'), 'utf8');
const design = readFileSync(join(here, '..', '..', 'docs', 'design.md'), 'utf8');

/**
 * Asserts the globals.css token set matches docs/design.md. Every raw CSS
 * variable declared in the design.md code blocks (§3–§6) must appear in
 * globals.css. This is the F1 acceptance guard "token set matches design.md".
 */
describe('globals.css token set matches docs/design.md', () => {
  it('defines both theme blocks and the @theme bridge', () => {
    expect(css).toMatch(/:root\s*\{/);
    expect(css).toMatch(/html\[data-theme='dark'\]\s*\{/);
    expect(css).toMatch(/@theme inline\s*\{/);
  });

  it('declares every raw --token documented in design.md', () => {
    // Collect --token names from design.md's CSS code fences (declarations only).
    const declared = new Set<string>();
    for (const m of design.matchAll(/--([a-z0-9-]+)\s*:/gi)) {
      declared.add(m[1]);
    }
    // design.md also references tokens via var(); keep only ones it declares.
    expect(declared.size).toBeGreaterThan(80);

    const missing: string[] = [];
    for (const name of declared) {
      const re = new RegExp(`--${name.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}\\s*:`);
      if (!re.test(css)) missing.push(name);
    }
    expect(missing, `Tokens in design.md missing from globals.css: ${missing.join(', ')}`).toEqual([]);
  });

  it('keeps the teal-green accent (not the reference blue-violet)', () => {
    expect(css).toMatch(/--accent:\s*#0f9d76/);
    expect(css).toMatch(/--accent:\s*#2dd4a7/); // dark
    expect(css).not.toMatch(/#3557f6/); // CrawlerAI light accent
  });
});

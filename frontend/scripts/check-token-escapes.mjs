/**
 * Token-escape + no-raw-hex guard (F1).
 *
 * Enforces the design.md token policy:
 *   1. Components must not use raw CSS-var Tailwind escapes like
 *      `bg-[var(--foo)]` — they must use the bridged semantic utilities.
 *   2. Raw hex colors live ONLY in app/globals.css theme blocks. No hex may
 *      appear in any component/app .tsx/.ts source.
 *   3. globals.css itself parses and contains the expected theme structure.
 *
 * Run: node scripts/check-token-escapes.mjs
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
const SEARCH_ROOTS = ['app', 'components', 'lib'];
const TOKEN_ESCAPE_PATTERN = /\b(?:bg|text|border|shadow|ring|fill|stroke)-\[var\(--/;
const RAW_HEX_PATTERN = /#[0-9a-fA-F]{3,8}\b/;

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    const stats = statSync(path);
    if (stats.isDirectory()) return walk(path);
    if (!/\.(tsx|ts)$/.test(path)) return [];
    if (/\.(test|spec)\.(tsx|ts)$/.test(path)) return [];
    return [path];
  });
}

const violations = [];

for (const root of SEARCH_ROOTS) {
  const rootPath = join(ROOT, root);
  if (!existsSync(rootPath)) continue;
  for (const file of walk(rootPath)) {
    const normalized = relative(ROOT, file).replaceAll('\\', '/');
    const text = readFileSync(file, 'utf8');
    if (TOKEN_ESCAPE_PATTERN.test(text)) {
      violations.push(`${normalized}: raw CSS-var Tailwind escape (use a bridged token)`);
    }
    if (RAW_HEX_PATTERN.test(text)) {
      violations.push(`${normalized}: raw hex color (only globals.css theme blocks may hold hex)`);
    }
  }
}

// globals.css must exist and define both theme blocks.
const globalsPath = join(ROOT, 'app', 'globals.css');
if (!existsSync(globalsPath)) {
  violations.push('app/globals.css is missing — it is the single token source.');
} else {
  const css = readFileSync(globalsPath, 'utf8');
  if (!/:root\s*\{/.test(css)) violations.push('app/globals.css: missing :root light theme block.');
  if (!/html\[data-theme='dark'\]\s*\{/.test(css)) {
    violations.push("app/globals.css: missing html[data-theme='dark'] dark theme block.");
  }
  if (!/@theme inline\s*\{/.test(css)) {
    violations.push('app/globals.css: missing @theme inline Tailwind bridge.');
  }
}

if (violations.length) {
  console.error('Token-escape / no-raw-hex guard failed:');
  for (const v of violations) console.error(`- ${v}`);
  process.exit(1);
}

console.log('token-escape / no-raw-hex guard: OK');

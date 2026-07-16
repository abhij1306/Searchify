/**
 * Frontend architecture guard (F1 → F2).
 *
 * Enforces line budgets and required-file ownership. The per-domain API owner
 * modules now exist (F2), so the required-owners check is ENFORCED (hard fail)
 * rather than warned. Also enforces that the `index.ts` compat facade owns no
 * transport — it may only spread the per-domain modules, never call `fetch` or
 * import the transport client.
 *
 * Run: node scripts/check-frontend-architecture.mjs
 */
import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();

// Line budgets — split any owner that exceeds its limit.
const lineBudgets = [
  { file: 'app/layout.tsx', maxLines: 120 },
  { file: 'app/globals.css', maxLines: 700 },
  { file: 'components/ui/theme-toggle.tsx', maxLines: 120 },
  { file: 'lib/theme.ts', maxLines: 160 },
];

// Per-domain API owners under lib/api/. These now exist (F2), so the check is
// enforced as a hard failure. ENFORCE_API_OWNERS=0 can soften it for debugging.
const requiredApiOwners = [
  'auth.ts',
  'projects.ts',
  'prompts.ts',
  'providers.ts',
  'runs.ts',
  'visibility.ts',
];
const ENFORCE_API_OWNERS = process.env.ENFORCE_API_OWNERS !== '0';

const failures = [];
const warnings = [];

function read(relativePath) {
  try {
    return fs.readFileSync(path.join(root, relativePath), 'utf8');
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    failures.push(`${relativePath} could not be read: ${message}`);
    return null;
  }
}

for (const check of lineBudgets) {
  const content = read(check.file);
  if (content === null) continue;
  const lines = content.split(/\r?\n/).length;
  if (lines > check.maxLines) {
    failures.push(`${check.file} has ${lines} lines; limit is ${check.maxLines}. Split the owner.`);
  }
}

for (const owner of requiredApiOwners) {
  const ownerPath = path.join(root, 'lib', 'api', owner);
  if (fs.existsSync(ownerPath)) continue;
  const msg = `lib/api/${owner} is missing (API methods split by domain owner).`;
  if (ENFORCE_API_OWNERS) failures.push(msg);
  else warnings.push(msg);
}

// The index.ts compat facade must own no transport: no fetch, no client import.
const indexPath = path.join(root, 'lib', 'api', 'index.ts');
if (fs.existsSync(indexPath)) {
  const indexSource = fs.readFileSync(indexPath, 'utf8');
  if (/\bfetch\s*\(/.test(indexSource)) {
    failures.push('lib/api/index.ts calls fetch() — the facade must own no transport.');
  }
  if (/from\s+['"]\.\/client['"]/.test(indexSource)) {
    failures.push("lib/api/index.ts imports './client' — the facade must own no transport.");
  }
} else if (ENFORCE_API_OWNERS) {
  failures.push('lib/api/index.ts is missing (compat facade spreading the domain modules).');
}

for (const w of warnings) console.warn(`warning: ${w}`);

if (failures.length) {
  console.error('Frontend architecture check failed:');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log('frontend architecture guard: OK');

/**
 * Frontend architecture guard (F1).
 *
 * Enforces line budgets and required-file ownership. F1 seeds a placeholder
 * list of required API owners (the per-domain modules F2 will author); the
 * list is checked leniently now (warn) and becomes hard in F2. Line budgets
 * on existing files are enforced.
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

// Per-domain API owners F2 will create under lib/api/. Enforced softly in F1
// (the contract layer does not exist yet); flipped to hard once F2 lands.
const requiredApiOwners = [
  'auth.ts',
  'projects.ts',
  'prompts.ts',
  'providers.ts',
  'runs.ts',
  'visibility.ts',
];
const ENFORCE_API_OWNERS = process.env.ENFORCE_API_OWNERS === '1';

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

for (const w of warnings) console.warn(`warning: ${w}`);

if (failures.length) {
  console.error('Frontend architecture check failed:');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log('frontend architecture guard: OK');

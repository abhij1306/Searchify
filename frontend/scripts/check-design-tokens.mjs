/**
 * Design-token guard (F1).
 *
 * Asserts that app/globals.css declares every token name documented in
 * docs/design.md (§3–§7). This is the machine check for the F1 acceptance
 * criterion "the globals.css token set matches docs/design.md". If a token
 * is renamed/removed in design.md, update this list to match — globals.css
 * is the source of truth for VALUES, design.md for the NAME SET.
 *
 * Run: node scripts/check-design-tokens.mjs
 */
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.cwd();
const cssPath = join(root, 'app', 'globals.css');

if (!existsSync(cssPath)) {
  console.error('check-design-tokens: app/globals.css is missing.');
  process.exit(1);
}
const css = readFileSync(cssPath, 'utf8');

// Raw CSS variables that MUST be declared (`--name:`) in globals.css.
const requiredVars = [
  // Fonts
  'font-primary-family',
  'font-mono-family',
  'font-display-family',
  // Surfaces
  'bg-base',
  'bg-alt',
  'bg-panel',
  'bg-elevated',
  'bg-well',
  'bg-sidebar',
  'surface-overlay',
  // Borders
  'border-subtle',
  'border',
  'border-strong',
  'border-focus',
  // Text
  'text-primary',
  'text-secondary',
  'text-muted',
  'text-subtle',
  // Accent
  'accent',
  'accent-hover',
  'accent-fg',
  'accent-subtle',
  'accent-soft',
  'accent-border',
  'accent-text',
  // Status
  'success',
  'success-bg',
  'success-border',
  'success-text',
  'warning',
  'warning-bg',
  'warning-border',
  'warning-text',
  'danger',
  'danger-bg',
  'danger-border',
  'danger-text',
  'info',
  'info-bg',
  'info-border',
  'info-text',
  'neutral-bg',
  // Sentiment
  'sentiment-positive',
  'sentiment-positive-bg',
  'sentiment-positive-text',
  'sentiment-neutral',
  'sentiment-neutral-bg',
  'sentiment-neutral-text',
  'sentiment-negative',
  'sentiment-negative-bg',
  'sentiment-negative-text',
  'value-placeholder',
  // Citation classification
  'citation-owned',
  'citation-owned-bg',
  'citation-owned-text',
  'citation-competitor',
  'citation-competitor-bg',
  'citation-competitor-text',
  'citation-third-party',
  'citation-third-party-bg',
  'citation-third-party-text',
  // Run status
  'run-draft',
  'run-draft-bg',
  'run-queued',
  'run-queued-bg',
  'run-running',
  'run-running-bg',
  'run-analyzing',
  'run-analyzing-bg',
  'run-completed',
  'run-completed-bg',
  'run-partial',
  'run-partial-bg',
  'run-failed',
  'run-failed-bg',
  'run-cancelled',
  'run-cancelled-bg',
  // Score bands
  'score-low',
  'score-low-bg',
  'score-mid',
  'score-mid-bg',
  'score-good',
  'score-good-bg',
  'score-high',
  'score-high-bg',
  // Shadows / elevation
  'shadow-xs-value',
  'shadow-sm-value',
  'shadow-card-value',
  'shadow-elevated-value',
  'shadow-lg-value',
  'shadow-modal',
  'focus-ring',
  'overlay-scrim',
  // Skeleton
  'skeleton-base',
  'skeleton-highlight',
  // Weights
  'weight-normal',
  'weight-medium',
  'weight-semibold',
  'weight-bold',
  // Tracking
  'tracking-tight',
  'tracking-normal',
  'tracking-wide',
  'tracking-wider',
  // Line heights
  'leading-none',
  'leading-tight',
  'leading-snug',
  'leading-normal',
  // Spacing 4px grid
  'space-1',
  'space-2',
  'space-3',
  'space-4',
  'space-5',
  'space-6',
  'space-7',
  'space-8',
  'space-10',
  'space-12',
  'space-14',
  'space-16',
  'space-20',
  'card-padding',
  'content-gutter',
  // Radii
  'radius-xs',
  'radius-sm',
  'radius-md',
  'radius-lg',
  'radius-xl',
  'radius-2xl',
  'radius-full',
  // Controls
  'control-height-sm',
  'control-height',
  'control-height-lg',
  'interactive-border-width',
  // Table
  'table-row-height',
  'table-header-height',
  'table-font-size',
  'table-header-font-size',
  // Segmented
  'segmented-bg',
  // Motion
  'transition-fast',
  'transition-base',
  'transition-slow',
];

// Type-scale sizes live in the @theme inline bridge (Tailwind --text-* namespace).
const requiredTypeScale = [
  'text-2xs',
  'text-xs',
  'text-sm',
  'text-base',
  'text-lg',
  'text-xl',
  'text-2xl',
];

// Bridged @theme colors that MUST exist so components can reference them.
const requiredBridged = [
  'color-background',
  'color-panel',
  'color-foreground',
  'color-secondary',
  'color-muted',
  'color-border',
  'color-accent',
  'color-success',
  'color-warning',
  'color-danger',
  'color-info',
  'color-sentiment-positive',
  'color-sentiment-neutral',
  'color-sentiment-negative',
  'color-citation-owned',
  'color-citation-competitor',
  'color-citation-third-party',
  'color-run-draft',
  'color-run-queued',
  'color-run-running',
  'color-run-analyzing',
  'color-run-completed',
  'color-run-partial',
  'color-run-failed',
  'color-run-cancelled',
  'color-score-low',
  'color-score-mid',
  'color-score-good',
  'color-score-high',
];

const missing = [];

for (const name of requiredVars) {
  const re = new RegExp(`--${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*:`);
  if (!re.test(css)) missing.push(`--${name}`);
}
for (const name of requiredTypeScale) {
  const re = new RegExp(`--${name}\\s*:`);
  if (!re.test(css)) missing.push(`--${name} (type scale)`);
}
for (const name of requiredBridged) {
  const re = new RegExp(`--${name}\\s*:`);
  if (!re.test(css)) missing.push(`--${name} (@theme bridge)`);
}

if (missing.length) {
  console.error(
    `Design-token guard failed: ${missing.length} token(s) documented in docs/design.md are missing from app/globals.css:`,
  );
  for (const m of missing) console.error(`- ${m}`);
  process.exit(1);
}

console.log(
  `design-token guard: OK (${requiredVars.length + requiredTypeScale.length + requiredBridged.length} tokens present)`,
);

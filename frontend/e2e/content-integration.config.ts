import { defineConfig } from '@playwright/test';

import { FRONTEND_ORIGIN } from './helpers/real-stack';

/**
 * Dedicated config for the real-stack content integration spec (Task 8).
 *
 * Unlike the default `playwright.config.ts` (which starts a single `pnpm dev`
 * webServer and stubs every backend call in-spec), this config starts NOTHING
 * itself: `content-integration.spec.ts` boots the disposable DB, mock Mistral
 * server, API, content worker and frontend via `helpers/real-stack.ts` in its
 * own `beforeAll`, and tears everything down in `afterAll`.
 *
 * Run explicitly:
 *   cd frontend && pnpm exec playwright test --config e2e/content-integration.config.ts
 */
export default defineConfig({
  testDir: '.',
  testMatch: '**/content-integration.spec.ts',
  // The stack is a shared, stateful fixture: one worker, no retries (a retry
  // would re-enter beforeAll against ports the failed run may not have freed).
  workers: 1,
  retries: 0,
  fullyParallel: false,
  // Stack boot (uv + uvicorn + worker + next dev) dominates; give each test
  // room since the first one pays the whole boot cost in beforeAll.
  timeout: 300_000,
  use: {
    baseURL: FRONTEND_ORIGIN,
    trace: 'retain-on-failure',
  },
});

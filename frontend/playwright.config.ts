import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  // The real-stack integration spec owns its own lifecycle + config; run it
  // explicitly with `--config e2e/content-integration.config.ts`.
  testIgnore: '**/content-integration.spec.ts',
  retries: 1,
  use: {
    baseURL: 'http://127.0.0.1:3000',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'pnpm dev',
    url: 'http://127.0.0.1:3000',
    reuseExistingServer: !process.env.CI,
  },
});

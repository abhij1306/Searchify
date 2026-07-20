import { expect, test, type Page } from '@playwright/test';

import {
  MOCK_API_KEY,
  MOCK_RETURNED_MODEL,
  mockProvider,
  setProviderDelay,
  startRealStack,
  type RealStack,
} from './helpers/real-stack';

/**
 * Real-stack content integration (Task 8) — run with the dedicated config:
 *
 *   pnpm exec playwright test --config e2e/content-integration.config.ts
 *
 * No stubs anywhere in app code: disposable Postgres DB, real FastAPI app,
 * real `content_worker` process, real Next.js proxy, and the real Mistral
 * connector pointed (via `CONTENT_PROVIDER_ENDPOINT` only) at a local mock
 * server. Requires Postgres reachable via `E2E_ADMIN_DATABASE_URL` (or the
 * backend `.env` `DATABASE_URL` server) and `uv` + node on PATH.
 */

let stack: RealStack;

test.beforeAll(async () => {
  stack = await startRealStack();
});

test.afterAll(async () => {
  await stack?.stop();
});

test.beforeEach(() => {
  setProviderDelay(0);
});

const PROMPT_BOX = /describe the website content/i;

/** Register (auto-login via HttpOnly cookie) + create a project via the real API. */
async function seedAccount(page: Page, email: string, projectName: string): Promise<string> {
  const register = await page.request.post('/api/v1/auth/register', {
    data: { email, password: 'password123' },
  });
  expect(register.status(), await register.text()).toBe(201);
  const project = await page.request.post('/api/v1/projects', {
    data: {
      name: projectName,
      brand_name: projectName,
      website_url: 'https://acme.example',
      country_code: 'US',
      language_code: 'en',
      benchmark_mode: 'consumer_like',
      default_repetitions: 1,
    },
  });
  expect(project.status(), await project.text()).toBe(201);
  return ((await project.json()) as { id: string }).id;
}

async function generateFromComposer(page: Page, prompt: string): Promise<void> {
  await page.getByRole('textbox', { name: PROMPT_BOX }).fill(prompt);
  await page.getByRole('button', { name: 'Generate' }).click();
}

test('enqueue → worker → sanitised markdown result, persisted across reload', async ({ page }) => {
  await seedAccount(page, 'e2e-happy@example.com', 'Acme');
  await page.goto('/content');

  await generateFromComposer(page, 'Write a launch page for Acme.');
  await expect(page.getByRole('status', { name: /generating content/i })).toBeVisible();

  // The real worker claims the row, calls the mock provider, finalises.
  // Generous timeout: the first navigation also pays the Next.js dev-server
  // compile of /content, which can dwarf the actual queue round trip.
  const heading = page.getByRole('heading', { name: 'Acme Launch Page' });
  await expect(heading).toBeVisible({ timeout: 120_000 });

  // Sanitisation ran on the REAL provider output: markdown rendered, raw HTML
  // never executed or injected, javascript: href neutralised.
  await expect(page.getByText('we make excellent things')).toBeVisible();
  expect(await page.evaluate(() => (window as { pwned?: boolean }).pwned)).toBeUndefined();
  expect(await page.locator('script:has-text("pwned")').count()).toBe(0);
  const goodLink = page.getByRole('link', { name: 'Contact us' });
  await expect(goodLink).toHaveAttribute('href', 'https://acme.example/contact');
  await expect(goodLink).toHaveAttribute('rel', /noopener/);
  const badLink = page.getByRole('link', { name: 'Bad link' });
  const badHref = await badLink.getAttribute('href');
  expect(badHref ?? '').not.toContain('javascript:');

  // Provenance from the real round trip.
  await expect(
    page.getByText(new RegExp(`returned model: ${MOCK_RETURNED_MODEL}`, 'i')),
  ).toBeVisible();

  // The env-held key reached the provider (and only the provider): the real
  // connector sent it, and no DTO ever carries it.
  expect(mockProvider.requests.length).toBeGreaterThan(0);
  expect(mockProvider.requests.at(-1)?.authorization).toBe(`Bearer ${MOCK_API_KEY}`);
  const listResponse = await page.request.get(
    '/api/v1/content/generations?project_id=' + (await activeProjectId(page)),
  );
  expect(await listResponse.text()).not.toContain(MOCK_API_KEY);

  // Reload: the record persists in the disposable DB, reappears in history,
  // and reopens from there (nothing is auto-selected after a fresh load).
  await page.reload();
  const historyEntry = page.getByRole('button', { name: /write a launch page for acme/i });
  await expect(historyEntry).toBeVisible({ timeout: 30_000 });
  await historyEntry.click();
  await expect(page.getByRole('heading', { name: 'Acme Launch Page' })).toBeVisible({
    timeout: 30_000,
  });
});

async function activeProjectId(page: Page): Promise<string> {
  const projects = await page.request.get('/api/v1/projects');
  const body = (await projects.json()) as Array<{ id: string }>;
  return body[0]!.id;
}

test('cancel during a slow provider call ends cancelled with no output', async ({ page }) => {
  const email = 'e2e-cancel@example.com';
  await seedAccount(page, email, 'CancelCo');
  const projectId = await activeProjectId(page);
  await page.goto('/content');

  // Slow mode: the provider holds the HTTP call open long enough to cancel.
  setProviderDelay(20_000);
  await generateFromComposer(page, 'Write something slowly.');
  await expect(page.getByRole('status', { name: /generating content/i })).toBeVisible();

  // Wait until the worker is actually inside the provider call, then cancel —
  // this exercises the cancelled-in-flight path (attempt recorded, output
  // discarded), not just a queued-row cancel.
  const requestsBefore = mockProvider.requests.length;
  await expect
    .poll(() => mockProvider.requests.length, { timeout: 30_000 })
    .toBeGreaterThan(requestsBefore);
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();

  await expect(page.getByRole('status', { name: /generating content/i })).not.toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByRole('textbox', { name: PROMPT_BOX })).toBeEnabled();

  // API state: terminal cancelled, no output text ever exposed.
  const list = (await (
    await page.request.get(`/api/v1/content/generations?project_id=${projectId}`)
  ).json()) as Array<{ id: string; status: string }>;
  expect(list.length).toBe(1);
  const detailUrl = `/api/v1/content/generations/${list[0]!.id}`;
  await expect
    .poll(
      async () => ((await (await page.request.get(detailUrl)).json()) as { status: string }).status,
      { timeout: 40_000 },
    )
    .toBe('cancelled');
  const detail = (await (await page.request.get(detailUrl)).json()) as {
    output_text: string | null;
  };
  expect(detail.output_text).toBeNull();
});

test('regenerate and try-again create new records; originals are untouched', async ({ page }) => {
  await seedAccount(page, 'e2e-regen@example.com', 'RegenCo');
  const projectId = await activeProjectId(page);
  await page.goto('/content');

  await generateFromComposer(page, 'Write a launch page.');
  await expect(page.getByRole('heading', { name: 'Acme Launch Page' })).toBeVisible({
    timeout: 60_000,
  });

  const listUrl = `/api/v1/content/generations?project_id=${projectId}`;
  const firstList = (await (await page.request.get(listUrl)).json()) as Array<{ id: string }>;
  expect(firstList.length).toBe(1);
  const originalId = firstList[0]!.id;

  // Regenerate from the result card creates a second record...
  await page.getByRole('button', { name: 'Regenerate' }).click();
  await expect
    .poll(async () => ((await (await page.request.get(listUrl)).json()) as unknown[]).length, {
      timeout: 30_000,
    })
    .toBe(2);

  // ...which also runs to success against the real worker.
  await expect
    .poll(
      async () => {
        const rows = (await (await page.request.get(listUrl)).json()) as Array<{
          status: string;
        }>;
        return rows.every((row) => row.status === 'succeeded');
      },
      { timeout: 60_000 },
    )
    .toBe(true);

  // Try-again via the API surface (the UI button only shows on failures).
  const tryAgain = await page.request.post(`/api/v1/content/generations/${originalId}/try-again`);
  expect(tryAgain.status(), await tryAgain.text()).toBe(201);
  const clone = (await tryAgain.json()) as { id: string };
  expect(clone.id).not.toBe(originalId);

  // The original record is untouched throughout.
  const original = (await (
    await page.request.get(`/api/v1/content/generations/${originalId}`)
  ).json()) as { status: string; output_text: string | null };
  expect(original.status).toBe('succeeded');
  expect(original.output_text).toContain('Acme Launch Page');
});

test('cross-workspace isolation: a second workspace never sees the first workspace records', async ({
  browser,
}) => {
  // Workspace A: one succeeded generation.
  const contextA = await browser.newContext();
  const pageA = await contextA.newPage();
  await seedAccount(pageA, 'e2e-iso-a@example.com', 'IsoAlpha');
  const projectA = await activeProjectId(pageA);
  await pageA.goto('/content');
  await generateFromComposer(pageA, 'Write a page for workspace A.');
  await expect(pageA.getByRole('heading', { name: 'Acme Launch Page' })).toBeVisible({
    timeout: 60_000,
  });
  const generationA = (
    (await (
      await pageA.request.get(`/api/v1/content/generations?project_id=${projectA}`)
    ).json()) as Array<{ id: string }>
  )[0]!.id;

  // Workspace B: separate registration, fresh cookie jar.
  const contextB = await browser.newContext();
  const pageB = await contextB.newPage();
  await seedAccount(pageB, 'e2e-iso-b@example.com', 'IsoBeta');
  const projectB = await activeProjectId(pageB);

  // B's list is empty; B cannot list A's project, read A's detail, or act on it.
  const listB = await pageB.request.get(`/api/v1/content/generations?project_id=${projectB}`);
  expect((await listB.json()) as unknown[]).toEqual([]);
  expect(
    (await pageB.request.get(`/api/v1/content/generations?project_id=${projectA}`)).status(),
  ).toBe(404);
  expect((await pageB.request.get(`/api/v1/content/generations/${generationA}`)).status()).toBe(
    404,
  );
  expect(
    (await pageB.request.post(`/api/v1/content/generations/${generationA}/cancel`)).status(),
  ).toBe(404);

  // And the B screen shows the empty state, not A's history.
  await pageB.goto('/content');
  await expect(pageB.getByRole('textbox', { name: PROMPT_BOX })).toBeVisible();
  await expect(pageB.getByText('Acme Launch Page')).toHaveCount(0);

  await contextA.close();
  await contextB.close();
});

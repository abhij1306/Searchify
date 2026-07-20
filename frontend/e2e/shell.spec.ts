import { expect, test } from '@playwright/test';

/**
 * F5 shell smoke: with an authenticated session the `(app)` shell renders its
 * chrome — sidebar nav groups, the top-bar page title, and the theme
 * toggle. The backend `/auth/me` and `/projects` calls are stubbed at the
 * network layer so the spec does not need a live backend.
 *
 * Note: this requires a running dev server (playwright.config.ts starts one).
 * It is skipped automatically when no browser/dev server is available.
 */
test('authenticated shell renders sidebar groups and top bar', async ({ page }) => {
  const user = {
    id: '22222222-2222-4222-8222-222222222222',
    email: 'shell@example.com',
    role: 'owner',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
  const project = {
    id: '11111111-1111-4111-8111-111111111111',
    workspace_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    name: 'Acme',
    brand_name: 'Acme',
    website_url: 'https://acme.example',
    country_code: 'US',
    language_code: 'en',
    benchmark_mode: 'consumer_like',
    default_repetitions: 3,
    brand: { aliases: [] },
    owned_domains: [],
    unintended_domains: [],
    competitors: [],
    prompt_sets: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: { user } }));
  await page.route('**/api/v1/projects', (route) => route.fulfill({ json: [project] }));

  await page.goto('/visibility');

  // Sidebar groups + a live nav item.
  await expect(page.getByText('Analytics')).toBeVisible();
  await expect(page.getByRole('link', { name: /visibility/i })).toBeVisible();

  // Project switcher shows the active brand.
  await expect(page.getByText('Acme').first()).toBeVisible();

  // Top-bar page title + theme toggle are present.
  await expect(page.getByRole('heading', { name: 'Visibility' })).toBeVisible();
  await expect(page.getByRole('button', { name: /toggle color theme/i })).toBeVisible();

  // A disabled roadmap item shows the "soon" affordance and is not a link.
  await expect(page.getByRole('link', { name: /llm analytics/i })).toHaveCount(0);
});

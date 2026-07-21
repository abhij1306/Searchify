import { expect, test } from '@playwright/test';

/**
 * Smoke: the public marketing landing page renders at `/` (no backend needed —
 * the session island stays inert on error) and the nav theme toggle flips
 * `data-theme` on <html>. The hero h1 is the page's single level-1 heading.
 */
test('landing renders and theme toggle flips data-theme', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

  const html = page.locator('html');
  const initialTheme = (await html.getAttribute('data-theme')) ?? 'dark';

  await page.getByRole('button', { name: /toggle color theme/i }).click();

  const expected = initialTheme === 'dark' ? 'light' : 'dark';
  await expect(html).toHaveAttribute('data-theme', expected);
});

import { expect, test } from '@playwright/test';

test('home page renders and theme toggle flips data-theme', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Searchify' })).toBeVisible();

  const initial = await page.evaluate(() => document.documentElement.dataset.theme);
  await page.getByRole('button', { name: /toggle color theme/i }).click();
  const next = await page.evaluate(() => document.documentElement.dataset.theme);
  expect(next).not.toBe(initial);
});

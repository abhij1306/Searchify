import { expect, test } from '@playwright/test';

import { GITHUB_URL, LICENSE_URL } from '../lib/marketing-content/social';

const MARKETING_PAGES = ['/pricing', '/enterprise', '/solutions', '/blog', '/compare', '/faq'];

test.describe('marketing routes', () => {
  test('public routes render anonymously with one visible h1', async ({ page }) => {
    for (const path of MARKETING_PAGES) {
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      await expect(page.locator('h1:visible')).toHaveCount(1);
    }
  });

  test('unknown content slugs return 404', async ({ page }) => {
    for (const path of [
      '/blog/hello-searchify',
      '/blog/does-not-exist',
      '/compare/profound',
      '/compare/does-not-exist',
    ]) {
      const response = await page.goto(path);
      expect(response?.status()).toBe(404);
    }
  });

  test('shared navigation and footer work from a subpage', async ({ page }) => {
    await page.goto('/pricing');
    const resources = page.getByRole('button', { name: 'Resources' });
    await resources.hover();
    await expect(page.locator('#desktop-nav-panel')).toBeVisible();
    await expect(page.locator('#desktop-nav-panel').getByRole('menuitem')).toHaveCount(4);

    const footer = page.getByRole('navigation', { name: 'Footer' });
    await expect(footer.locator('.f-col-label')).toHaveCount(5);
    await expect(footer.getByRole('link', { name: 'Documentation' })).toHaveAttribute(
      'href',
      GITHUB_URL,
    );
    await expect(page.getByRole('link', { name: 'MIT License' })).toHaveAttribute(
      'href',
      LICENSE_URL,
    );
  });

  test('marketing subpages keep the dark-first default', async ({ page }) => {
    await page.goto('/pricing');
    await page.evaluate(() => window.localStorage.removeItem('searchify-theme'));
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });
});

import { expect, test } from '@playwright/test';

const DROPS = [
  { key: 'product', count: 9 },
  { key: 'resources', count: 3 },
  { key: 'solutions', count: 4 },
] as const;

test.describe('marketing navigation (real-engine CSS contract)', () => {
  test('desktop dropdowns open on hover and focus, then close with Escape', async ({ page }) => {
    await page.goto('/');

    for (const { key, count } of DROPS) {
      const trigger = page.locator(`button[aria-controls="desktop-nav-panel"]`, {
        hasText: new RegExp(key, 'i'),
      });
      const panel = page.locator('#desktop-nav-panel');

      await trigger.hover();
      await expect(panel).toBeVisible();
      await expect(trigger).toHaveAttribute('aria-expanded', 'true');
      await expect(panel.getByRole('menuitem')).toHaveCount(count);

      await trigger.focus();
      await expect(panel).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(panel).toBeHidden();
    }
  });

  test('mobile menu exposes all three accordions at 375px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/');
    const menu = page.locator('#mobile-menu');
    await expect(menu).toBeHidden();
    await page.getByRole('button', { name: 'Open menu' }).click();
    await expect(menu).toBeVisible();

    for (const { key, count } of DROPS) {
      const trigger = page.locator(`button[aria-controls="acc-${key}"]`);
      await trigger.click();
      await expect(trigger).toHaveAttribute('aria-expanded', 'true');
      const links = page.locator(`#acc-${key}`).getByRole('link');
      await expect(links).toHaveCount(count);
      await expect(links.first()).toBeVisible();
    }

    await page.getByRole('button', { name: 'Close menu' }).click();
    await expect(menu).toBeHidden();
  });

  test('theme is dark-first by default and an explicit choice persists across reload', async ({
    page,
  }) => {
    await page.goto('/');
    await page.evaluate(() => window.localStorage.removeItem('searchify-theme'));
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.getByRole('button', { name: 'Toggle color theme' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('marketing dark-first default does not leak into app routes', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' });
    await page.goto('/');
    await page.evaluate(() => window.localStorage.removeItem('searchify-theme'));
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.getByRole('link', { name: 'Sign in' }).first().click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('mobile evidence rows flow inline without overlap', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/');
    const row = page.locator('.mkt .evidence-row').first();
    const engine = await row.locator('.ev-engine').boundingBox();
    const badge = await row.locator('.badge').boundingBox();
    expect(engine).not.toBeNull();
    expect(badge).not.toBeNull();
    expect(Math.abs((engine as { y: number }).y - (badge as { y: number }).y)).toBeLessThan(4);
    expect(
      (engine as { x: number; width: number }).x + (engine as { width: number }).width,
    ).toBeLessThanOrEqual((badge as { x: number }).x);
  });

  test('nav gains the scrolled class after scrolling', async ({ page }) => {
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Main navigation' });
    await expect(nav).not.toHaveClass(/scrolled/);
    await page.mouse.wheel(0, 600);
    await expect(nav).toHaveClass(/scrolled/);
  });
});

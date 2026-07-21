import { expect, test } from '@playwright/test';

/**
 * Landing nav contract in a real browser engine: the open-state classes the
 * CSS visibility rules key on (.nav-item.open .drop, .mobile-menu.open,
 * .acc.open .acc-body, .site-nav.scrolled) actually produce visible panels —
 * the coverage jsdom unit tests cannot provide (jsdom never evaluates CSS
 * selectors). Complements app/(marketing)/page.test.tsx (aria/class contract)
 * and e2e/smoke.spec.ts (render + theme toggle).
 */
test.describe('landing nav (real-engine CSS contract)', () => {
  test('desktop Product dropdown opens visibly on hover and focus, Esc closes', async ({
    page,
  }) => {
    await page.goto('/');
    const trigger = page.locator('button[aria-controls="drop-product"]');
    const panel = page.locator('#drop-product');

    await expect(panel).toBeHidden();

    // Hover opens the 6-feature panel.
    await trigger.hover();
    await expect(panel).toBeVisible();
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await expect(panel.getByRole('menuitem')).toHaveCount(6);

    // Leaving the hover area closes it.
    await page.mouse.move(2, 500);
    await expect(panel).toBeHidden();

    // Keyboard: focus opens, Esc closes.
    await trigger.focus();
    await expect(panel).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
  });

  test('How-it-works dropdown opens on hover with the 3 steps', async ({ page }) => {
    await page.goto('/');
    const trigger = page.locator('button[aria-controls="drop-how"]');
    const panel = page.locator('#drop-how');

    await trigger.hover();
    await expect(panel).toBeVisible();
    await expect(panel.getByRole('menuitem')).toHaveCount(3);
  });

  test('mobile menu and Product accordion open visibly at 375px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/');
    const menu = page.locator('#mobile-menu');
    await expect(menu).toBeHidden();

    await page.getByRole('button', { name: 'Open menu' }).click();
    await expect(menu).toBeVisible();

    const accHead = page.locator('button[aria-controls="acc-product"]');
    await accHead.click();
    await expect(accHead).toHaveAttribute('aria-expanded', 'true');
    const accLinks = page.locator('#acc-product').getByRole('link');
    await expect(accLinks).toHaveCount(6);
    await expect(accLinks.first()).toBeVisible();

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

  test('nav gains the scrolled class after scrolling', async ({ page }) => {
    await page.goto('/');
    const nav = page.getByRole('navigation', { name: 'Main navigation' });
    await expect(nav).not.toHaveClass(/scrolled/);
    await page.mouse.wheel(0, 600);
    await expect(nav).toHaveClass(/scrolled/);
  });
});

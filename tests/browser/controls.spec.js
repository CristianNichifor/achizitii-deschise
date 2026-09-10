import { test, expect } from '@playwright/test';

for (const colorScheme of ['light', 'dark']) {
  test(`native controls, focus and table scrolling in ${colorScheme}`, async ({ page }, info) => {
    await page.emulateMedia({ colorScheme });
    await page.setViewportSize({ width: 900, height: 900 });
    await page.goto('/#v=cpv');
    await expect(page.locator('#run')).toBeEnabled();
    await expect(page.locator('#pager')).toBeVisible();
    await expect(page.locator('#pag-prev')).toBeDisabled();
    await expect(page.locator('#pag-next')).toBeEnabled();
    await expect(page.locator('#status')).toHaveAttribute('role', 'status');
    await expect(page.locator('#pager')).toHaveAccessibleName('Paginarea rezultatelor');
    const controls = page.locator('input, select');
    await expect(controls).toHaveCount(7);
    for (const control of await controls.all()) {
      if (await control.isVisible()) await expect(control).toHaveAccessibleName(/.+/);
      expect(await control.evaluate(element => Boolean(element.labels?.length || element.getAttribute('aria-label')))).toBe(true);
      expect(await control.evaluate(element => Boolean(element.closest('.civic-field')))).toBe(true);
    }
    for (const select of await page.locator('select').all()) {
      await expect(select).toHaveClass(/civic-select--native/);
      expect(await select.evaluate(element => getComputedStyle(element).appearance)).not.toBe('none');
      expect(await select.evaluate(element => parseFloat(getComputedStyle(element).paddingRight))).toBeGreaterThanOrEqual(24);
    }
    await page.locator('#an').focus();
    await page.keyboard.press('Tab');
    await expect(page.locator('#baza')).toBeFocused();
    expect(await page.locator('#baza').evaluate(element => getComputedStyle(element).outlineStyle)).not.toBe('none');
    const table = page.locator('#wrap-out');
    await expect(table).toHaveClass(/civic-table-scroll/);
    await expect(table).toHaveAttribute('role', 'region');
    await expect(table).toHaveAccessibleName(/Tabel/);
    expect(await table.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
    await table.focus();
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => table.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
    await page.screenshot({ path: info.outputPath('controls-and-scroll.png') });
    await page.setViewportSize({ width: 320, height: 900 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath('controls-mobile.png') });
  });
}

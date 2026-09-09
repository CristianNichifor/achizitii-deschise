import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';

for (const width of [390, 1440]) {
  test(`real DuckDB summary, filtering, export and shared URL at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/#v=sumar');
    await expect(page.locator('#run')).toBeEnabled();
    const rows = page.locator('#out tbody tr');
    await expect(rows.first()).toBeVisible();
    const year = await page.locator('#an option').evaluateAll(options =>
      options.find(option => option.value)?.value);
    expect(year).toBeTruthy();
    await page.locator('#an').selectOption(year);
    await expect(page).toHaveURL(new RegExp(`an=${year}`));
    await expect(page.locator('#sql')).toContainText(year);
    await expect(rows.first()).toContainText(year);
    await expect.poll(async () => {
      const years = await rows.locator('td:first-child').allTextContents();
      return years.length > 0 && years.every(value => value === year);
    }).toBe(true);
    const before = await page.locator('#out').innerText();
    const sharedUrl = page.url();
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#descarca').click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
    expect(await readFile(await download.path(), 'utf8')).toContain(year);
    await page.reload();
    await expect(page.locator('#run')).toBeEnabled();
    await expect(page.locator('#out')).toHaveText(before, { useInnerText: true });
    expect(page.url()).toBe(sharedUrl);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(errors).toEqual([]);
    await page.screenshot({ path: info.outputPath(`summary-${width}.png`), fullPage: true });
  });
}

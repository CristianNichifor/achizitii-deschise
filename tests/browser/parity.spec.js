import { test, expect } from '@playwright/test';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';

for (const colorScheme of ['light', 'dark']) {
  for (const width of [390, 1440]) {
    test(`summary and CPV parity ${colorScheme} ${width}`, async ({ page }, info) => {
      await page.emulateMedia({ colorScheme });
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/#v=sumar');
      await expect(page.locator('#run')).toBeEnabled();
      await expect(page.locator('#out tbody tr').first()).toBeVisible();
      const snapshot = async () => page.evaluate(() => ({
        hash: location.hash,
        rows: [...document.querySelectorAll('#out tr')].map(row =>
          [...row.cells].map(cell => cell.textContent)),
        sql: document.querySelector('#sql').textContent,
        options: [...document.querySelectorAll('select')].map(select => ({
          id: select.id, value: select.value,
          options: [...select.options].map(option => [option.value, option.text]),
        })),
      }));
      const summary = await snapshot();
      await page.locator('[data-key="cpv"]').click();
      await expect(page.locator('#pager')).toBeVisible();
      await expect(page.locator('#pag-info')).toHaveText('Pagina 1');
      const first = await snapshot();
      await page.locator('#pag-next').click();
      await expect(page.locator('#pag-info')).toHaveText('Pagina 2');
      const second = await snapshot();
      expect(second.rows).not.toEqual(first.rows);
      await page.locator('#pag-prev').click();
      await expect(page.locator('#pag-info')).toHaveText('Pagina 1');
      expect(await snapshot()).toEqual(first);
      const actual = { summary, first, second };
      const filename = `${colorScheme}-${width}.json`;
      if (process.env.CIVIC_RECORD_BASELINE) {
        await mkdir(process.env.CIVIC_RECORD_BASELINE, { recursive: true });
        await writeFile(join(process.env.CIVIC_RECORD_BASELINE, filename), JSON.stringify(actual));
      }
      if (process.env.CIVIC_PARITY_BASELINE) {
        expect(actual).toEqual(JSON.parse(await readFile(join(process.env.CIVIC_PARITY_BASELINE, filename), 'utf8')));
      }
      await page.screenshot({ path: info.outputPath('cpv.png') });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    });
  }
}

/**
 * Render each 1080×1080 artboard in carousel.html to its own PNG.
 *
 * Runs the page from file://, so Chromium needs --allow-file-access-from-files
 * to pull in the screenshots and the self-hosted Cairo woff2 alongside it.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const HTML = path.resolve(process.argv[2]);
const OUT = path.resolve(process.argv[3]);
const NAMES = [
  '1-cover',
  '2-scan',
  '3-subjects',
  '4-money',
  '5-reports',
  '6-cta',
];

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  args: ['--allow-file-access-from-files', '--force-device-scale-factor=1'],
});
const page = await browser.newPage({
  viewport: { width: 1400, height: 1200 },
  deviceScaleFactor: 1,
});
page.on('console', (m) => { if (m.type() === 'error') console.log('  js:', m.text()); });

await page.goto(pathToFileURL(HTML).href, { waitUntil: 'networkidle' });
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(700);

const boards = await page.$$('.board');
if (boards.length !== NAMES.length) {
  console.log(`  WARNING: ${boards.length} artboards but ${NAMES.length} names`);
}

for (let i = 0; i < boards.length; i++) {
  const file = path.join(OUT, `${NAMES[i] || 'board-' + (i + 1)}.png`);
  await boards[i].screenshot({ path: file });
  const { width, height } = await boards[i].boundingBox();
  console.log(`  ${path.basename(file)}  ${width}×${height}`);
}

await browser.close();

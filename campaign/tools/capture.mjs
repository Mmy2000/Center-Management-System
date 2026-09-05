/**
 * Campaign screenshot run.
 *
 * Drives the real app in Chromium at 2x so the output is usable in print as
 * well as on the web, once per language.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const BASE = 'http://127.0.0.1:8712';
const OUT_ROOT = process.argv[2];
const LANG = process.argv[3] || 'ar';
const IDS = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
// A scan is a real write: each language run must present a card that has not
// been checked in yet, or the second run photographs a duplicate-scan warning.
const HERO_TOKEN = (IDS.heroTokens || [])[LANG === 'ar' ? 0 : 1] || IDS.heroToken;

const OUT = path.join(OUT_ROOT, LANG);
fs.mkdirSync(OUT, { recursive: true });

/** Marketing shot list, in campaign order. */
const SHOTS = [
  { file: '01-login',              url: '/accounts/login/', anon: true },
  { file: '02-dashboard',          url: '/' },
  { file: '03-scanner-live',       url: `/scan/${IDS.lesson}/`, settle: 2500 },
  { file: '04-scanner-picker',     url: '/scan/' },
  { file: '05-students-list',      url: '/students/' },
  { file: '06-student-profile',    url: `/students/${IDS.student}/` },
  { file: '06b-student-subjects',  url: `/students/${IDS.student}/`, tab: '#tab-groups' },
  { file: '06c-student-attendance',url: `/students/${IDS.student}/`, tab: '#tab-attendance' },
  { file: '06d-student-ledger',    url: `/students/${IDS.student}/`, tab: '#tab-payments' },
  { file: '07-student-new',        url: '/students/new/' },
  { file: '08-groups',             url: '/groups/' },
  { file: '09-group-roster',       url: `/groups/${IDS.group}/` },
  { file: '10-lessons',            url: '/lessons/' },
  { file: '11-lesson-attendance',  url: `/lessons/${IDS.pastLesson}/` },
  { file: '12-payments',           url: '/payments/' },
  { file: '13-cards',              url: '/cards/' },
  { file: '14-reports-index',      url: '/reports/' },
  { file: '15-report-outstanding', url: '/reports/outstanding/' },
  { file: '16-report-attendance',  url: '/reports/attendance-daily/' },
  { file: '17-report-finance',     url: '/reports/finance-group/' },
  { file: '18-academics',          url: '/academics/' },
  { file: '18b-academics-offerings', url: '/academics/', tab: '#tab-offerings' },
  { file: '18c-academics-teachers', url: '/academics/', tab: '#tab-instructors' },
  { file: '19-users-roles',        url: '/accounts/users/' },
  { file: '20-settings',           url: '/settings/' },
  { file: '21-audit-log',          url: '/audit/' },
];

async function settle(page, extra = 0) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
  // Tables and KPI tiles arrive over AJAX after the shell paints.
  await page.waitForTimeout(900 + extra);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(150);
}

async function main() {
  const browser = await chromium.launch({ args: ['--force-device-scale-factor=2'] });
  const context = await browser.newContext({
    viewport: { width: 1560, height: 980 },
    deviceScaleFactor: 2,
    locale: LANG === 'ar' ? 'ar-EG' : 'en-US',
    timezoneId: 'Africa/Cairo',
    colorScheme: 'light',
  });

  // Pin the appearance: the theme picker stores per-device choices, and a
  // half-applied dark mode in one shot ruins a set that has to look uniform.
  await context.addInitScript(() => {
    try {
      localStorage.setItem('cms.appearance', JSON.stringify({
        mode: 'light', density: 'comfortable',
      }));
    } catch (e) { /* private mode */ }
  });
  await context.addCookies([
    { name: 'django_language', value: LANG, domain: '127.0.0.1', path: '/' },
  ]);

  const page = await context.newPage();
  page.on('console', (m) => { if (m.type() === 'error') console.log(`    js-error: ${m.text()}`); });

  // ------------------------------------------------------------- anonymous
  for (const shot of SHOTS.filter((s) => s.anon)) {
    await page.goto(BASE + shot.url, { waitUntil: 'domcontentloaded' });
    await settle(page);
    await page.screenshot({ path: path.join(OUT, `${shot.file}.png`) });
    console.log(`  ${LANG}/${shot.file}.png`);
  }

  // ----------------------------------------------------------------- login
  await page.goto(`${BASE}/accounts/login/`, { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', 'admin');
  await page.fill('input[name="password"]', 'demo12345678');
  await page.click('#login-submit');
  // The form posts over AJAX and then assigns window.location, so there is no
  // navigation event to await - watch the URL instead.
  await page.waitForURL((url) => !url.pathname.startsWith('/accounts/login'), {
    timeout: 30000,
  });
  await settle(page);
  // The cookie is set before login; Django can rewrite it on session start.
  await context.addCookies([
    { name: 'django_language', value: LANG, domain: '127.0.0.1', path: '/' },
  ]);

  // ----------------------------------------------------------- signed in
  // ------------------------------------------------- hero: a live scan
  if (HERO_TOKEN) {
    await page.goto(`${BASE}/scan/${IDS.lesson}/`, { waitUntil: 'domcontentloaded' });
    await settle(page, 1500);
    await page.fill('#scan-input', HERO_TOKEN);
    await page.press('#scan-input', 'Enter');
    // Wait for the idle placeholder to be replaced by a real verdict.
    await page.waitForFunction(
      () => {
        const el = document.getElementById('scan-result');
        return el && !el.classList.contains('scan-idle');
      },
      { timeout: 15000 },
    ).catch(() => console.log('    scan verdict did not render'));
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(OUT, '03b-scan-accepted.png') });
    console.log(`  ${LANG}/03b-scan-accepted.png`);
  }

  for (const shot of SHOTS.filter((s) => !s.anon)) {
    const response = await page.goto(BASE + shot.url, { waitUntil: 'domcontentloaded' });
    const status = response ? response.status() : 0;
    if (status >= 400) {
      console.log(`  SKIP ${shot.file} - HTTP ${status} at ${shot.url}`);
      continue;
    }
    if (shot.tab) {
      await page.click(`[data-bs-target="${shot.tab}"]`);
      // The pane loads its rows over AJAX the first time it is shown.
      await page.waitForTimeout(1400);
    }
    await settle(page, shot.settle || 0);
    await page.screenshot({ path: path.join(OUT, `${shot.file}.png`) });
    console.log(`  ${LANG}/${shot.file}.png`);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

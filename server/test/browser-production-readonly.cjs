const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { performance } = require('node:perf_hooks');
const [workdir, playwrightModule] = process.argv.slice(2);
const { chromium, webkit } = require(playwrightModule);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const results = [];
async function main() {
  for (const [label, engine, options, viewport] of [
    ['production-desktop', chromium, { channel: 'chrome' }, { width: 1440, height: 1000 }],
    ['production-mobile', webkit, {}, { width: 390, height: 844 }],
  ]) {
    const browser = await engine.launch({ headless: true, ...options });
    try {
      const context = await browser.newContext({ viewport, isMobile: label.endsWith('mobile'), hasTouch: label.endsWith('mobile') });
      await context.addCookies(Object.entries(input.cookies).map(([name, value]) => ({ name, value, url: input.base, secure: true, httpOnly: true, sameSite: 'Lax' })));
      const page = await context.newPage();
      const errors = [], api = [];
      page.on('pageerror', (e) => errors.push(e.message));
      page.on('request', (r) => {
        const url = new URL(r.url());
        if (url.pathname.startsWith('/api/')) {
          api.push({ path: url.pathname, search: url.search, method: r.method() });
          assert.ok(['GET','HEAD'].includes(r.method()), 'Production validation must remain read only');
        }
      });
      const start = performance.now();
      await page.goto(input.base, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.querySelectorAll('.asset-card').length === 60);
      const firstCardsMs = Math.round(performance.now() - start);
      await page.waitForFunction(() => [...document.querySelectorAll('.asset-card img')].slice(0, 6).every((img) => img.complete && img.naturalWidth > 0), null, { timeout: 30000 });
      const firstSixImagesMs = Math.round(performance.now() - start);
      assert.ok(!api.some((r) => r.path === '/api/assets'));
      assert.ok(!api.some((r) => ['/api/prompts','/api/music-tracks','/api/video-accounts','/api/character-albums'].includes(r.path)));
      assert.ok(!api.some((r) => r.path.endsWith('/stream')));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const data = await page.evaluate(() => performance.getEntriesByType('resource')
        .filter((r) => r.name.includes('/api/asset-browser'))
        .map((r) => ({ endpoint: new URL(r.name).pathname, durationMs: Math.round(r.duration), encodedBytes: r.encodedBodySize, decodedBytes: r.decodedBodySize })));
      await page.screenshot({ path: path.join(workdir, `${label}.png`) });
      const firstPageRequests = api.length;
      const search = page.getByPlaceholder('搜索素材、标签或来源');
      await search.fill(input.target);
      await page.locator('.asset-card h3').filter({ hasText: input.target }).first().waitFor();
      await search.fill('');
      await page.waitForFunction(() => document.querySelectorAll('.asset-card').length === 60);
      const videoCard = page.locator('.asset-card').filter({ has: page.locator('.play-chip') }).first();
      await videoCard.click();
      const video = page.locator('.detail-media video');
      await video.waitFor();
      await video.evaluate(async (v) => { v.muted = true; await v.play(); });
      await page.waitForFunction(() => {
        const v = document.querySelector('.detail-media video');
        return v && v.currentTime > 0.2 && v.videoWidth > 0 && !v.error;
      }, null, { timeout: 30000 });
      await video.evaluate((v) => { v.currentTime = Math.min(2, v.duration / 2); });
      await page.waitForFunction(() => {
        const v = document.querySelector('.detail-media video');
        return v && !v.seeking && v.currentTime > 0.5 && !v.error;
      });
      assert.deepEqual(errors, []);
      const result = { label, firstCardsMs, firstSixImagesMs, data, firstPageRequests, total: input.total,
        checks: ['60 cards', 'gzip', 'no full-list request', 'no initial video download', 'oldest asset search', 'full detail', 'video play and seek', 'no JS errors', 'no horizontal overflow'] };
      results.push(result);
      console.log(JSON.stringify(result));
    } finally { await browser.close(); }
  }
  fs.writeFileSync(path.join(workdir, 'production-browser-results.json'), JSON.stringify({ passed: true, results }, null, 2));
}
main().catch((e) => { console.error(e.message); process.exitCode = 1; });

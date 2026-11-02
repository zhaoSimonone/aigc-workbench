// Explicit production browser acceptance using only the temporary test account.
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');

async function main() {
  const [api, workdir, playwrightModule] = process.argv.slice(2);
  if (!api || !workdir || !playwrightModule) throw new Error('Expected API, workdir and Playwright module path');
  const { chromium, webkit } = require(playwrightModule);
  const login = JSON.parse(fs.readFileSync(path.join(workdir, 'primary-login.json')));
  assert(/^acceptance-[a-f0-9]{32}@example\.invalid$/.test(login.email));
  const imported = JSON.parse(fs.readFileSync(path.join(workdir, 'import-receipt.json')));
  const checks = [];
  for (const [label, engine, options, viewport] of [
    ['desktop-chrome', chromium, { channel: 'chrome' }, { width: 1440, height: 1000 }],
    ['mobile-webkit', webkit, {}, { width: 390, height: 844 }],
  ]) {
    const browser = await engine.launch({ headless: true, ...options });
    try {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto(api, { waitUntil: 'networkidle' });
      await page.locator('input[type=email]').fill(login.email);
      await page.locator('input[type=password]').fill(login.password);
      await page.getByRole('button', { name: '进入工作台' }).click();
      await page.locator('.asset-card').first().waitFor({ timeout: 30000 });
      await page.screenshot({ path: path.join(workdir, `${label}-library.png`), fullPage: true });
      for (const result of imported.results) {
        const title = result.asset.name;
        await page.getByPlaceholder('搜索素材、标签或来源').fill(title);
        const card = page.locator('.asset-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
        await card.waitFor();
        await card.click();
        const video = page.locator('.detail-media video');
        await video.waitFor();
        await video.evaluate(async (element) => { element.muted = true; await element.play(); });
        await page.waitForFunction(() => {
          const video = document.querySelector('.detail-media video');
          return video && video.currentTime > 0.5 && video.videoWidth === 1080 && video.videoHeight === 1920 && !video.error;
        }, null, { timeout: 30000 });
        await video.evaluate((element) => { element.currentTime = Math.min(5, element.duration / 2); });
        await page.waitForFunction(() => {
          const video = document.querySelector('.detail-media video');
          return video && !video.seeking && video.currentTime >= 4 && !video.error;
        }, null, { timeout: 30000 });
        await page.screenshot({ path: path.join(workdir, `${label}-${result.asset.characterName ? 'instagram' : 'tiktok'}.png`), fullPage: true });
        checks.push(`${label}: ${title} plays and seeks at 1080x1920`);
        await page.getByRole('button', { name: '关闭', exact: true }).click();
      }
      assert.equal(errors.length, 0, errors.join('\n'));
      checks.push(`${label}: login, search and no uncaught JavaScript errors`);
    } finally {
      await browser.close();
    }
  }
  fs.writeFileSync(path.join(workdir, 'browser-receipt.json'), JSON.stringify({ passed: true, checks }, null, 2));
  checks.forEach((check) => console.log('PASS ' + check));
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });

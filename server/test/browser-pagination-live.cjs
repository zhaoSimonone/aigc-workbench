const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { setTimeout: delay } = require('node:timers/promises');
const [workdir, playwrightModule] = process.argv.slice(2);
const { chromium, webkit } = require(playwrightModule);
const fixture = JSON.parse(fs.readFileSync(path.join(workdir, 'fixture.json')));
const root = path.resolve(__dirname, '../../dist');
const checks = [];
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: '127.0.0.1', port: 18083, path: req.url, method: req.method, headers: { ...req.headers, host: 'localhost', 'x-forwarded-proto': 'http' } }, (upstream) => {
      const headers = { ...upstream.headers };
      if (headers['set-cookie']) headers['set-cookie'] = headers['set-cookie'].map((s) => s.replace(/; secure/ig, ''));
      res.writeHead(upstream.statusCode, headers);
      upstream.pipe(res);
    });
    proxy.on('error', () => { res.writeHead(502); res.end(); });
    req.pipe(proxy);
  } else {
    let file = path.join(root, req.url.split('?')[0]);
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(root, 'index.html');
    const type = { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(file)];
    res.writeHead(200, { 'Content-Type': type || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  }
});
const count = async (page, n) => page.waitForFunction((expected) => document.querySelectorAll('.asset-card').length === expected, n);
const mark = (name) => { checks.push(name); console.log('PASS ' + name); };
async function main() {
  await new Promise((resolve) => server.listen(4175, '127.0.0.1', resolve));
  for (const [label, engine, options, viewport] of [
    ['desktop', chromium, { channel: 'chrome' }, { width: 1440, height: 1000 }],
    ['mobile', webkit, {}, { width: 390, height: 844 }],
  ]) {
    if (process.env.BROWSER_ONLY && process.env.BROWSER_ONLY !== label) continue;
    const browser = await engine.launch({ headless: true, ...options });
    try {
      const context = await browser.newContext({ viewport, isMobile: label === 'mobile', hasTouch: label === 'mobile' });
      const page = await context.newPage();
      if (process.env.DEBUG_BROWSER) {
        page.on('console', (message) => console.log('BROWSER', message.text()));
        await page.addInitScript(() => {
          for (const event of ['touchstart', 'touchend', 'click']) document.addEventListener(event, (e) => {
            const more = document.querySelector('.asset-load-more button');
            console.log('INPUT', event, e.target.closest('button')?.textContent || e.target.className, window.scrollY,
              JSON.stringify(more?.getBoundingClientRect()), e.clientY || e.changedTouches?.[0]?.clientY);
          }, true);
        });
      }
      const errors = [], requests = [];
      page.on('pageerror', (e) => errors.push(e.message));
      page.on('request', (r) => {
        if (new URL(r.url()).pathname.startsWith('/api/')) {
          requests.push(new URL(r.url()));
          if (process.env.DEBUG_BROWSER) console.log('REQUEST', new URL(r.url()).pathname + new URL(r.url()).search);
        }
      });
      await page.goto('http://127.0.0.1:4175');
      await page.locator('input[type=email]').fill(fixture.email);
      await page.locator('input[type=password]').fill(fixture.password);
      await page.getByRole('button', { name: '进入工作台' }).click();
      await count(page, 60);
      await page.waitForFunction(() => [...document.querySelectorAll('.asset-card img')].some((img) => img.complete && img.naturalWidth > 0));
      assert.ok(!requests.some((u) => u.pathname === '/api/assets' && !u.searchParams.has('ids')));
      assert.ok(!requests.some((u) => ['/api/prompts', '/api/music-tracks','/api/video-accounts','/api/character-albums'].includes(u.pathname)));
      assert.ok(!requests.some((u) => u.pathname.endsWith('/stream')));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(workdir, `${label}-first-page.png`) });
      mark(`${label}: 60 cards, thumbnails, no full-library/secondary-library/video downloads, no horizontal overflow`);
      const more = page.getByRole('button', { name: /加载更多素材/ });
      await more.scrollIntoViewIfNeeded();
      await (label === 'mobile' ? more.tap() : more.click());
      await count(page, 120);
      await more.scrollIntoViewIfNeeded();
      await (label === 'mobile' ? more.tap() : more.click());
      await count(page, 141);
      const search = page.getByPlaceholder('搜索素材、标签或来源');
      await search.fill('100%_');
      await count(page, 1);
      await page.locator('.asset-card').click();
      await page.locator('.detail-media video').waitFor();
      await page.waitForFunction(() => document.querySelector('.detail-content')?.textContent.includes('完整备注1'));
      const video = page.locator('.detail-media video');
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
      await page.getByTitle('打开关联作品').click();
      await page.waitForFunction(() => document.querySelector('.detail-content')?.textContent.includes('完整备注140'));
      await page.getByRole('button', { name: '编辑素材', exact: true }).click();
      await page.getByRole('button', { name: '关联参考素材', exact: true }).click();
      await page.locator('.asset-picker-selected').getByRole('button', { name: /跨页唯一素材/ }).waitFor();
      const pickerSearch = page.getByRole('textbox', { name: '搜索关联参考素材' });
      await pickerSearch.fill('素材002');
      await page.locator('.reference-option').filter({ hasText: '素材002' }).click();
      assert.ok((await page.locator('.asset-picker-selected button').count()) === 2);
      await pickerSearch.fill('没有此素材');
      await page.getByText('没有匹配素材', { exact: true }).waitFor();
      assert.equal(await page.locator('.asset-picker-selected button').count(), 2);
      await page.screenshot({ path: path.join(workdir, `${label}-picker.png`) });
      await pickerSearch.fill('素材002');
      await page.locator('.reference-option').filter({ hasText: '素材002' }).click();
      await page.getByRole('button', { name: '关联参考素材', exact: true }).click();
      await page.locator('.edit-modal').getByRole('button', { name: '保存修改', exact: true }).click();
      await page.locator('.edit-modal').waitFor({ state: 'detached' });
      const saved = await (await context.request.get(`http://127.0.0.1:4175/api/assets/${fixture.newestId}`)).json();
      assert.deepEqual(saved.asset.parentAssetIds, [fixture.targetId]);
      assert.equal(saved.asset.note, '完整备注140');
      await page.getByRole('button', { name: '关闭', exact: true }).click();
      mark(`${label}: load all pages, cross-page search, full details, playback/seek, relations and persistent picker selection`);
      await search.fill('');
      await count(page, 60);
      // Delay an obsolete search and verify it cannot overwrite the newer result.
      await page.route('**/api/asset-browser?**', async (route) => {
        if (new URL(route.request().url()).searchParams.get('q') === '跨页唯一') await delay(800);
        await route.continue().catch(() => {});
      });
      const obsolete = page.waitForRequest((r) => new URL(r.url()).searchParams.get('q') === '跨页唯一');
      await search.fill('跨页唯一');
      await obsolete;
      await search.fill('跨页音乐');
      await count(page, 1);
      await page.waitForFunction(() => document.querySelector('.asset-card h3')?.textContent.includes('素材003'));
      await delay(900);
      assert.ok((await page.locator('.asset-card h3').textContent()).includes('素材003'));
      await page.unroute('**/api/asset-browser?**');
      // An HTTP failure must be visible and retryable.
      let fail = true;
      await page.route('**/api/asset-browser?**', async (route) => {
        if (fail) { fail = false; await route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"验收模拟失败"}' }); }
        else await route.continue();
      });
      await search.fill('重试触发');
      await page.getByRole('alert').filter({ hasText: '验收模拟失败' }).waitFor();
      await page.getByRole('button', { name: '重试', exact: true }).click();
      await page.getByRole('heading', { name: '没有找到匹配素材' }).waitFor();
      await page.unroute('**/api/asset-browser?**');
      await search.fill('');
      await count(page, 60);
      const nav = async (name) => {
        if (label === 'mobile') await page.getByRole('button', { name: '打开导航' }).click();
        await page.locator('.nav-item').filter({ has: page.locator('span', { hasText: new RegExp(`^${name}$`) }) }).click();
      };
      await nav('角色设定');
      await page.locator('.role-album-card').filter({ hasText: '空相册' }).waitFor();
      await page.getByRole('button', { name: '设置「角色甲」的相册封面', exact: true }).click();
      await page.locator('.album-cover-option').first().waitFor();
      await page.getByRole('textbox', { name: '搜索封面素材' }).fill('100%_');
      await page.waitForFunction(() => document.querySelectorAll('.album-cover-option').length === 1);
      await page.getByRole('button', { name: '关闭', exact: true }).click();
      await page.locator('.role-album-card').filter({ hasText: '角色甲' }).click();
      await page.locator('.character-category-tabs').getByRole('button', { name: /^发型/ }).click();
      await page.locator('.asset-card').first().waitFor();
      assert.ok(await page.locator('.character-category-tabs .active').textContent());
      await nav('提示词库');
      await page.locator('.prompt-linked-assets button').filter({ hasText: '跨页唯一素材' }).waitFor();
      await page.locator('.prompt-linked-assets button').filter({ hasText: '跨页唯一素材' }).click();
      await page.locator('.detail-media video').waitFor();
      await page.getByRole('button', { name: '关闭', exact: true }).click();
      mark(`${label}: stale-response protection, retry, empty albums, cover search, categories and prompt links`);
      // Simulate a 30k catalogue over the real UI, while keeping each response bounded.
      const sample = (await (await context.request.get('http://127.0.0.1:4175/api/asset-browser')).json()).assets[0];
      const ranges = [];
      let shifted = false;
      await page.route('**/api/asset-browser?**', async (route) => {
        const url = new URL(route.request().url());
        const limit = Number(url.searchParams.get('limit') || 60);
        const offset = Number(url.searchParams.get('offset') || 0);
        const q = url.searchParams.get('q');
        const total = q ? (q === '规模素材29999' ? 1 : 0) : 30000;
        const length = Math.min(limit, Math.max(0, total - offset));
        const assets = Array.from({ length }, (_, index) => {
          const n = q ? 29999 : offset + index - (shifted && offset >= 60 ? 10 : 0);
          return { ...sample, id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`, name: `规模素材${n}` };
        });
        ranges.push({ offset, limit, length });
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ assets, total, offset, limit, hasMore: offset + length < total }) });
      });
      await nav('全部素材');
      await count(page, 60);
      const clickPage = async (name) => {
        const button = page.getByRole('button', { name, exact: true });
        await button.scrollIntoViewIfNeeded();
        await (label === 'mobile' ? button.tap() : button.click());
      };
      for (let batch = 0; batch < 6; batch++) {
        await clickPage('加载更多素材');
        await count(page, 120);
        await clickPage('加载更多素材');
        await count(page, 180);
        assert.equal(await page.locator('.asset-card h3').first().textContent(), `规模素材${batch * 180}`);
        await clickPage('下一批素材');
        await count(page, 60);
        assert.equal(await page.locator('.asset-card h3').first().textContent(), `规模素材${(batch + 1) * 180}`);
      }
      await clickPage('上一批素材');
      await count(page, 180);
      assert.equal(await page.locator('.asset-card h3').first().textContent(), '规模素材900');
      const restored = await page.locator('.asset-card').first().boundingBox();
      assert.ok(restored && restored.y >= -1 && restored.y < viewport.height);
      await search.fill('规模素材29999');
      await count(page, 1);
      assert.equal(await page.locator('.asset-card h3').textContent(), '规模素材29999');
      await search.fill('');
      await count(page, 60);
      assert.equal(await page.locator('.asset-card h3').first().textContent(), '规模素材0');
      shifted = true;
      await clickPage('加载更多素材');
      await count(page, 110);
      await clickPage('加载更多素材');
      await count(page, 170);
      await clickPage('下一批素材');
      await count(page, 60);
      assert.equal(await page.locator('.asset-card h3').first().textContent(), '规模素材170');
      await clickPage('上一批素材');
      await count(page, 180);
      assert.equal(await page.locator('.asset-card h3').first().textContent(), '规模素材0');
      assert.ok(ranges.every((r) => r.length <= 180 && r.limit <= 180));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(workdir, `${label}-bounded-catalog.png`) });
      await page.unroute('**/api/asset-browser?**');
      mark(`${label}: 30k catalogue simulation, 1080 items traversed, DOM capped at 180, previous batch, duplicate-page boundary and full-catalogue search`);
      assert.deepEqual(errors, []);
      await context.close();
    } catch (error) {
      const pages = browser.contexts().flatMap((c) => c.pages());
      if (pages[0]) await pages[0].screenshot({ path: path.join(workdir, `${label}-failure.png`) }).catch(() => {});
      throw error;
    } finally { await browser.close(); }
  }
  fs.writeFileSync(path.join(workdir, process.env.BROWSER_ONLY ? `${process.env.BROWSER_ONLY}-results.json` : 'browser-results.json'), JSON.stringify({ passed: true, checks }, null, 2));
}
main().catch((e) => { console.error(e); process.exitCode = 1; }).finally(() => server.close());

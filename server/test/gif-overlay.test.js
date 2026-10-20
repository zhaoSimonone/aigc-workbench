// GIF 素材的展示地址回归测试。
//
// 背景：GIF 不生成静态缩略图（见 preview-generation.test.js），网格里播的就是
// 原始 GIF 对象本身。这里锁定两件事：
//   1. GIF 走稳定签名 URL——URL 每次刷新都变会让浏览器缓存失效、反复重下动画；
//   2. content_type 透传给前端，前端才能把 GIF 和普通图片区分开（详情页显示
//      「GIF 素材」、卡片上显示 GIF 标识）。
//
// 运行：node test/gif-overlay.test.js
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const INDEX = path.join(__dirname, '..', 'src', 'index.js');
const SRC = fs.readFileSync(INDEX, 'utf8');

/** 从真实源码切出函数原文，保证测试跑的是生产代码而不是复制品。 */
function extract(name) {
  let start = SRC.indexOf(`async function ${name}(`);
  if (start < 0) start = SRC.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`未在 src/index.js 中找到函数 ${name}`);
  const open = SRC.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < SRC.length; i += 1) {
    if (SRC[i] === '{') depth += 1;
    else if (SRC[i] === '}') {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, i + 1);
    }
  }
  throw new Error(`函数 ${name} 大括号不匹配`);
}

const BODY = [extract('isGifMime'), extract('hydrateAsset')].join('\n\n');

/** 用假的签名器把 objectUrl / stableSignedUrl 区分开，便于断言走了哪条分支。 */
function load() {
  const objectUrl = async (key) => `https://cos.example/short-lived/${encodeURIComponent(key)}`;
  const stableSignedUrl = (key) => `https://cos.example/stable/${encodeURIComponent(key)}`;
  const formatDuration = (seconds) => `00:${String(Math.round(Number(seconds))).padStart(2, '0')}`;
  const formatBytes = (bytes) => `${bytes} B`;

  const build = new Function(
    'objectUrl', 'stableSignedUrl', 'formatDuration', 'formatBytes',
    `${BODY}\nreturn { hydrateAsset, isGifMime };`,
  );
  return build(objectUrl, stableSignedUrl, formatDuration, formatBytes);
}

const { hydrateAsset, isGifMime } = load();

function baseRow(overrides = {}) {
  return {
    id: '11111111-2222-3333-4444-555555555555',
    name: '心形贴纸-胸口位置',
    type: 'image',
    content_type: 'image/gif',
    source: '本地导入',
    source_url: '',
    character_name: '',
    character_category: '',
    object_key: 'u1/image/2026-10-20/abc-heart.gif',
    thumb_key: null,
    size_bytes: 114167,
    sha256: 'deadbeef',
    duration_seconds: null,
    created_at: '2026-10-20T00:00:00.000Z',
    favorite: false,
    used: false,
    folder: 'GIF 素材',
    note: '',
    tags: [],
    parent_asset_ids: [],
    derived_asset_ids: [],
    ...overrides,
  };
}

let passed = 0;
let failed = 0;

async function test(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed += 1;
  } catch (error) {
    console.log(`  ✗ ${name}\n      ${error.message}`);
    failed += 1;
  }
}

(async () => {
  console.log('\nGIF 素材展示地址回归测试');

  await test('isGifMime 精确识别 image/gif', async () => {
    assert.strictEqual(isGifMime('image/gif'), true);
    assert.strictEqual(isGifMime('image/gif; charset=binary'), true);
    assert.strictEqual(isGifMime('IMAGE/GIF'), true);
    assert.strictEqual(isGifMime('image/png'), false);
    assert.strictEqual(isGifMime('video/mp4'), false);
    assert.strictEqual(isGifMime(''), false);
    assert.strictEqual(isGifMime(undefined), false);
  });

  await test('GIF 用稳定签名 URL', async () => {
    const asset = await hydrateAsset(baseRow());
    assert.ok(asset.src.includes('/stable/'), `期望稳定签名 URL，实际 ${asset.src}`);
    assert.ok(asset.src.endsWith(encodeURIComponent(baseRow().object_key)));
  });

  await test('GIF 没有缩略图，thumb 为空', async () => {
    const asset = await hydrateAsset(baseRow());
    assert.strictEqual(asset.thumb, '');
  });

  await test('普通图片仍然走短期签名 URL（保持既有策略）', async () => {
    const asset = await hydrateAsset(baseRow({ content_type: 'image/png' }));
    assert.ok(asset.src.includes('/short-lived/'), `期望短期签名 URL，实际 ${asset.src}`);
  });

  await test('视频走同源流地址', async () => {
    const asset = await hydrateAsset(baseRow({ type: 'video', content_type: 'video/mp4' }));
    assert.ok(asset.src.startsWith('/api/assets/11111111-2222-3333-4444-555555555555/stream'), asset.src);
  });

  await test('content_type 透传给前端', async () => {
    const asset = await hydrateAsset(baseRow());
    assert.strictEqual(asset.contentType, 'image/gif');
    const png = await hydrateAsset(baseRow({ content_type: 'image/png' }));
    assert.strictEqual(png.contentType, 'image/png');
  });

  await test('folder 原样返回，GIF 素材归到独立库', async () => {
    const asset = await hydrateAsset(baseRow());
    assert.strictEqual(asset.folder, 'GIF 素材');
  });

  console.log(`\n通过 ${passed} 项，失败 ${failed} 项\n`);
  process.exit(failed ? 1 : 0);
})();

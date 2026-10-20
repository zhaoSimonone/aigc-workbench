// createAssetPreview 回归测试。
//
// 背景：线上出现过视频素材没有封面、时长永远停在“待识别”的问题。
// 根因是抽帧与时长共用一个 try，抽帧偶发失败时 catch 会把 durationSeconds
// 一起返回 null，且没有任何回填机制，素材就永久缺封面。
// 本测试用注入故障的方式锁定“抽帧失败不得连坐时长”这条不变量。
//
// 运行：node test/preview-generation.test.js
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

const BODY = ['readVideoDuration', 'scoreGrayFrame', 'extractGrayFrame', 'pickPreviewTime',
  'writeVideoPreview', 'renderPreviewImage', 'isGifMime', 'createAssetPreview'].map(extract).join('\n\n');

/**
 * @param {object} options
 * @param {boolean} options.failRender 抽帧是否抛错
 * @param {boolean} options.failPut    COS 上传是否抛错
 * @param {boolean} options.failProbe  ffprobe 是否抛错
 */
function load({ failRender = false, failPut = false, failProbe = false } = {}) {
  const calls = { render: 0, put: [] };
  const state = { renderedOnDisk: false };

  const execFileAsync = async (bin, args) => {
    if (!args.includes('-frames:v')) throw new Error('非预期的 ffmpeg 调用');
    calls.render += 1;
    if (failRender) throw new Error('模拟 ffmpeg 抽帧失败');
    state.renderedOnDisk = true;
    return { stdout: '' };
  };
  const putObject = async (key, filePath) => {
    if (failPut) throw new Error('模拟 COS 上传失败');
    if (!state.renderedOnDisk) throw new Error('预览文件不存在却被上传');
    calls.put.push(key);
  };
  const fsp = {
    unlink: async () => {},
    stat: async () => {
      if (!state.renderedOnDisk) { const e = new Error('ENOENT'); e.code = 'ENOENT'; throw e; }
      return { size: 1 };
    },
  };
  const probe = async () => {
    if (failProbe) throw new Error('模拟 ffprobe 失败');
    return { stdout: '12.2\n' };
  };

  const build = new Function(
    'execFileAsync', 'putObject', 'fsp', 'console_', 'mediaCacheControl',
    'ffmpegPath', 'ffprobePath', 'execFileProbe', 'failRender',
    `${BODY.replace(
      /async function readVideoDuration\(filePath\) \{[\s\S]*?\n\}/,
      'async function readVideoDuration(filePath) { const r = await execFileProbe(); return Number.parseFloat(String(r.stdout).trim()); }',
    ).replace(
      /async function extractGrayFrame\(filePath, seek\) \{[\s\S]*?\n\}/,
      'async function extractGrayFrame(filePath, seek) { if (failRender) throw new Error("模拟灰度帧失败"); return Buffer.alloc(1024, 128); }',
    )}\nreturn { createAssetPreview };`,
  );

  const { createAssetPreview } = build(
    execFileAsync, putObject, fsp, { warn: () => {} }, 'private',
    '/fake/ffmpeg', '/fake/ffprobe', probe, failRender,
  );
  return { createAssetPreview, calls };
}

const FILE = '/tmp/fake.mp4';
const KEY = 'u/video/2026-10-12/abc.mp4';
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
  console.log('\ncreateAssetPreview 回归测试');

  await test('正常路径产出封面与时长', async () => {
    const { createAssetPreview, calls } = load();
    const r = await createAssetPreview('video', FILE, KEY, 'T');
    assert.strictEqual(r.thumbKey, `${KEY}.jpg`);
    assert.strictEqual(r.durationSeconds, 12.2);
    assert.strictEqual(calls.put.length, 1);
  });

  await test('抽帧失败时时长必须保留（本次线上故障的核心不变量）', async () => {
    const { createAssetPreview } = load({ failRender: true });
    const r = await createAssetPreview('video', FILE, KEY, 'T');
    assert.strictEqual(r.durationSeconds, 12.2, `时长被连坐丢掉了，实际 ${r.durationSeconds}`);
  });

  await test('抽帧失败会触发一次不带选帧的重试', async () => {
    const { createAssetPreview, calls } = load({ failRender: true });
    await createAssetPreview('video', FILE, KEY, 'T');
    assert.ok(calls.render >= 2, `期望至少 2 次渲染尝试，实际 ${calls.render}`);
  });

  await test('彻底失败时不上传空封面，也不抛异常', async () => {
    const { createAssetPreview, calls } = load({ failRender: true });
    const r = await createAssetPreview('video', FILE, KEY, 'T');
    assert.strictEqual(r.thumbKey, null);
    assert.strictEqual(calls.put.length, 0, '不该上传任何东西');
  });

  await test('COS 上传失败时时长仍保留', async () => {
    const { createAssetPreview } = load({ failPut: true });
    const r = await createAssetPreview('video', FILE, KEY, 'T');
    assert.strictEqual(r.durationSeconds, 12.2);
    assert.strictEqual(r.thumbKey, null);
  });

  await test('ffprobe 同时失败时优雅返回 null', async () => {
    const { createAssetPreview } = load({ failRender: true, failProbe: true });
    const r = await createAssetPreview('video', FILE, KEY, 'T');
    assert.strictEqual(r.durationSeconds, null);
    assert.strictEqual(r.thumbKey, null);
  });

  await test('图片类型正常产出封面且无时长', async () => {
    const { createAssetPreview } = load();
    const r = await createAssetPreview('image', FILE, KEY, 'T');
    assert.strictEqual(r.thumbKey, `${KEY}.jpg`);
    assert.strictEqual(r.durationSeconds, null);
  });

  await test('未知类型直接返回空且不调用 ffmpeg', async () => {
    const { createAssetPreview, calls } = load();
    const r = await createAssetPreview('audio', FILE, KEY, 'T');
    assert.deepStrictEqual(r, { thumbKey: null, durationSeconds: null });
    assert.strictEqual(calls.render, 0);
  });

  await test('GIF 不生成静态首帧缩略图，也不调用 ffmpeg', async () => {
    const { createAssetPreview, calls } = load();
    const r = await createAssetPreview('image', FILE, KEY, 'T', 'image/gif');
    assert.deepStrictEqual(r, { thumbKey: null, durationSeconds: null });
    assert.strictEqual(calls.render, 0, 'GIF 不该被抽成静态帧');
    assert.strictEqual(calls.put.length, 0, 'GIF 不该产生缩略图对象');
  });

  await test('非 GIF 图片仍然照常生成缩略图', async () => {
    const { createAssetPreview, calls } = load();
    const r = await createAssetPreview('image', FILE, KEY, 'T', 'image/png');
    assert.strictEqual(r.thumbKey, `${KEY}.jpg`);
    assert.strictEqual(calls.render, 1);
  });

  await test('isGifMime 只认 image/gif', async () => {
    const { createAssetPreview } = load();
    // 通过 createAssetPreview 间接验证：只有真的是 GIF 才跳过缩略图
    const gif = await createAssetPreview('image', FILE, KEY, 'T', 'image/gif');
    const png = await createAssetPreview('image', FILE, KEY, 'T', 'image/png');
    assert.strictEqual(gif.thumbKey, null);
    assert.strictEqual(png.thumbKey, `${KEY}.jpg`);
  });

  console.log(`\n通过 ${passed} 项，失败 ${failed} 项\n`);
  process.exit(failed ? 1 : 0);
})();

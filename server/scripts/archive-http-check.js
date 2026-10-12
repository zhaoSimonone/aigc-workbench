// 归档接口 HTTP 冒烟测试：只用内存桩替换 pg，其余全部走真实 Express 路由和真实文件系统。
// 用于在没有 PostgreSQL 的机器上验证路由、鉴权、白名单校验和只读扫描。
// 用法：node server/scripts/archive-http-check.js
const assert = require('assert');
const Module = require('module');
const path = require('path');

const TEST_USER = { id: '11111111-1111-4111-8111-111111111111', name: '归档测试', email: 'archive@example.com' };
const SCAN_TARGET = process.env.ARCHIVE_TEST_SCAN_PATH || '/Users/simon/Desktop/AIGC/1007视频';
const PORT = Number(process.env.ARCHIVE_TEST_PORT || 18099);

process.env.PORT = String(PORT);
process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://archive-smoke-test/fake';
process.env.ARCHIVE_ALLOWED_SOURCE_ROOTS = path.dirname(SCAN_TARGET);
process.env.ARCHIVE_STORAGE_BACKEND = 'cos';
process.env.ARCHIVE_LOCAL_ROOT = '';
delete process.env.TENCENT_COS_SECRET_ID;
delete process.env.TENCENT_COS_SECRET_KEY;

const sessionRow = { ...TEST_USER };
const fakePool = {
  async query(text) {
    const sql = String(text || '');
    if (sql.includes('FROM sessions s JOIN users u')) return { rows: [sessionRow], rowCount: 1 };
    if (sql.includes('COUNT(*)::int AS total')) return { rows: [{ total: 0 }], rowCount: 1 };
    return { rows: [], rowCount: 0 };
  },
  async connect() {
    return { query: fakePool.query, release() {} };
  },
};

const originalLoad = Module._load;
Module._load = function patchedLoad(request, parent, isMain) {
  if (request === 'pg') return { Pool: class FakePool { constructor() { return fakePool; } } };
  return originalLoad.call(this, request, parent, isMain);
};

const results = [];
let failures = 0;

async function test(name, fn) {
  try {
    await fn();
    results.push(`  PASS  ${name}`);
  } catch (error) {
    failures += 1;
    results.push(`  FAIL  ${name}\n        ${error.message}`);
  }
}

async function call(pathname, { method = 'GET', body, auth = true } = {}) {
  const response = await fetch(`http://127.0.0.1:${PORT}${pathname}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(auth ? { Cookie: 'aigc_session=archive-smoke-test' } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  return { status: response.status, payload };
}

async function waitForServer() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/api/health`);
      if (response.status) return;
    } catch (_) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error('服务未在预期时间内启动');
}

async function main() {
  require('../src/index.js');
  await waitForServer();

  results.push('鉴权');
  await test('未登录访问归档接口返回 401', async () => {
    const scan = await call('/api/archive/scan', { method: 'POST', body: { sourcePath: SCAN_TARGET }, auth: false });
    assert.strictEqual(scan.status, 401);
    const options = await call('/api/archive/options', { auth: false });
    assert.strictEqual(options.status, 401);
  });

  results.push('路径白名单');
  await test('白名单外的目录返回 403', async () => {
    const response = await call('/api/archive/scan', { method: 'POST', body: { sourcePath: '/etc' } });
    assert.strictEqual(response.status, 403);
    assert.match(response.payload.error, /白名单/);
  });
  await test('.. 逃逸返回 400', async () => {
    const response = await call('/api/archive/scan', { method: 'POST', body: { sourcePath: `${path.dirname(SCAN_TARGET)}/../..` } });
    assert.strictEqual(response.status, 400);
  });
  await test('目录不存在返回 404', async () => {
    const response = await call('/api/archive/scan', { method: 'POST', body: { sourcePath: path.join(path.dirname(SCAN_TARGET), '不存在的目录-xyz') } });
    assert.strictEqual(response.status, 404);
  });
  await test('缺少路径参数返回 400', async () => {
    const response = await call('/api/archive/scan', { method: 'POST', body: {} });
    assert.strictEqual(response.status, 400);
  });

  results.push('只读扫描');
  let scan = null;
  await test('扫描真实目录返回统计，不返回文件内容', async () => {
    const response = await call('/api/archive/scan', { method: 'POST', body: { sourcePath: SCAN_TARGET } });
    assert.strictEqual(response.status, 200);
    scan = response.payload.scan;
    assert.strictEqual(scan.fileCount, 287);
    assert.strictEqual(scan.totalBytes, 618298523);
    assert.ok(scan.byKind.video > 0 && scan.byKind.image > 0 && scan.byKind.document > 0);
    assert.ok(Array.isArray(scan.entries));
    assert.ok(!JSON.stringify(scan.entries).includes('"content"'));
    assert.ok(scan.entries.every((entry) => entry.sizeBytes >= 0 && entry.kind));
  });
  await test('扫描结果中的条目都位于源目录内', async () => {
    assert.ok(scan.entries.every((entry) => !entry.relativePath.startsWith('/') && !entry.relativePath.includes('..')));
  });

  results.push('批次与文件接口');
  await test('未配置 COS 密钥时创建 COS 批次被拒绝', async () => {
    const response = await call('/api/archive/batches', { method: 'POST', body: { sourcePath: SCAN_TARGET, storageBackend: 'cos' } });
    assert.strictEqual(response.status, 409);
    assert.match(response.payload.error, /COS 配置不完整/);
  });
  await test('未配置 local 根目录时创建 local 批次被拒绝', async () => {
    const response = await call('/api/archive/batches', { method: 'POST', body: { sourcePath: SCAN_TARGET, storageBackend: 'local' } });
    assert.strictEqual(response.status, 409);
  });
  await test('无效后端返回 400', async () => {
    const response = await call('/api/archive/batches', { method: 'POST', body: { sourcePath: SCAN_TARGET, storageBackend: 's3' } });
    assert.strictEqual(response.status, 400);
  });
  await test('批次列表返回数组结构', async () => {
    const response = await call('/api/archive/batches');
    assert.strictEqual(response.status, 200);
    assert.deepStrictEqual(response.payload.batches, []);
  });
  await test('文件列表返回分页结构', async () => {
    const response = await call('/api/archive/files?page=1&pageSize=10&kind=document');
    assert.strictEqual(response.status, 200);
    assert.deepStrictEqual(response.payload.files, []);
    assert.strictEqual(response.payload.page, 1);
    assert.strictEqual(response.payload.pageSize, 10);
  });
  await test('不存在的批次与文件返回 404', async () => {
    const batch = await call('/api/archive/batches/22222222-2222-4222-8222-222222222222');
    assert.strictEqual(batch.status, 404);
    const file = await call('/api/archive/files/33333333-3333-4333-8333-333333333333');
    assert.strictEqual(file.status, 404);
  });
  await test('清理源文件接口对不存在的批次返回 404', async () => {
    const response = await call('/api/archive/batches/22222222-2222-4222-8222-222222222222/cleanup-source', { method: 'POST', body: {} });
    assert.strictEqual(response.status, 404);
  });
  await test('请求体批次 ID 与路径不一致时返回 400', async () => {
    const response = await call('/api/archive/batches/22222222-2222-4222-8222-222222222222/cleanup-source', {
      method: 'POST',
      body: { batchId: '44444444-4444-4444-8444-444444444444' },
    });
    assert.strictEqual(response.status, 400);
  });
  await test('options 返回后端可用性与白名单', async () => {
    const response = await call('/api/archive/options');
    assert.strictEqual(response.status, 200);
    assert.deepStrictEqual(response.payload.options.backends.map((item) => item.available), [false, false]);
    assert.strictEqual(response.payload.options.allowedRoots.length, 1);
  });

  console.log(results.join('\n'));
  console.log(`\n${failures ? `FAILED: ${failures} 项` : 'ALL PASSED'}`);
  process.exit(failures ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
// 百度网盘目录接口 HTTP 冒烟测试：只用内存桩替换 pg，其余走真实 Express 路由。
// 默认会用 netdisk_sync.py publish-catalog 产出的真实目录快照（若存在）走一遍导入与查询。
// 用法：node server/scripts/netdisk-http-check.js
const assert = require('assert');
const fs = require('fs');
const Module = require('module');
const path = require('path');

const PORT = Number(process.env.NETDISK_TEST_PORT || 18098);
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const CATALOG_FILE = path.join(REPO_ROOT, 'outputs', 'netdisk-catalog.json');
const TEST_USER = { id: '11111111-1111-4111-8111-111111111111', name: '网盘测试', email: 'netdisk@example.com' };

process.env.PORT = String(PORT);
process.env.DATABASE_URL = process.env.DATABASE_URL || 'postgres://netdisk-smoke-test/fake';
process.env.NETDISK_ROOT_PATH = '/AIGC';

const state = { snapshots: [], nodes: [] };

const fakePool = {
  async query(text, params = []) {
    const sql = String(text || '');
    if (sql.includes('FROM sessions s JOIN users u')) return { rows: [{ ...TEST_USER }], rowCount: 1 };
    if (sql.includes('INSERT INTO netdisk_snapshots')) {
      const row = {
        id: `s${state.snapshots.length + 1}`, user_id: params[0], root_path: params[1], source: params[2],
        file_count: params[3], dir_count: params[4], total_bytes: params[5], status: 'ready', last_error: '', created_at: new Date().toISOString(),
      };
      state.snapshots.push(row);
      return { rows: [row], rowCount: 1 };
    }
    if (sql.includes('INSERT INTO netdisk_nodes')) {
      for (let index = 0; index < params.length; index += 14) {
        const node = {
          user_id: params[index], path: params[index + 3], parent_path: params[index + 4], name: params[index + 5],
          is_dir: params[index + 6], kind: params[index + 7], size_bytes: params[index + 8],
          server_mtime: params[index + 9], fs_id: params[index + 2], category: params[index + 10],
          local_state: params[index + 11], local_size_bytes: params[index + 12],
        };
        const existing = state.nodes.findIndex((item) => item.user_id === node.user_id && item.path === node.path);
        if (existing >= 0) state.nodes[existing] = node;
        else state.nodes.push(node);
      }
      return { rows: [], rowCount: 0 };
    }
    if (sql.startsWith('DELETE FROM netdisk_nodes')) {
      const [userId, root, prefix, keep] = params;
      const literalPrefix = String(prefix).replace(/%$/, '');
      const before = state.nodes.length;
      state.nodes = state.nodes.filter((item) => !(item.user_id === userId && (item.path === root || item.path.startsWith(literalPrefix)) && !keep.includes(item.path)));
      return { rows: [], rowCount: before - state.nodes.length };
    }
    if (sql.includes('FROM netdisk_snapshots') && sql.includes('created_at DESC')) return { rows: state.snapshots.slice(-1), rowCount: 1 };
    if (sql.includes('GROUP BY kind')) {
      const groups = new Map();
      state.nodes.filter((node) => node.user_id === params[0] && !node.is_dir).forEach((node) => {
        const item = groups.get(node.kind) || { kind: node.kind, count: 0, bytes: 0 };
        item.count += 1;
        item.bytes += node.size_bytes;
        groups.set(node.kind, item);
      });
      return { rows: [...groups.values()], rowCount: 1 };
    }
    if (sql.includes('FROM netdisk_nodes d')) {
      return { rows: state.nodes.filter((node) => node.user_id === params[0] && node.is_dir && node.parent_path === params[1]).slice(0, 20), rowCount: 1 };
    }
    if (sql.includes('parent_path=$2')) {
      return { rows: state.nodes.filter((node) => node.user_id === params[0] && node.parent_path === params[1]), rowCount: 1 };
    }
    if (sql.includes('name ILIKE')) {
      return { rows: state.nodes.filter((node) => node.user_id === params[0] && !node.is_dir && node.name.includes(params[1].replace(/%/g, ''))), rowCount: 1 };
    }
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
    headers: { 'Content-Type': 'application/json', ...(auth ? { Cookie: 'aigc_session=netdisk-smoke-test' } : {}) },
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

  results.push('鉴权与白名单');
  await test('未登录访问返回 401', async () => {
    assert.strictEqual((await call('/api/netdisk/tree', { auth: false })).status, 401);
    assert.strictEqual((await call('/api/netdisk/catalog', { method: 'POST', body: { nodes: [] }, auth: false })).status, 401);
  });
  await test('查询 /AIGC 以外的路径返回 400', async () => {
    const response = await call('/api/netdisk/children?path=/Pictures');
    assert.strictEqual(response.status, 400);
    assert.match(response.payload.error, /不在允许的网盘根目录/);
    const traversal = await call('/api/netdisk/children?path=/AIGC/../secret');
    assert.strictEqual(traversal.status, 400);
  });
  await test('catalog 拒绝 /AIGC 以外的节点', async () => {
    const response = await call('/api/netdisk/catalog', {
      method: 'POST',
      body: { root: '/AIGC', nodes: [{ path: '/私有相册/秘密.mp4', name: '秘密.mp4', isDir: false, kind: 'video' }] },
    });
    assert.strictEqual(response.status, 400);
    assert.match(response.payload.error, /不在允许的网盘根目录/);
  });
  await test('catalog 拒绝携带凭据字段的请求体', async () => {
    const token = await call('/api/netdisk/catalog', {
      method: 'POST',
      body: { root: '/AIGC', access_token: 'should-be-rejected', nodes: [] },
    });
    assert.strictEqual(token.status, 400);
    const dlink = await call('/api/netdisk/catalog', {
      method: 'POST',
      body: { root: '/AIGC', nodes: [{ path: '/AIGC/a.mp4', name: 'a.mp4', isDir: false, dlink: 'https://d.pcs.baidu.com/file?sign=x' }] },
    });
    assert.strictEqual(dlink.status, 400);
  });
  await test('catalog 拒绝带文件内容的请求体', async () => {
    const base64 = Buffer.alloc(2048, 9).toString('base64');
    const response = await call('/api/netdisk/catalog', {
      method: 'POST',
      body: { root: '/AIGC', nodes: [{ path: '/AIGC/a.mp4', name: 'a.mp4', isDir: false, content: base64 }] },
    });
    assert.strictEqual(response.status, 400);
    assert.match(response.payload.error, /拒绝字段/);
  });
  await test('错误响应里不含请求中的凭据', async () => {
    const response = await call('/api/netdisk/catalog', {
      method: 'POST',
      body: { root: '/AIGC', access_token: 'super-secret-value', nodes: [] },
    });
    assert.ok(!JSON.stringify(response.payload).includes('super-secret-value'), '响应泄露了凭据');
  });

  results.push('真实目录快照导入');
  const hasCatalog = fs.existsSync(CATALOG_FILE);
  let catalog = null;
  if (hasCatalog) catalog = JSON.parse(fs.readFileSync(CATALOG_FILE, 'utf8'));
  await test(hasCatalog ? `导入真实快照（${catalog.fileCount} 个文件 / ${catalog.dirCount} 个目录）` : '跳过：还没有 outputs/netdisk-catalog.json', async () => {
    if (!hasCatalog) return;
    assert.strictEqual(catalog.root, '/AIGC');
    const response = await call('/api/netdisk/catalog', { method: 'POST', body: catalog });
    assert.strictEqual(response.status, 201);
    assert.strictEqual(response.payload.catalog.fileCount, catalog.fileCount);
    assert.ok(state.nodes.length === catalog.nodes.length);
    // 快照本身不得含任何凭据字段
    const serialised = JSON.stringify(response.payload).toLowerCase();
    for (const forbidden of ['access_token', 'dlink', 'cookie', 'session_secret']) {
      assert.ok(!serialised.includes(forbidden), forbidden);
    }
  });
  await test('树接口返回 /AIGC 的直接子节点', async () => {
    const response = await call('/api/netdisk/tree?path=/AIGC');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.rootPath, '/AIGC');
    assert.ok(Array.isArray(response.payload.nodes));
    if (hasCatalog) {
      assert.ok(response.payload.nodes.length > 0, '真实快照应有子节点');
      assert.strictEqual(response.payload.needsSync, false);
      assert.ok(response.payload.snapshot && response.payload.snapshot.source);
      const names = response.payload.nodes.map((node) => node.name);
      console.log(`        /AIGC 一级目录：${names.join('、')}`);
    } else {
      assert.strictEqual(response.payload.needsSync, true);
    }
    assert.ok(!response.payload.nodes.some((node) => node.path === '/AIGC'), '根节点不应把自己列成子节点');
  });
  await test('可以进入子目录并看到文件大小', async () => {
    const top = await call('/api/netdisk/tree?path=/AIGC');
    const dir = top.payload.nodes.find((node) => node.isDir);
    if (!dir) {
      assert.strictEqual(top.payload.needsSync, hasCatalog ? false : true);
      return;
    }
    const response = await call(`/api/netdisk/children?path=${encodeURIComponent(dir.path)}`);
    assert.strictEqual(response.status, 200);
    assert.ok(response.payload.nodes.every((node) => node.path.startsWith('/AIGC/')));
    console.log(`        ${dir.path} 下有 ${response.payload.nodes.length} 项`);
  });
  await test('搜索按文件名匹配', async () => {
    const response = await call('/api/netdisk/search?q=成片');
    assert.strictEqual(response.status, 200);
    assert.ok(Array.isArray(response.payload.nodes));
    assert.ok(response.payload.nodes.every((node) => !node.isDir));
  });
  await test('统计接口返回快照与类型分布', async () => {
    const response = await call('/api/netdisk/stats');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.rootPath, '/AIGC');
    assert.ok(Array.isArray(response.payload.byKind));
    if (hasCatalog) assert.ok(response.payload.snapshot.totalBytes > 0);
    console.log(`        类型分布：${response.payload.byKind.map((item) => `${item.kind}=${item.count}`).join(' ') || '（空）'}`);
  });

  results.push('同步命令');
  await test('sync-commands 返回可复制的本机命令', async () => {
    const response = await call('/api/netdisk/sync-commands?localPath=/Users/simon/Desktop/AIGC/1007视频');
    assert.strictEqual(response.status, 200);
    const ids = response.payload.commands.map((command) => command.id);
    for (const expected of ['dry-run', 'upload', 'tree', 'publish-file', 'publish-api']) {
      assert.ok(ids.includes(expected), `缺少 ${expected}`);
    }
    assert.ok(response.payload.commands.every((command) => command.command.includes('netdisk_sync.py')));
    assert.ok(!JSON.stringify(response.payload).toLowerCase().includes('access_token'));
    assert.strictEqual(response.payload.commands[0].command.includes('/Users/simon/Desktop/AIGC/1007视频'), true);
  });
  await test('非法 localPath 不会污染生成的命令', async () => {
    const response = await call('/api/netdisk/sync-commands?localPath=; rm -rf /');
    assert.strictEqual(response.status, 200);
    assert.ok(!response.payload.commands[0].command.includes('rm -rf'));
  });
  await test('本机导入接口拒绝非白名单文件', async () => {
    const response = await call('/api/netdisk/import-local-catalog', { method: 'POST', body: { file: 'package.json' } });
    assert.strictEqual(response.status, 403);
  });
  await test('本机导入接口可读取 outputs/netdisk-catalog.json', async () => {
    const response = await call('/api/netdisk/import-local-catalog', { method: 'POST', body: {} });
    if (hasCatalog) {
      assert.strictEqual(response.status, 201);
      assert.strictEqual(response.payload.catalog.fileCount, catalog.fileCount);
      console.log(`        已通过本机降级导入 ${response.payload.catalog.fileCount} 个文件目录节点`);
    } else {
      assert.strictEqual(response.status, 404);
    }
  });

  console.log(results.join('\n'));
  console.log(`\n${failures ? `FAILED: ${failures} 项` : 'ALL PASSED'}`);
  process.exit(failures ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
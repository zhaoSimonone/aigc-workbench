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
// 测试专用加密密钥（只在本进程内使用，不是任何环境的真实密钥）
process.env.NETDISK_TOKEN_ENCRYPTION_KEY = require('crypto').randomBytes(32).toString('base64');

const FAKE_TOKEN = 'fake-token-for-http-check-000000000000';
const BAIDU_TREE = {
  '/AIGC': [[{ path: '/AIGC/902视频', isdir: 1, size: 0, fs_id: '497116331455040', server_mtime: 1791787162, category: 6 }]],
  '/AIGC/902视频/成片': [[
    { path: '/AIGC/902视频/成片/示例成片.mp4', isdir: 0, size: 2048, fs_id: '964775760105557', server_mtime: 1791786900, category: 1, dlink: 'https://d.pcs.baidu.com/file?sign=SECRET' },
  ]],
  '/AIGC/902视频': [[
    { path: '/AIGC/902视频/成片', isdir: 1, size: 0, fs_id: '964775760105555', server_mtime: 1791786548, category: 6 },
    { path: '/AIGC/902视频/照片', isdir: 1, size: 0, fs_id: '964775760105556', server_mtime: 1791786549, category: 6 },
    { path: '/AIGC/902视频/成片/示例成片.mp4', isdir: 0, size: 2048, fs_id: '964775760105557', server_mtime: 1791786900, category: 1, dlink: 'https://d.pcs.baidu.com/file?sign=SECRET' },
  ]],
};
const originalFetch = globalThis.fetch;
let baiduCalls = [];
globalThis.fetch = async (input, init) => {
  const url = new URL(String(input));
  // 非百度请求原样放行；必须把 init 一起传下去，否则 Cookie 等 header 会丢
  if (url.hostname !== 'pan.baidu.com') return originalFetch(input, init);
  baiduCalls.push(url.pathname);
  if (url.searchParams.get('access_token') !== FAKE_TOKEN) {
    return { ok: true, status: 200, json: async () => ({ errno: 111, errmsg: 'invalid token' }) };
  }
  const dir = url.searchParams.get('dir');
  const list = (BAIDU_TREE[dir] || [])[Number(url.searchParams.get('page')) - 1] || [];
  return { ok: true, status: 200, json: async () => ({ errno: 0, list }) };
};

const state = { snapshots: [], nodes: [], credentials: new Map() };

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
    if (sql.startsWith('DELETE FROM netdisk_nodes WHERE user_id=$1 AND id = ANY')) {
      const [userId, ids] = params;
      const before = state.nodes.length;
      state.nodes = state.nodes.filter((item) => !(item.user_id === userId && ids.includes(item.id)));
      return { rows: [], rowCount: before - state.nodes.length };
    }
    if (sql.startsWith('DELETE FROM netdisk_nodes')) {
      const [userId, root, prefix, keep] = params;
      const literalPrefix = String(prefix).replace(/%$/, '');
      const before = state.nodes.length;
      state.nodes = state.nodes.filter((item) => !(item.user_id === userId && (item.path === root || item.path.startsWith(literalPrefix)) && !keep.includes(item.path)));
      return { rows: [], rowCount: before - state.nodes.length };
    }
    if (sql.includes('DELETE FROM netdisk_credentials')) { state.credentials.delete(params[0]); return { rows: [], rowCount: 1 }; }
  if (sql.includes('SELECT') && sql.includes('FROM netdisk_credentials WHERE user_id=$1')) {
    const row = state.credentials.get(params[0]);
    return { rows: row ? [row] : [], rowCount: row ? 1 : 0 };
  }
  if (sql.includes('INSERT INTO netdisk_credentials')) {
    state.credentials.set(params[0], {
      user_id: params[0], provider: params[1], encrypted_token: params[2], token_last4: params[3],
      scope: params[4], status: 'active', expires_at: params[5], obtained_at: params[6],
      last_verified_at: null, last_error: '', updated_at: new Date(),
    });
    return { rows: [], rowCount: 1 };
  }
  if (sql.includes("SET status='invalid'")) {
    const row = state.credentials.get(params[0]);
    if (row && row.status !== 'expired') { row.status = 'invalid'; row.last_error = params[1]; }
    return { rows: [], rowCount: 1 };
  }
  if (sql.includes('SET status=$1') && sql.includes('last_verified_at=$2')) {
    const row = state.credentials.get(params[0]);
    if (row) { row.status = params[1]; row.last_error = ''; row.last_verified_at = params[2]; }
    return { rows: [], rowCount: 1 };
  }
  if (sql.includes('path LIKE ANY')) {
    const patterns = params[1].map((pattern) => new RegExp(`^${pattern.replace(/%$/, '.*')}$`));
    const found = state.nodes.filter((item) => item.user_id === params[0] && patterns.some((re) => re.test(item.path)));
    return { rows: found.map((item) => ({ id: item.id })), rowCount: found.length };
  }
  if (sql.startsWith('DELETE FROM netdisk_nodes')) {
    const [userId, ids] = params;
    const before = state.nodes.length;
    state.nodes = state.nodes.filter((item) => !(item.user_id === userId && ids.includes(item.id)));
    return { rows: [], rowCount: before - state.nodes.length };
  }
  if (sql.includes('MAX(updated_at)')) return { rows: [{ updated_at: new Date(), total: state.nodes.length }], rowCount: 1 };
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
  const raw = await response.text().catch(() => "");
  let payload = null;
  try { payload = JSON.parse(raw); } catch (_) {}
  return { status: response.status, payload, raw };
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

  results.push('凭据生命周期');
  await test('未配置时 GET credential 返回 configured=false 且不含 Token', async () => {
    const response = await call('/api/netdisk/credential');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.credential.configured, false);
    assert.strictEqual(response.payload.credential.purpose.includes('不会用此 Token 上传'), true);
    assert.ok(response.payload.credential.authorizeUrl.includes('openapi.baidu.com/oauth/2.0/authorize'));
    assert.ok(!JSON.stringify(response.payload).includes(FAKE_TOKEN));
  });
  await test('options 返回 authorizeUrl、purpose 和加密配置状态', async () => {
    const response = await call('/api/netdisk/options');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.options.rootPath, '/AIGC');
    assert.ok(response.payload.options.authorizeUrl.includes('openapi.baidu.com'));
    assert.strictEqual(response.payload.options.encryptionConfigured, true);
  });
  await test('无凭据时 children 不打百度，返回 need_credential', async () => {
    const before = baiduCalls.length;
    const response = await call('/api/netdisk/children?path=/AIGC');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.source, 'none');
    assert.strictEqual(response.payload.errorCode, 'need_credential');
    assert.deepStrictEqual(response.payload.nodes, []);
    assert.strictEqual(baiduCalls.length, before, '没有凭据时不能请求百度');
  });
  await test('PUT 粘贴整段回调 → 探活成功 → 响应不含 Token', async () => {
    const pasted = `https://openapi.baidu.com/oauth/2.0/login_success#expires_in=2592000&access_token=${FAKE_TOKEN}&session_secret=&session_key=&scope=basic+netdisk`;
    const response = await call('/api/netdisk/credential', { method: 'PUT', body: { credential: pasted } });
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.credential.configured, true);
    assert.strictEqual(response.payload.credential.status, 'active');
    assert.strictEqual(response.payload.credential.tokenLast4, FAKE_TOKEN.slice(-4));
    const serialised = JSON.stringify(response.payload);
    assert.ok(!serialised.includes(FAKE_TOKEN), '响应泄露了 Token');
    assert.ok(!serialised.includes('login_success'), '响应泄露了完整回调');
    assert.ok(!state.credentials.get(TEST_USER.id).encrypted_token.includes(FAKE_TOKEN), '库里存的是明文');
  });
  await test('配置后 children 实时读取 /AIGC 下一层', async () => {
    const response = await call('/api/netdisk/children?path=/AIGC');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.source, 'live');
    assert.ok(response.payload.fetchedAt);
    assert.deepStrictEqual(response.payload.nodes.map((node) => node.name), ['902视频']);
    console.log(`        实时读取 /AIGC：${response.payload.nodes.map((node) => node.path).join('、')}`);
  });
  await test('进入子目录看到里面的文件与大小', async () => {
    const dir = await call('/api/netdisk/tree?path=/AIGC/902视频');
    assert.strictEqual(dir.status, 200);
    assert.strictEqual(dir.payload.source, 'live');
    const names = dir.payload.nodes.map((node) => node.name);
    assert.ok(names.includes('成片') && names.includes('照片'), `实际：${names.join('、')}`);
    console.log(`        /AIGC/902视频 下有 ${dir.payload.nodes.length} 项：${names.join('、')}`);

    const fileResponse = await call('/api/netdisk/children?path=/AIGC/902视频/成片');
    const file = fileResponse.payload.nodes.find((node) => node.name === '示例成片.mp4');
    assert.ok(file, '应能看到具体文件');
    assert.strictEqual(file.sizeBytes, 2048);
    assert.strictEqual(file.kind, 'video');
    assert.strictEqual(typeof file.fsId, 'string');
    assert.ok(!JSON.stringify(fileResponse.payload.nodes).includes('dlink'), '响应泄露 dlink');
    console.log(`        /AIGC/902视频/成片 下有：${fileResponse.payload.nodes.map((n) => n.name).join('、')}`);
  });
  await test('拒绝 /AIGC 以外的路径且不打百度', async () => {
    const before = baiduCalls.length;
    const outside = await call('/api/netdisk/children?path=/Pictures');
    assert.strictEqual(outside.status, 400);
    const traversal = await call('/api/netdisk/children?path=/AIGC/../secret');
    assert.strictEqual(traversal.status, 400);
    assert.strictEqual(baiduCalls.length, before);
  });
  await test('错误 Token 保存后被标为失效，且响应不含 Token', async () => {
    const response = await call('/api/netdisk/credential', { method: 'PUT', body: { accessToken: 'this-token-is-not-valid-000000000000' } });
    assert.strictEqual(response.status, 400);
    assert.strictEqual(response.payload.errorCode, 'token_invalid');
    assert.ok(!JSON.stringify(response.payload).includes('this-token-is-not-valid'));
    assert.strictEqual(response.payload.credential.status, 'invalid');
  });
  await test('凭据失效时 children 降级到缓存并给出 token_invalid', async () => {
    const response = await call('/api/netdisk/children?path=/AIGC');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.errorCode, 'token_invalid');
    assert.strictEqual(response.payload.source, 'cache');
    assert.ok(response.payload.nodes.length > 0, '应回落到已缓存的目录');
    assert.match(response.payload.warning, /缓存/);
  });
  await test('删除凭据后缓存仍在，但不再打百度', async () => {
    const removed = await call('/api/netdisk/credential', { method: 'DELETE' });
    assert.strictEqual(removed.status, 200);
    assert.strictEqual(removed.payload.credential.configured, false);
    const before = baiduCalls.length;
    const response = await call('/api/netdisk/children?path=/AIGC');
    assert.strictEqual(baiduCalls.length, before, '移除凭据后不能再请求百度');
    assert.strictEqual(response.payload.source, 'cache');
    assert.strictEqual(response.payload.credential.configured, false);
    console.log(`        移除凭据后仍可浏览 ${response.payload.nodes.length} 个缓存节点`);
  });

  results.push('缓存派生接口');
  await test('搜索只查缓存，不打百度', async () => {
    const before = baiduCalls.length;
    const response = await call('/api/netdisk/search?q=成片');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(baiduCalls.length, before);
    assert.ok(response.payload.nodes.every((node) => !node.isDir));
  });
  await test('统计标明来自缓存并给出缓存更新时间', async () => {
    const response = await call('/api/netdisk/stats');
    assert.strictEqual(response.status, 200);
    assert.strictEqual(response.payload.fromCache, true);
    assert.ok(response.payload.cacheUpdatedAt);
    assert.ok(response.payload.cachedNodeCount > 0);
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
  await test('本机导入接口可读取 outputs/netdisk-catalog.json（可选暖缓存）', async () => {
    const response = await call('/api/netdisk/import-local-catalog', { method: 'POST', body: {} });
    if (fs.existsSync(path.join(__dirname, '..', '..', 'outputs', 'netdisk-catalog.json'))) {
      assert.strictEqual(response.status, 201);
      console.log(`        本机降级导入 ${response.payload.catalog.fileCount} 个文件目录节点（暖缓存，不影响实时读取）`);
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
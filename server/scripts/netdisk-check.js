// 百度网盘目录接口测试：用内存 SQL 桩跑真实的解析、白名单和事务逻辑。
// 用法：node server/scripts/netdisk-check.js
const assert = require('assert');
const path = require('path');
const fs = require('fs');
const os = require('os');

const {
  applyCatalog,
  assertNetdiskPath,
  assertNoFileContent,
  loadNetdiskConfig,
  normaliseNodePath,
  parseCatalogBody,
  publicNode,
  readWhitelistedCatalog,
} = require('../src/netdisk-catalog');

const results = [];
let failures = 0;

async function test(name, fn) {
  try {
    await fn();
    results.push(`  PASS  ${name}`);
  } catch (error) {
    failures += 1;
    results.push(`  FAIL  ${name}\n        ${error.stack.split('\n').slice(0, 3).join('\n        ')}`);
  }
}

const USER = '11111111-1111-4111-8111-111111111111';
const ROOT = '/AIGC';

function makePool() {
  const state = { snapshots: [], nodes: [], statements: [] };
  const pool = {
    state,
    async query(text, params = []) {
      const sql = String(text || '');
      state.statements.push(sql.trim().split('\n')[0]);
      if (sql.includes('INSERT INTO netdisk_snapshots')) {
        const row = {
          id: `s${state.snapshots.length + 1}`,
          user_id: params[0], root_path: params[1], source: params[2],
          file_count: params[3], dir_count: params[4], total_bytes: params[5], status: 'ready', last_error: '', created_at: new Date().toISOString(),
        };
        state.snapshots.push(row);
        return { rows: [row], rowCount: 1 };
      }
      if (sql.includes('INSERT INTO netdisk_nodes')) {
        for (let index = 0; index < params.length; index += 14) {
          const node = {
            user_id: params[index], snapshot_id: params[index + 1], fs_id: params[index + 2],
            path: params[index + 3], parent_path: params[index + 4], name: params[index + 5],
            is_dir: params[index + 6], kind: params[index + 7], size_bytes: params[index + 8],
            server_mtime: params[index + 9], category: params[index + 10], local_state: params[index + 11],
            local_size_bytes: params[index + 12], extra: params[index + 13],
          };
          const existing = state.nodes.findIndex((item) => item.user_id === node.user_id && item.path === node.path);
          if (existing >= 0) state.nodes[existing] = node;
          else state.nodes.push(node);
        }
        return { rows: [], rowCount: params.length / 14 };
      }
      if (sql.startsWith('DELETE FROM netdisk_nodes')) {
        const [userId, root, prefix, keep] = params;
        // SQL 里 prefix 是 LIKE 模式（结尾 % 表示任意后缀），这里还原成前缀匹配
        const literalPrefix = String(prefix).replace(/%$/, '');
        const before = state.nodes.length;
        state.nodes = state.nodes.filter((item) => !(item.user_id === userId && (item.path === root || item.path.startsWith(literalPrefix)) && !keep.includes(item.path)));
        return { rows: [], rowCount: before - state.nodes.length };
      }
      if (sql.includes('FROM netdisk_nodes') && sql.includes('parent_path=$2')) {
        return { rows: state.nodes.filter((item) => item.user_id === params[0] && item.parent_path === params[1]), rowCount: 1 };
      }
      if (sql.includes('FROM netdisk_snapshots')) return { rows: state.snapshots.slice(-1), rowCount: 1 };
      return { rows: [], rowCount: 0 };
    },
    async connect() {
      return {
        query: pool.query,
        async release() {},
      };
    },
  };
  return pool;
}

const fixtureCatalog = () => ({
  root: '/AIGC',
  source: 'mcp',
  generatedAt: new Date().toISOString(),
  fileCount: 2,
  dirCount: 2,
  totalBytes: 300,
  nodes: [
    { path: '/AIGC', name: 'AIGC', parentPath: '/AIGC', isDir: true, sizeBytes: 0, serverMtime: 1791787162, fsId: 111, category: 6, kind: 'directory' },
    { path: '/AIGC/1007视频', name: '1007视频', parentPath: '/AIGC', isDir: true, sizeBytes: 0, serverMtime: 1791787162, fsId: 222, category: 6, kind: 'directory' },
    { path: '/AIGC/1007视频/成片', name: '成片', parentPath: '/AIGC/1007视频', isDir: true, sizeBytes: 0, serverMtime: 1791787000, fsId: 333, category: 6, kind: 'directory' },
    { path: '/AIGC/1007视频/成片/final.mp4', name: 'final.mp4', parentPath: '/AIGC/1007视频/成片', isDir: false, sizeBytes: 200, serverMtime: 1791786900, fsId: 444, category: 1, kind: 'video' },
    { path: '/AIGC/1007视频/成片/封面.png', name: '封面.png', parentPath: '/AIGC/1007视频/成片', isDir: false, sizeBytes: 100, serverMtime: 1791786900, fsId: 555, category: 2, kind: 'image' },
  ],
});

async function main() {
  results.push('路径白名单');
  await test('接受 /AIGC 及其子路径', async () => {
    assert.strictEqual(assertNetdiskPath('/AIGC', ROOT), '/AIGC');
    assert.strictEqual(assertNetdiskPath('/AIGC/1007视频/成片/final.mp4', ROOT), '/AIGC/1007视频/成片/final.mp4');
    assert.strictEqual(assertNetdiskPath('AIGC//1007视频/', ROOT), '/AIGC/1007视频');
  });
  await test('拒绝 /AIGC 以外的路径', async () => {
    for (const bad of ['/', '/其他目录', '/AIGC2/文件', '/Pictures', '/aigc/x']) {
      assert.throws(() => assertNetdiskPath(bad, ROOT), (error) => error.statusCode === 400, `应拒绝 ${bad}`);
    }
  });
  await test('拒绝 ../ 逃逸', async () => {
    assert.throws(() => assertNetdiskPath('/AIGC/../secret', ROOT), (error) => error.statusCode === 400);
    assert.throws(() => assertNetdiskPath('/AIGC/..', ROOT), (error) => error.statusCode === 400);
  });
  await test('拒绝 netdisk 根路径', async () => {
    assert.strictEqual(normaliseNodePath(''), '/');
    assert.throws(() => assertNetdiskPath('/', ROOT), (error) => error.statusCode === 400);
  });

  results.push('请求体安全');
  await test('拒绝 access_token / dlink / cookie 字段', async () => {
    assert.throws(() => assertNoFileContent({ access_token: 'x' }), /拒绝字段/);
    assert.throws(() => assertNoFileContent({ nodes: [{ path: '/AIGC/a', dlink: 'https://d.pcs.baidu.com/file?sign=x' }] }), /拒绝字段/);
    assert.throws(() => assertNoFileContent({ cookie: 'aigc_session=abc' }), /拒绝字段/);
    assert.throws(() => assertNoFileContent({ nodes: [{ path: '/AIGC/a', extra: { refresh_token: 'r' } }] }), /拒绝字段/);
    assert.throws(() => assertNoFileContent({ nodes: [{ path: '/AIGC/a', Access_Token: 'x' }] }), /拒绝字段/);
  });
  await test('拒绝文件内容载荷', async () => {
    const base64 = Buffer.alloc(4096, 7).toString('base64');
    assert.throws(() => assertNoFileContent({ nodes: [{ path: '/AIGC/a', content: base64 }] }), /拒绝字段/);
    assert.throws(() => assertNoFileContent({ nodes: [{ path: '/AIGC/a', data: 'data:video/mp4;base64,AAAA' }] }), /拒绝字段/);
    assert.throws(() => assertNoFileContent({ nodes: [{ path: '/AIGC/a', blob: 'x'.repeat(5000) }] }), /拒绝字段/);
    assert.throws(() => assertNoFileContent({ nodes: [{ path: '/AIGC/a', content: Buffer.alloc(2048, 1) }] }), /拒绝字段/);
  });
  await test('正常目录元数据不被误判', async () => {
    assertNoFileContent(fixtureCatalog());
  });

  results.push('目录解析');
  await test('解析出正确的父子关系，根节点不列为自己子节点', async () => {
    const { nodes } = parseCatalogBody(fixtureCatalog(), ROOT);
    const root = nodes.find((node) => node.path === '/AIGC');
    assert.strictEqual(root.parentPath, '');
    assert.strictEqual(root.kind, 'directory');
    const file = nodes.find((node) => node.path.endsWith('final.mp4'));
    assert.strictEqual(file.parentPath, '/AIGC/1007视频/成片');
    assert.strictEqual(file.sizeBytes, 200);
    assert.ok(file.serverMtime instanceof Date);
  });
  await test('拒绝非 /AIGC 的快照根', async () => {
    assert.throws(() => parseCatalogBody({ ...fixtureCatalog(), root: '/Pictures' }, ROOT), /只接受/);
  });
  await test('拒绝缺少 nodes 的请求体', async () => {
    assert.throws(() => parseCatalogBody({ hello: 1 }, ROOT), /nodes/);
    assert.throws(() => parseCatalogBody(null, ROOT), /JSON/);
  });
  await test('拒绝超大节点数量', async () => {
    const many = { root: '/AIGC', nodes: Array.from({ length: 20001 }, (_, i) => ({ path: `/AIGC/f${i}`, name: `f${i}`, isDir: false, kind: 'other' })) };
    assert.throws(() => parseCatalogBody(many, ROOT), /最多提交/);
  });
  await test('节点的路径同样受白名单约束', async () => {
    const bad = { root: '/AIGC', nodes: [{ path: '/其他目录/秘密.mp4', name: '秘密.mp4', isDir: false }] };
    assert.throws(() => parseCatalogBody(bad, ROOT), /不在允许的网盘根目录/);
  });

  results.push('快照写入');
  await test('发布后节点入库，根目录子节点可查询', async () => {
    const pool = makePool();
    const result = await applyCatalog({ pool, userId: USER, body: fixtureCatalog(), rootPath: ROOT, source: 'mcp' });
    assert.strictEqual(result.fileCount, 2);
    assert.strictEqual(result.dirCount, 3);
    assert.strictEqual(result.totalBytes, 300);
    const children = await pool.query('SELECT * FROM netdisk_nodes WHERE user_id=$1 AND parent_path=$2', [USER, '/AIGC']);
    assert.deepStrictEqual(children.rows.map((row) => row.name).sort(), ['1007视频']);
  });
  await test('重复发布更新而不是堆积新行', async () => {
    const pool = makePool();
    await applyCatalog({ pool, userId: USER, body: fixtureCatalog(), rootPath: ROOT, source: 'mcp' });
    await applyCatalog({ pool, userId: USER, body: fixtureCatalog(), rootPath: ROOT, source: 'mcp' });
    assert.strictEqual(pool.state.nodes.length, 5);
    assert.strictEqual(pool.state.snapshots.length, 2);
  });
  await test('完整快照里消失的节点会被删除', async () => {
    const pool = makePool();
    const full = { ...fixtureCatalog(), complete: true };
    await applyCatalog({ pool, userId: USER, body: full, rootPath: ROOT, source: 'mcp' });
    const reduced = { ...fixtureCatalog(), complete: true, nodes: fixtureCatalog().nodes.filter((node) => !node.path.endsWith('封面.png')) };
    const result = await applyCatalog({ pool, userId: USER, body: reduced, rootPath: ROOT, source: 'mcp' });
    assert.strictEqual(result.removedNodes, 1);
    assert.ok(!pool.state.nodes.some((node) => node.path.endsWith('封面.png')));
  });
  await test('截断快照不删除未列出的节点', async () => {
    const pool = makePool();
    await applyCatalog({ pool, userId: USER, body: { ...fixtureCatalog(), complete: true }, rootPath: ROOT, source: 'mcp' });
    const partial = fixtureCatalog();               // 没有 complete 标记 = 截断/子目录快照
    partial.nodes = partial.nodes.filter((node) => node.path.endsWith('成片'));
    const result = await applyCatalog({ pool, userId: USER, body: partial, rootPath: ROOT, source: 'mcp' });
    assert.strictEqual(result.removedNodes, 0, '截断快照不得清理其它层级');
    assert.ok(pool.state.nodes.some((node) => node.path.endsWith('封面.png')), '未列出的节点必须保留');
  });
  await test('空的 complete 快照不会删光目录', async () => {
    const pool = makePool();
    await applyCatalog({ pool, userId: USER, body: { ...fixtureCatalog(), complete: true }, rootPath: ROOT, source: 'mcp' });
    const result = await applyCatalog({ pool, userId: USER, body: { ...fixtureCatalog(), complete: true, nodes: [] }, rootPath: ROOT, source: 'mcp' });
    assert.strictEqual(result.removedNodes, 0);
    assert.strictEqual(pool.state.nodes.length, 5);
  });
  await test('LIKE 前缀会转义 % 和 _', async () => {
    const { likePrefix } = require('../src/netdisk-catalog');
    assert.strictEqual(likePrefix('/AIGC'), '/AIGC/%');
    assert.strictEqual(likePrefix('/AIGC/100_7'), '/AIGC/100\\_7/%');
    assert.strictEqual(likePrefix('/AIGC/50%'), '/AIGC/50\\%/%');
  });
  await test('fs_id 超过 2^53 时不丢精度', async () => {
    const big = { ...fixtureCatalog(), nodes: [{ path: '/AIGC/big.mp4', name: 'big.mp4', isDir: false, kind: 'video', sizeBytes: 1, fsId: '9007199254740993' }] };
    const { nodes } = parseCatalogBody(big, ROOT);
    assert.strictEqual(nodes[0].fsId, '9007199254740993');
    assert.strictEqual(String(Number(nodes[0].fsId)), '9007199254740992', '确认该值确实超出 JS 安全整数范围');
  });
  await test('写入失败时事务回滚', async () => {
    const pool = makePool();
    const broken = { ...pool, connect: async () => ({ query: async (sql) => { if (String(sql).includes('netdisk_nodes')) throw new Error('boom'); return pool.query(sql); }, release() {} }) };
    await assert.rejects(() => applyCatalog({ pool: broken, userId: USER, body: fixtureCatalog(), rootPath: ROOT, source: 'mcp' }), /boom/);
    assert.strictEqual(pool.state.nodes.length, 0);
  });
  await test('用户之间互不影响', async () => {
    const pool = makePool();
    const other = '99999999-9999-4999-8999-999999999999';
    await applyCatalog({ pool, userId: USER, body: fixtureCatalog(), rootPath: ROOT, source: 'mcp' });
    await applyCatalog({ pool, userId: other, body: fixtureCatalog(), rootPath: ROOT, source: 'mcp' });
    assert.strictEqual(pool.state.nodes.filter((node) => node.user_id === USER).length, 5);
    assert.strictEqual(pool.state.nodes.filter((node) => node.user_id === other).length, 5);
  });

  results.push('本机降级导入');
  const workspace = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'netdisk-check-'));
  await test('只允许导入白名单文件', async () => {
    const pool = makePool();
    await fs.promises.mkdir(path.join(workspace, 'outputs'), { recursive: true });
    await fs.promises.writeFile(path.join(workspace, 'outputs', 'netdisk-catalog.json'), JSON.stringify(fixtureCatalog()), 'utf8');
    const result = await readWhitelistedCatalog({ pool, userId: USER, rootPath: ROOT, repoRoot: workspace });
    assert.strictEqual(result.fileCount, 2);
    await assert.rejects(
      () => readWhitelistedCatalog({ pool, userId: USER, rootPath: ROOT, requestedFile: 'outputs/../../etc/passwd', repoRoot: workspace }),
      (error) => error.statusCode === 403,
    );
    await assert.rejects(
      () => readWhitelistedCatalog({ pool, userId: USER, rootPath: ROOT, requestedFile: 'package.json', repoRoot: workspace }),
      (error) => error.statusCode === 403,
    );
  });
  await test('白名单文件不存在时返回明确错误', async () => {
    const empty = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'netdisk-empty-'));
    await assert.rejects(
      () => readWhitelistedCatalog({ pool: makePool(), userId: USER, rootPath: ROOT, repoRoot: empty }),
      (error) => error.statusCode === 404,
    );
    await fs.promises.rm(empty, { recursive: true, force: true });
  });

  await fs.promises.rm(workspace, { recursive: true, force: true });

  results.push('响应与配置');
  await test('节点响应不含任何凭据字段', async () => {
    const serialised = JSON.stringify(publicNode({
      path: '/AIGC/a.mp4', name: 'a.mp4', parent_path: '/AIGC', is_dir: false, kind: 'video',
      size_bytes: 10, server_mtime: new Date(), fs_id: 1, category: 1, local_state: 'unknown', local_size_bytes: 0,
    }));
    for (const forbidden of ['access_token', 'dlink', 'cookie', 'session_secret']) {
      assert.ok(!serialised.toLowerCase().includes(forbidden), forbidden);
    }
  });
  await test('默认根目录是 /AIGC 且可配置', async () => {
    assert.strictEqual(loadNetdiskConfig({}).rootPath, '/AIGC');
    assert.strictEqual(loadNetdiskConfig({ NETDISK_ROOT_PATH: '/Backup' }).rootPath, '/Backup');
    assert.strictEqual(loadNetdiskConfig({ NETDISK_ROOT_PATH: '/AIGC/' }).rootPath, '/AIGC');
  });

  console.log(results.join('\n'));
  console.log(`\n${failures ? `FAILED: ${failures} 项` : 'ALL PASSED'}`);
  process.exit(failures ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
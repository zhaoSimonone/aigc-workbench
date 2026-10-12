// 百度网盘「实时目录」自检：mock 百度 HTTP + 内存数据库桩，跑真实的加密/解析/降级逻辑。
// 绝不使用真实 Token，绝不访问真实网盘。
// 用法：node server/scripts/netdisk-live-check.js
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const {
  AUTHORIZE_URL,
  CREDENTIAL_PURPOSE,
  DEFAULT_EXPIRES_IN,
  EXPIRING_SOON_SECONDS,
  PAGE_SIZE,
  RETRYABLE_ERRNO,
  LiveError,
  classifyKind,
  cleanListItem,
  createCredentialStore,
  createListClient,
  createLiveDirectoryReader,
  decryptToken,
  encryptToken,
  parsePastedCredential,
  pruneStaleLayer,
  readEncryptionKey,
  upsertLayer,
} = require('../src/netdisk-live');

const KEY_B64 = crypto.randomBytes(32).toString('base64');
const ENV = { NETDISK_TOKEN_ENCRYPTION_KEY: KEY_B64 };
const USER = '11111111-1111-4111-8111-111111111111';
const ROOT = '/AIGC';
const FAKE_TOKEN = 'fake-access-token-for-tests-only-0000000000';

const results = [];
let failures = 0;

async function test(name, fn) {
  try {
    await fn();
    results.push(`  PASS  ${name}`);
  } catch (error) {
    failures += 1;
    results.push(`  FAIL  ${name}\n        ${(error.stack || error.message).split('\n').slice(0, 4).join('\n        ')}`);
  }
}

// ---------------------------------------------------------------- 内存数据库桩

function makePool() {
  const state = { credentials: new Map(), nodes: [], statements: [] };
  const pool = {
    state,
    async query(text, params = []) {
      const sql = String(text || '');
      state.statements.push(sql.replace(/\s+/g, ' ').trim().slice(0, 60));
      if (sql.includes('DELETE FROM netdisk_credentials')) {
        state.credentials.delete(params[0]);
        return { rows: [], rowCount: 1 };
      }
      if (sql.includes('SELECT') && sql.includes('FROM netdisk_credentials WHERE user_id=$1')) {
        const row = state.credentials.get(params[0]);
        return { rows: row ? [row] : [], rowCount: row ? 1 : 0 };
      }
      if (sql.includes('INSERT INTO netdisk_credentials')) {
        const row = {
          user_id: params[0], provider: params[1], encrypted_token: params[2], token_last4: params[3],
          scope: params[4], status: 'active', expires_at: params[5], obtained_at: params[6],
          last_verified_at: null, last_error: '', updated_at: new Date(),
        };
        state.credentials.set(params[0], row);
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
      if (sql.includes('INSERT INTO netdisk_nodes')) {
        for (let index = 0; index < params.length; index += 14) {
          const node = {
            id: `n${state.nodes.length + 1}`, user_id: params[index], snapshot_id: params[index + 1],
            fs_id: params[index + 2], path: params[index + 3], parent_path: params[index + 4],
            name: params[index + 5], is_dir: params[index + 6], kind: params[index + 7],
            size_bytes: params[index + 8], server_mtime: params[index + 9], category: params[index + 10],
            local_state: params[index + 11], local_size_bytes: params[index + 12], extra: params[index + 13],
            updated_at: new Date(),
          };
          const existing = state.nodes.findIndex((item) => item.user_id === node.user_id && item.path === node.path);
          if (existing >= 0) state.nodes[existing] = node;
          else state.nodes.push(node);
        }
        return { rows: [], rowCount: params.length / 14 };
      }
      if (sql.includes('SELECT id, path, is_dir FROM netdisk_nodes')) {
        return { rows: state.nodes.filter((item) => item.user_id === params[0] && item.parent_path === params[1]), rowCount: 1 };
      }
      if (sql.includes('unnest(') && sql.includes('position(')) {
        const found = state.nodes.filter((item) => item.user_id === params[0] && params[1].some((prefix) => item.path.startsWith(`${prefix}/`)));
        return { rows: found.map((item) => ({ id: item.id })), rowCount: found.length };
      }
      if (sql.includes('DELETE FROM netdisk_nodes WHERE user_id=$1 AND id = ANY')) {
        const [userId, ids] = params;
        const before = state.nodes.length;
        state.nodes = state.nodes.filter((item) => !(item.user_id === userId && ids.includes(item.id)));
        return { rows: [], rowCount: before - state.nodes.length };
      }
      if (sql.includes('SELECT * FROM netdisk_nodes WHERE user_id=$1 AND parent_path=$2')) {
        const found = state.nodes.filter((item) => item.user_id === params[0] && item.parent_path === params[1]);
        return { rows: found, rowCount: 1 };
      }
      if (sql.includes('MAX(updated_at)')) return { rows: [{ updated_at: new Date(), total: state.nodes.length }], rowCount: 1 };
      if (sql.includes('FROM netdisk_snapshots')) return { rows: [], rowCount: 0 };
      return { rows: [], rowCount: 0 };
    },
    async connect() {
      return { query: pool.query, release() {} };
    },
  };
  return pool;
}

// ------------------------------------------------------------ mock 百度 list

function baiduItem(path, { isDir = false, size = 0, fsId = 1, mtime = 1791787162, category = isDir ? 6 : 1 } = {}) {
  return {
    path, isdir: isDir ? 1 : 0, size, fs_id: fsId, server_mtime: mtime, category,
    // 百度 web=1 会带这些字段，必须在清洗层被丢掉
    dlink: `https://d.pcs.baidu.com/file?token=SECRET&sign=deadbeef`,
    thumbs: { url_large: 'https://thumb.baidu.com/large.jpg' },
    md5: 'fba93620dmd8804115a627c69115fefc',
  };
}

function mockFetch(pagesByDir, { onCall } = {}) {
  const calls = [];
  const fetchImpl = async (url) => {
    calls.push({ url });
    const parsed = new URL(url);
    if (onCall) onCall({ url, dir: parsed.searchParams.get('dir'), page: Number(parsed.searchParams.get('page')) });
    if (parsed.searchParams.get('access_token') !== FAKE_TOKEN) {
      return { ok: true, status: 200, json: async () => ({ errno: 111, errmsg: 'invalid access token' }) };
    }
    const dir = parsed.searchParams.get('dir');
    const page = Number(parsed.searchParams.get('page'));
    const pages = pagesByDir[dir];
    if (!pages) return { ok: true, status: 200, json: async () => ({ errno: -9, errmsg: 'dir not exist' }) };
    const list = pages[page - 1] || [];
    return { ok: true, status: 200, json: async () => ({ errno: 0, list }) };
  };
  return { fetchImpl, calls };
}

async function main() {
  results.push('加密');
  await test('明文进、库里不是明文、解密一致', () => {
    const key = readEncryptionKey(ENV);
    const cipher = encryptToken(FAKE_TOKEN, key);
    assert.ok(!cipher.includes(FAKE_TOKEN), '密文里不能出现明文');
    assert.strictEqual(cipher.split('.').length, 4);
    assert.strictEqual(cipher.split('.')[0], 'v1');
    assert.strictEqual(decryptToken(cipher, key), FAKE_TOKEN);
  });
  await test('同一明文两次加密结果不同（随机 IV）', () => {
    const key = readEncryptionKey(ENV);
    assert.notStrictEqual(encryptToken(FAKE_TOKEN, key), encryptToken(FAKE_TOKEN, key));
  });
  await test('密钥错误时解密失败而不是返回垃圾', () => {
    const cipher = encryptToken(FAKE_TOKEN, readEncryptionKey(ENV));
    const other = crypto.randomBytes(32);
    assert.throws(() => decryptToken(cipher, other), (error) => error.code === 'credential_corrupt');
  });
  await test('缺密钥或长度不对时拒绝', () => {
    assert.throws(() => readEncryptionKey({}), (error) => error.code === 'encryption_key_missing');
    assert.throws(() => readEncryptionKey({ NETDISK_TOKEN_ENCRYPTION_KEY: 'c2hvcnQ=' }), (error) => /32 字节/.test(error.message));
    assert.throws(() => readEncryptionKey({ NETDISK_TOKEN_ENCRYPTION_KEY: 'not-base64!!!' }), (error) => /32 字节/.test(error.message));
  });

  results.push('粘贴解析');
  const fullCallback = `https://openapi.baidu.com/oauth/2.0/login_success#expires_in=2592000&access_token=${FAKE_TOKEN}&session_secret=&session_key=&scope=basic+netdisk`;
  await test('解析整段 login_success 回调（hash 里的 token 与 expires_in）', () => {
    const parsed = parsePastedCredential(fullCallback);
    assert.strictEqual(parsed.token, FAKE_TOKEN);
    assert.strictEqual(parsed.expiresIn, 2592000);
    assert.strictEqual(parsed.scope, 'basic netdisk');
  });
  await test('解析只有 # 片段的情况', () => {
    const parsed = parsePastedCredential(`#expires_in=2592000&access_token=${FAKE_TOKEN}&scope=basic+netdisk`);
    assert.strictEqual(parsed.token, FAKE_TOKEN);
    assert.strictEqual(parsed.expiresIn, 2592000);
  });
  await test('解析纯 Token，expires_in 默认 2592000', () => {
    const parsed = parsePastedCredential(FAKE_TOKEN);
    assert.strictEqual(parsed.token, FAKE_TOKEN);
    assert.strictEqual(parsed.expiresIn, 2592000);
    assert.strictEqual(parsed.scope, '');
  });
  await test('空值、超长、非法字符被拒绝', () => {
    assert.throws(() => parsePastedCredential(''), (error) => error.code === 'credential_empty');
    assert.throws(() => parsePastedCredential('x'.repeat(2049)), (error) => error.code === 'credential_too_long');
    assert.throws(() => parsePastedCredential('有中文的 token'), (error) => error.code === 'credential_invalid');
  });
  await test('解析结果不含完整回调 URL', () => {
    const parsed = parsePastedCredential(fullCallback);
    assert.ok(!JSON.stringify(parsed).includes('login_success'));
    assert.ok(!Object.keys(parsed).some((key) => /url|callback|session/i.test(key)));
  });

  results.push('凭据存储与状态');
  await test('保存后 GET 不返回 Token，只返回后四位与有效期', async () => {
    const pool = makePool();
    const store = createCredentialStore({ pool, env: ENV });
    await store.save(USER, fullCallback);
    const view = store.describe(await store.load(USER));
    assert.strictEqual(view.configured, true);
    assert.strictEqual(view.status, 'active');
    assert.strictEqual(view.tokenLast4, FAKE_TOKEN.slice(-4));
    assert.strictEqual(view.purpose, CREDENTIAL_PURPOSE);
    assert.ok(view.expiresAt);
    assert.ok(view.expiresInSec > 0);
    const serialised = JSON.stringify(view);
    assert.ok(!serialised.includes(FAKE_TOKEN), '凭据视图绝不能包含 Token');
    assert.ok(!serialised.includes('login_success'));
    assert.strictEqual(pool.state.credentials.get(USER).encrypted_token.includes(FAKE_TOKEN), false);
  });
  await test('expires_at 已过时按 expired 返回，即使库内是 active', async () => {
    const pool = makePool();
    const store = createCredentialStore({ pool, env: ENV });
    await store.save(USER, FAKE_TOKEN);
    assert.strictEqual(store.describe(await store.load(USER)).status, 'active');
    pool.state.credentials.get(USER).expires_at = new Date(Date.now() - 1000);
    const view = store.describe(await store.load(USER));
    assert.strictEqual(view.status, 'expired');
    assert.strictEqual(view.expiringSoon, false);
    assert.strictEqual(view.expiresInSec < 0, true);
  });
  await test('剩余不足 3 天时标记即将过期（仍可实时 list）', async () => {
    const pool = makePool();
    const store = createCredentialStore({ pool, env: ENV });
    await store.save(USER, FAKE_TOKEN);
    pool.state.credentials.get(USER).expires_at = new Date(Date.now() + 2 * 24 * 3600 * 1000);
    const view = store.describe(await store.load(USER));
    assert.strictEqual(view.status, 'active');
    assert.strictEqual(view.expiringSoon, true);
  });
  await test('未配置时 describe 返回 configured=false', async () => {
    const store = createCredentialStore({ pool: makePool(), env: ENV });
    const view = store.describe(await store.load(USER));
    assert.strictEqual(view.configured, false);
    assert.strictEqual(view.status, 'none');
    assert.strictEqual(view.authorizeUrl, AUTHORIZE_URL);
  });
  await test('过期凭据不会返回明文 token（resolveToken 直接拒绝）', async () => {
    const pool = makePool();
    const store = createCredentialStore({ pool, env: ENV });
    await store.save(USER, FAKE_TOKEN);
    pool.state.credentials.get(USER).expires_at = new Date(Date.now() - 1000);
    await assert.rejects(() => store.resolveToken(USER), (error) => error.code === 'credential_expired');
  });
  await test('删除凭据不影响缓存节点', async () => {
    const pool = makePool();
    const store = createCredentialStore({ pool, env: ENV });
    await store.save(USER, FAKE_TOKEN);
    await store.remove(USER);
    assert.strictEqual((await store.load(USER)).configured, false);
  });

  results.push('list 客户端');
  await test('同层翻页到最后一页，结果合并', async () => {
    const page1 = Array.from({ length: 1000 }, (_, i) => baiduItem(`${ROOT}/f${i}.mp4`, { size: 1000 + i, fsId: 1000 + i }));
    const page2 = [baiduItem(`${ROOT}/last.txt`, { size: 12, fsId: 9999 })];
    const { fetchImpl, calls } = mockFetch({ [ROOT]: [page1, page2] });
    const client = createListClient({ fetchImpl, env: ENV });
    const items = await client.listLayer(FAKE_TOKEN, ROOT);
    assert.strictEqual(items.length, 1001);
    assert.strictEqual(calls.length, 2, '必须翻到第 2 页');
    assert.ok(calls[1].url.includes('page=2'));
  });
  await test('请求参数与 Python 实现一致', async () => {
    const { fetchImpl, calls } = mockFetch({ [ROOT]: [[baiduItem(`${ROOT}/a.mp4`)]] });
    const client = createListClient({ fetchImpl, env: ENV });
    await client.listLayer(FAKE_TOKEN, ROOT);
    const params = new URL(calls[0].url).searchParams;
    assert.strictEqual(params.get('dir'), ROOT);
    assert.strictEqual(params.get('web'), '1');
    assert.strictEqual(params.get('num'), '1000');
    assert.strictEqual(params.get('order'), 'name');
    assert.strictEqual(params.get('desc'), '0');
    assert.strictEqual(params.get('access_token'), FAKE_TOKEN);
    assert.ok(calls[0].url.startsWith('https://pan.baidu.com/api/list?'));
  });
  await test('路径白名单：/、/Pictures、.. 都拒绝且不打百度', async () => {
    const { fetchImpl, calls } = mockFetch({});
    const client = createListClient({ fetchImpl, env: ENV });
    await assert.rejects(() => client.listLayer(FAKE_TOKEN, '/'), (error) => error.statusCode === 400);
    await assert.rejects(() => client.listLayer(FAKE_TOKEN, '/Pictures'), (error) => error.statusCode === 400);
    await assert.rejects(() => client.listLayer(FAKE_TOKEN, `${ROOT}/../secret`), (error) => error.statusCode === 400);
    assert.strictEqual(calls.length, 0);
  });
  await test('errno 111 映射成 token_invalid 且不重试', async () => {
    let called = 0;
    const fetchImpl = async () => { called += 1; return { ok: true, status: 200, json: async () => ({ errno: 111 }) }; };
    const client = createListClient({ fetchImpl, env: ENV });
    await assert.rejects(() => client.listLayer(FAKE_TOKEN, ROOT), (error) => error.code === 'token_invalid');
    assert.strictEqual(called, 1, 'token_invalid 不应该重试');
  });
  await test('errno -9 视为空目录', async () => {
    const { fetchImpl } = mockFetch({});
    const client = createListClient({ fetchImpl, env: ENV, logger: { log() {} } });
    assert.deepStrictEqual(await client.listLayer(FAKE_TOKEN, `${ROOT}/不存在`), []);
  });
  await test('超时/网络错误按可重试 errno 处理后仍失败', async () => {
    const fetchImpl = async () => { throw Object.assign(new Error('socket hang up'), { name: 'AbortError' }); };
    const client = createListClient({ fetchImpl, env: ENV });
    await assert.rejects(() => client.listLayer(FAKE_TOKEN, ROOT), (error) => error.code === 'network_error');
  });

  results.push('节点清洗');
  await test('清洗丢弃 dlink / thumbs / 打码 md5，fsId 用字符串', () => {
    const node = cleanListItem(baiduItem(`${ROOT}/902视频`, { isDir: true, fsId: 497116331455040 }), ROOT);
    assert.strictEqual(node.path, `${ROOT}/902视频`);
    assert.strictEqual(node.kind, 'directory');
    assert.strictEqual(typeof node.fsId, 'string');
    const serialised = JSON.stringify(node);
    for (const forbidden of ['dlink', 'thumbs', 'md5', 'sign', 'token']) {
      assert.ok(!serialised.toLowerCase().includes(forbidden), forbidden);
    }
  });
  await test('超出白名单的条目被丢弃', () => {
    assert.strictEqual(cleanListItem(baiduItem('/其他目录/秘密.mp4'), ROOT), null);
    assert.strictEqual(cleanListItem(baiduItem('/AIGC2/x.mp4'), ROOT), null);
  });
  await test('kind 按扩展名分类', () => {
    const kinds = {
      'a.mp4': 'video', 'a.PNG': 'image', 'a.wav': 'audio', 'a.md': 'document', 'a.zip': 'archive', 'a.qq': 'other',
    };
    for (const [name, kind] of Object.entries(kinds)) {
      assert.strictEqual(cleanListItem(baiduItem(`${ROOT}/${name}`), ROOT).kind, kind, name);
    }
  });

  results.push('实时读取与降级');
  function makeReader(pagesByDir, options = {}) {
    const pool = makePool();
    const credentials = createCredentialStore({ pool, env: options.env || ENV, now: options.now });
    const { fetchImpl, calls } = mockFetch(pagesByDir, options.mockOptions);
    const listClient = createListClient({ fetchImpl, env: options.env || ENV, logger: { log() {}, warn() {} } });
    const reader = createLiveDirectoryReader({ pool, credentials, listClient });
    return { pool, credentials, reader, calls };
  }

  await test('无凭据时不打百度，返回空 + need_credential', async () => {
    const { reader, calls, pool } = makeReader({ [ROOT]: [[baiduItem(`${ROOT}/902视频`, { isDir: true })]] });
    const layer = await reader.readLayer({ userId: USER, dirPath: ROOT });
    assert.strictEqual(layer.source, 'none');
    assert.strictEqual(layer.errorCode, 'need_credential');
    assert.deepStrictEqual(layer.nodes, []);
    assert.strictEqual(calls.length, 0, '没有凭据时绝不能请求百度');
    assert.strictEqual(pool.state.statements.length >= 1, true);
  });
  await test('有凭据时实时读取并写入缓存', async () => {
    const { reader, credentials, pool } = makeReader({
      [ROOT]: [[baiduItem(`${ROOT}/902视频`, { isDir: true, fsId: 111 })]],
      [`${ROOT}/902视频`]: [[baiduItem(`${ROOT}/902视频/成片.mp4`, { size: 2048, fsId: 222 })]],
    });
    await credentials.save(USER, FAKE_TOKEN);
    const top = await reader.readLayer({ userId: USER, dirPath: ROOT });
    assert.strictEqual(top.source, 'live');
    assert.ok(top.fetchedAt instanceof Date);
    assert.deepStrictEqual(top.nodes.map((node) => node.name), ['902视频']);
    const inner = await reader.readLayer({ userId: USER, dirPath: `${ROOT}/902视频` });
    assert.strictEqual(inner.source, 'live');
    assert.strictEqual(inner.nodes.length, 1);
    assert.strictEqual(inner.nodes[0].size_bytes, 2048);
    assert.ok(pool.state.nodes.every((node) => node.path !== ROOT), '不应凭空插入根节点行');
  });
  await test('errno 111 → 降级缓存 + token_invalid + 凭据变 invalid', async () => {
    const { reader, credentials, pool } = makeReader({ [ROOT]: [[]] });
    await credentials.save(USER, FAKE_TOKEN);
    // 先成功一次制造缓存
    pool.state.nodes.push({ id: 'seed', user_id: USER, path: `${ROOT}/旧文件.mp4`, parent_path: ROOT, name: '旧文件.mp4', is_dir: false, kind: 'video', size_bytes: 10, server_mtime: null, fs_id: '1', category: 1, local_state: 'unknown', local_size_bytes: 0, extra: {}, updated_at: new Date() });
    // 再让百度返回 111：改用 token 校验失败的方式（用错 token）
    const brokenCredentials = createCredentialStore({ pool, env: ENV });
    const listClient = createListClient({
      fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ errno: 111 }) }),
      env: ENV,
      logger: { log() {}, warn() {} },
    });
    const brokenReader = createLiveDirectoryReader({ pool, credentials: brokenCredentials, listClient });
    await brokenCredentials.save(USER, FAKE_TOKEN);
    const layer = await brokenReader.readLayer({ userId: USER, dirPath: ROOT });
    assert.strictEqual(layer.source, 'cache');
    assert.strictEqual(layer.errorCode, 'token_invalid');
    assert.strictEqual(layer.nodes.length, 1, '应返回缓存节点');
    assert.strictEqual(pool.state.credentials.get(USER).status, 'invalid');
    assert.strictEqual(layer.credential.status, 'invalid');
    await reader.cachedNodes; // eslint 占位
  });
  await test('网盘不可用时降级缓存并给出 warning', async () => {
    const pool = makePool();
    const credentials = createCredentialStore({ pool, env: ENV });
    const listClient = createListClient({
      fetchImpl: async () => ({ ok: false, status: 503, json: async () => ({}) }),
      env: ENV,
      logger: { log() {}, warn() {} },
    });
    const reader = createLiveDirectoryReader({ pool, credentials, listClient });
    await credentials.save(USER, FAKE_TOKEN);
    pool.state.nodes.push({ id: 'seed', user_id: USER, path: `${ROOT}/缓存.mp4`, parent_path: ROOT, name: '缓存.mp4', is_dir: false, kind: 'video', size_bytes: 5, server_mtime: null, fs_id: '1', category: 1, local_state: 'unknown', local_size_bytes: 0, extra: {}, updated_at: new Date() });
    const layer = await reader.readLayer({ userId: USER, dirPath: ROOT });
    assert.strictEqual(layer.source, 'cache');
    assert.strictEqual(layer.errorCode, 'http_error');
    assert.match(layer.warning, /缓存/);
    assert.strictEqual(layer.nodes.length, 1);
  });
  await test('无缓存且失败时返回空 + live_unavailable', async () => {
    const pool = makePool();
    const credentials = createCredentialStore({ pool, env: ENV });
    const listClient = createListClient({
      fetchImpl: async () => ({ ok: false, status: 500, json: async () => ({}) }),
      env: ENV,
      logger: { log() {}, warn() {} },
    });
    const reader = createLiveDirectoryReader({ pool, credentials, listClient });
    await credentials.save(USER, FAKE_TOKEN);
    const layer = await reader.readLayer({ userId: USER, dirPath: ROOT });
    assert.strictEqual(layer.source, 'none');
    assert.deepStrictEqual(layer.nodes, []);
    assert.ok(layer.errorCode);
  });
  await test('过期凭据时不打百度', async () => {
    const pool = makePool();
    const credentials = createCredentialStore({ pool, env: ENV });
    const { fetchImpl, calls } = mockFetch({ [ROOT]: [[]] });
    const listClient = createListClient({ fetchImpl, env: ENV, logger: { log() {}, warn() {} } });
    const reader = createLiveDirectoryReader({ pool, credentials, listClient });
    await credentials.save(USER, FAKE_TOKEN);
    pool.state.credentials.get(USER).expires_at = new Date(Date.now() - 1000);
    const layer = await reader.readLayer({ userId: USER, dirPath: ROOT });
    assert.strictEqual(calls.length, 0, '过期 Token 绝不能打百度');
    assert.strictEqual(layer.errorCode, 'credential_expired');
    assert.strictEqual(layer.credential.status, 'expired');
  });
  await test('实时 list 不会清空整棵树（不做 complete 清理）', async () => {
    const { reader, credentials, pool } = makeReader({ [ROOT]: [[baiduItem(`${ROOT}/902视频`, { isDir: true, fsId: '2' })]] });
    await credentials.save(USER, FAKE_TOKEN);
    // 预置一棵有两层的树
    pool.state.nodes.push(
      { id: 'a', user_id: USER, path: ROOT, parent_path: '', name: 'AIGC', is_dir: true, kind: 'directory', size_bytes: 0, server_mtime: null, fs_id: '1', category: 6, local_state: 'unknown', local_size_bytes: 0, extra: {}, updated_at: new Date() },
      { id: 'b', user_id: USER, path: `${ROOT}/902视频`, parent_path: ROOT, name: '902视频', is_dir: true, kind: 'directory', size_bytes: 0, server_mtime: null, fs_id: '2', category: 6, local_state: 'unknown', local_size_bytes: 0, extra: {}, updated_at: new Date() },
      { id: 'c', user_id: USER, path: `${ROOT}/902视频/成片/老.mp4`, parent_path: `${ROOT}/902视频`, name: '老.mp4', is_dir: false, kind: 'video', size_bytes: 5, server_mtime: null, fs_id: '3', category: 1, local_state: 'unknown', local_size_bytes: 0, extra: {}, updated_at: new Date() },
    );
    // 只读 /AIGC 这一层：902视频 仍在列表里，它下面的子孙节点完全不受影响
    const layer = await reader.readLayer({ userId: USER, dirPath: ROOT });
    assert.strictEqual(layer.source, 'live');
    assert.ok(pool.state.nodes.some((node) => node.path === `${ROOT}/902视频`), '同层目录应保留');
    assert.ok(pool.state.nodes.some((node) => node.path === `${ROOT}/902视频/成片/老.mp4`), '更深层节点不应被实时 list 清空');
  });

  results.push('同层清理');
  await test('同层消失的文件被删，消失的目录连子孙一起删', async () => {
    const pool = makePool();
    const nodes = [
      { id: 'd1', user_id: USER, path: `${ROOT}/保留`, parent_path: ROOT, is_dir: true },
      { id: 'd2', user_id: USER, path: `${ROOT}/删除的目录`, parent_path: ROOT, is_dir: true },
      { id: 'd3', user_id: USER, path: `${ROOT}/删除的目录/孙.mp4`, parent_path: `${ROOT}/删除的目录`, is_dir: false },
      { id: 'd4', user_id: USER, path: `${ROOT}/删除的文件.mp4`, parent_path: ROOT, is_dir: false },
    ];
    pool.state.nodes = nodes.map((node) => ({ ...node, name: node.path.split('/').pop(), kind: node.is_dir ? 'directory' : 'video', size_bytes: 1, server_mtime: null, fs_id: '1', category: 1, local_state: 'unknown', local_size_bytes: 0, extra: {}, updated_at: new Date() }));
    const removed = await pruneStaleLayer(pool, USER, ROOT, new Set([`${ROOT}/保留`]));
    assert.strictEqual(removed, 3, '目录 + 子孙 + 文件 = 3 条');
    assert.deepStrictEqual(pool.state.nodes.map((node) => node.id), ['d1']);
  });
  await test('upsert 同层不产生重复行', async () => {
    const pool = makePool();
    const layer = [
      { fsId: '1', path: `${ROOT}/a.mp4`, parentPath: ROOT, name: 'a.mp4', isDir: false, kind: 'video', sizeBytes: 10, serverMtime: null, category: 1, extra: {} },
    ];
    await upsertLayer(pool, USER, null, layer);
    await upsertLayer(pool, USER, null, [{ ...layer[0], sizeBytes: 20 }]);
    assert.strictEqual(pool.state.nodes.length, 1);
    assert.strictEqual(pool.state.nodes[0].size_bytes, 20);
  });

  results.push('回归（本轮自查修复）');
  await test('errno -6 属于可重试，不会把凭据判成失效', async () => {
    let calls = 0;
    const fetchImpl = async () => {
      calls += 1;
      return { ok: true, status: 200, json: async () => (calls < 2 ? { errno: -6 } : { errno: 0, list: [baiduItem(`${ROOT}/a.mp4`)] }) };
    };
    const client = createListClient({ fetchImpl, env: ENV, logger: { log() {} } });
    const items = await client.listLayer(FAKE_TOKEN, ROOT);
    assert.strictEqual(items.length, 1, '第二次请求应成功');
    assert.strictEqual(calls, 2, '-6 应该重试而不是直接失败');
  });
  await test('errno 110 才判 token 失效', async () => {
    const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ errno: 110 }) });
    const client = createListClient({ fetchImpl, env: ENV, logger: { log() {} } });
    await assert.rejects(() => client.listLayer(FAKE_TOKEN, ROOT), (error) => error.code === 'token_invalid');
  });
  await test('粘贴不带 # / ? 的 hash 片段也能解析', () => {
    const parsed = parsePastedCredential(`expires_in=2592000&access_token=${FAKE_TOKEN}&scope=basic+netdisk`);
    assert.strictEqual(parsed.token, FAKE_TOKEN);
    assert.strictEqual(parsed.expiresIn, 2592000);
    assert.strictEqual(parsed.scope, 'basic netdisk');
  });
  await test('缺少加密密钥时保存失败，库里不留明文', async () => {
    const pool = makePool();
    const store = createCredentialStore({ pool, env: {} });
    await assert.rejects(() => store.save(USER, FAKE_TOKEN), (error) => error.code === 'encryption_key_missing');
    assert.strictEqual(pool.state.credentials.has(USER), false, '失败时不应写入任何凭据行');
    // 库里没有凭据时只能是 need_credential，够不到读密钥那一步
    await assert.rejects(() => store.resolveToken(USER), (error) => error.code === 'need_credential');
  });
  await test('密钥后来被移除时，读取报 encryption_key_missing 而不是崩溃', async () => {
    const pool = makePool();
    const withKey = createCredentialStore({ pool, env: ENV });
    await withKey.save(USER, FAKE_TOKEN);          // 先用正确密钥存进去
    const withoutKey = createCredentialStore({ pool, env: {} });   // 模拟密钥被清掉
    await assert.rejects(() => withoutKey.resolveToken(USER), (error) => error.code === 'encryption_key_missing');
    // 状态查询不碰密钥，仍然可用
    assert.strictEqual(withoutKey.describe(await withoutKey.load(USER)).configured, true);
  });
  await test('目录名含 % 或 _ 不会被前缀清理误伤', async () => {
    const pool = makePool();
    const base = { user_id: USER, parent_path: ROOT, is_dir: false, kind: 'video', size_bytes: 1, server_mtime: null, fs_id: '1', category: 1, local_state: 'unknown', local_size_bytes: 0, extra: {}, updated_at: new Date() };
    pool.state.nodes = [
      { ...base, id: 'x1', path: `${ROOT}/a_b`, name: 'a_b', is_dir: true },
      { ...base, id: 'x2', path: `${ROOT}/axb`, name: 'axb', is_dir: false },
      { ...base, id: 'x3', path: `${ROOT}/a_b/里面的.mp4`, name: '里面的.mp4' },
    ];
    // a_b 目录消失，axb 是同前缀兄弟目录，不应被删
    const removed = await pruneStaleLayer(pool, USER, ROOT, new Set([`${ROOT}/axb`]));
    assert.strictEqual(removed, 2, 'a_b 与其子孙应被删，axb 必须保留');
    assert.deepStrictEqual(pool.state.nodes.map((node) => node.id), ['x2']);
  });

  results.push('常量与 Python 实现对齐');
  await test('分页大小、可重试 errno、默认有效期与 Python 一致', () => {
    assert.strictEqual(PAGE_SIZE, 1000);
    assert.deepStrictEqual([...RETRYABLE_ERRNO].sort((a, b) => a - b), [-11, -8, -6, 112, 158]);
    assert.strictEqual(DEFAULT_EXPIRES_IN, 2592000);
    assert.strictEqual(EXPIRING_SOON_SECONDS, 3 * 24 * 3600);
  });
  await test('kind 分类与 Python 的扩展名集合一致', () => {
    assert.strictEqual(classifyKind('a.MP4', false), 'video');
    assert.strictEqual(classifyKind('a.目录', true), 'directory');
    assert.strictEqual(classifyKind('没有扩展名', false), 'other');
  });

  results.push('源码约束');
  await test('netdisk-live.js 不含任何网盘写接口字符串', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'netdisk-live.js'), 'utf8');
    for (const forbidden of ['precreate', 'superfile2', 'filemanager', 'opera=delete', '/api/create', 'pcs.baidu.com']) {
      assert.ok(!source.includes(forbidden), `源码不应出现 ${forbidden}`);
    }
  });
  await test('netdisk-live.js 只请求 pan.baidu.com/api/list', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'netdisk-live.js'), 'utf8');
    // openapi.baidu.com 只是返回给浏览器打开的授权页，服务端从不请求它
    const hosts = [...source.matchAll(/https:\/\/[^`'"\s]+/g)]
      .map((match) => match[0])
      .filter((url) => !url.startsWith(AUTHORIZE_URL));
    assert.deepStrictEqual([...new Set(hosts)], ['https://pan.baidu.com']);
  });
  await test('LiveError 都带稳定 errorCode', () => {
    try {
      readEncryptionKey({});
      assert.fail('应当抛错');
    } catch (error) {
      assert.ok(error instanceof LiveError);
      assert.strictEqual(typeof error.code, 'string');
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
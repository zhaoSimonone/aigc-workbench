const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');

const source = fs.readFileSync(path.join(__dirname, '../src/index.js'), 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const routes = new Map();
const id = '11111111-2222-4333-8444-555555555555';
const other = '22222222-2222-4333-8444-555555555555';
let queries = [];
let row = { id, name: '原名称', favorite: false, used: false };
const query = async (sql, params) => {
  queries.push({ sql, params });
  if (sql.includes('COUNT(*)')) return { rows: [{ total: 3 }] };
  if (sql.includes('FOR UPDATE')) return { rows: [row] };
  if (sql.startsWith('SELECT t.name')) return { rows: [{ name: 'c' }, { name: 'a' }, { name: 'b' }] };
  if (sql.startsWith('SELECT source_asset_id')) return { rows: [] };
  if (sql.includes('LIMIT $')) return { rows: [{ id, name: '素材', sourceUrl: 'url', folder: '灵感收集', durationSeconds: 12 }] };
  if (sql.startsWith('SELECT id FROM assets')) return { rows: [] };
  return { rows: [] };
};
const context = {
  app: { get: (url, _auth, fn) => routes.set(`GET ${url}`, fn),
    patch: (url, _auth, fn) => routes.set(`PATCH ${url}`, fn) },
  requireUser() {},
  pool: { query, connect: async () => ({ query, release() {} }) },
  uuidPattern: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
  assetQuery: async (assetId) => assetId === id ? row : null,
  hydrateAsset: async (asset) => asset,
  console,
};
vm.createContext(context);
vm.runInContext([
  section('function parseTags(', 'async function readMediaMetadata('),
  section("app.get('/api/assets/metadata-policy'", "app.get('/api/assets/:id/stream'"),
  section('async function organizeAsset(', 'function publicMusicTrack('),
].join('\n'), context);

async function request(route, { body = {}, params = { id }, query: queryParams = {}, strict = true } = {}) {
  let status = 200;
  let result;
  const res = { status(code) { status = code; return this; }, json(payload) { result = payload; } };
  await routes.get(route)({ user: { id: 'user' }, params, query: queryParams, body,
    get: () => strict ? 'curated-v1' : '' }, res, (error) => { throw error; });
  return { status, result };
}

(async () => {
  const detail = await request('GET /api/assets/:id');
  assert.equal(detail.result.asset.id, id);
  assert.equal((await request('GET /api/assets/:id', { params: { id: other } })).status, 404);
  assert.equal((await request('GET /api/assets/:id', { params: { id: 'bad' } })).status, 400);
  queries = [];
  const page = await request('GET /api/assets', { query: { limit: '1', q: "name' OR 1=1", folder: '灵感收集' } });
  assert.equal(page.result.hasMore, true);
  assert.equal(page.result.total, 3);
  assert.ok(queries[0].params.includes("%name' OR 1=1%"));
  assert.ok(!queries[0].sql.includes("name' OR 1=1"));
  assert.equal(queries[1].params.at(-2), 1);
  assert.ok(queries[1].sql.includes('WITH selected AS'));
  assert.ok(queries[1].sql.includes('JOIN selected ta'));
  queries = [];
  await request('GET /api/assets');
  assert.equal(queries[1].params.at(-2), null);
  assert.equal((await request('GET /api/assets', { query: { offset: '-1' } })).status, 400);
  assert.equal((await request('GET /api/assets', { query: { duration_min: 'oops' } })).status, 400);
  queries = [];
  const invalid = await request('PATCH /api/assets/:id/organize', { body: { name: '长'.repeat(33), tags: ['a'] } });
  assert.equal(invalid.status, 400);
  assert.equal(queries.length, 0);
  assert.ok(invalid.result.fields.some((field) => field.field === 'tags'));
  const drift = await request('PATCH /api/assets/:id/organize', { body: { name: '新名称', before: { name: '旧快照' } } });
  assert.equal(drift.status, 409);
  assert.ok(queries.some(({ sql }) => sql === 'ROLLBACK'));
  queries = [];
  const success = await request('PATCH /api/assets/:id/organize', { body: { name: '新名称', before: { tags: ['b', 'a', 'c'] } } });
  assert.equal(success.status, 200);
  assert.ok(queries.some(({ sql }) => sql === 'COMMIT'));
  assert.equal((await request('PATCH /api/assets/:id/organize', { body: { parentAssetIds: [other] } })).status, 400);
  const batch = await request('PATCH /api/assets', { body: { items: [{ id, name: '新名称' }, { id: other, name: '' }] } });
  assert.deepEqual(Array.from(batch.result.results, (item) => item.status), [200, 400]);
  const invalidBatch = await request('PATCH /api/assets', {
    body: { items: [{ id, tags: [null, 'b', 'c'] }] },
  });
  assert.equal(invalidBatch.result.results[0].status, 400);
  assert.equal(invalidBatch.result.results[0].fields[0].field, 'tags');
  assert.equal((await request('PATCH /api/assets', { body: { ids: [id, id], changes: { used: true } } })).status, 400);
  const candidate = { work: '作品', name: '角色', evidence: '画面文字', confidence: 0.9, confirmed: true };
  row.character_candidates = [{ confirmed: true, confidence: 0.9, evidence: '画面文字', name: '角色', work: '作品' }];
  assert.equal((await request('PATCH /api/assets/:id/organize', {
    body: { name: '新名称', before: { characterCandidates: [candidate] } },
  })).status, 200);
  assert.equal((await request('PATCH /api/assets/:id/organize', { body: { characterCandidates: [candidate] } })).status, 200);
  await assert.rejects(request('PATCH /api/assets/:id/organize', { body: { characterCandidates: [{ ...candidate, confidence: 2 }] } }));
  queries = [];
  await request('GET /api/assets', { query: { characterWork: '作品', characterName: '角色', confirmedCharacter: 'true' } });
  assert.ok(queries[0].sql.includes('jsonb_array_elements'));
  assert.ok(queries[0].sql.includes("c->>'name'"));
  await assert.rejects(request('PATCH /api/assets/:id/organize', { body: { tags: [null, 'b', 'c'] } }));
  row = { ...row, source_url: 'url', folder: '灵感收集', duration_seconds: 12 };
  const similar = await request('GET /api/assets', { query: { similarTo: id } });
  assert.ok(similar.result.assets[0].similarityReasons.includes('sameSourceUrl'));

  // 社交计数快照：结构固定，null 表示平台未提供，不等于 0。
  row.social_metrics = { platform: 'instagram', capturedAt: '2026-11-10T09:35:12Z',
    source: 'instagram-mobile-info', like: 596, comment: 6, repost: 11, share: 41, save: 194, play: 6762 };
  queries = [];
  const metricsWrite = await request('PATCH /api/assets/:id/organize', {
    body: { socialMetrics: { platform: 'instagram', capturedAt: '2026-11-10T09:35:12Z',
      source: 'instagram-mobile-info', like: 596, comment: 6, repost: 11, share: 41, save: 194, play: 6762 } },
  });
  assert.equal(metricsWrite.status, 200);
  const metricsUpdate = queries.find(({ sql }) => sql.startsWith('UPDATE assets SET'));
  assert.ok(metricsUpdate.sql.includes('social_metrics=$'));
  assert.deepEqual(JSON.parse(metricsUpdate.params.at(-3)), row.social_metrics);
  // 未知字段、负数、小数、数组都必须被拒绝。
  for (const bad of [
    { socialMetrics: { like: 1, unknown: 2 } },
    { socialMetrics: { like: -1 } },
    { socialMetrics: { like: 1.5 } },
    { socialMetrics: [1, 2] },
    { socialMetrics: 'not-json' },
  ]) {
    await assert.rejects(request('PATCH /api/assets/:id/organize', { body: bad }));
  }
  // 并发校验也看社交计数：快照不一致要 409。
  assert.equal((await request('PATCH /api/assets/:id/organize', {
    body: { name: '新名称', before: { socialMetrics: { like: 1 } } },
  })).status, 409);
  assert.equal((await request('PATCH /api/assets/:id/organize', {
    body: { name: '新名称', before: { socialMetrics: row.social_metrics } },
  })).status, 200);
  // 回读映射：素材详情返回 socialMetrics，而不是数据库列名。
  assert.ok(source.includes('socialMetrics: row.social_metrics || {}'));
  console.log('Asset API workflow: detail, paging, legacy compatibility, lint, conflict, batch, candidates and social counts passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });

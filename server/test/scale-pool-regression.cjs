// Real single-connection PostgreSQL pool; media operations are stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const path = require('node:path');
const { Pool } = require('pg');
assert.equal(new URL(process.env.DATABASE_URL).hostname, 'aigc-scale-db');
const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 1, connectionTimeoutMillis: 500 });
const source = fs.readFileSync(process.env.SOURCE_INDEX || require.resolve('../src/index.js'), 'utf8');
const section = (start, end) => {
  const offset = source.indexOf(start);
  assert.ok(offset >= 0);
  const stop = source.indexOf(end, offset);
  assert.ok(stop > offset);
  return source.slice(offset, stop);
};
const routes = new Map();
const context = {
  pool, crypto, path, fs: { statSync: () => ({ size: 100 }) },
  app: { post: (url, _auth, handler) => routes.set(`POST ${url}`, handler),
    patch: (url, _auth, handler) => routes.set(`PATCH ${url}`, handler) }, requireUser() {},
  parseTags: (v) => v || [], strictMetadata: () => false,
  normalizeFolder: (v) => v || '灵感收集', parseCharacterCandidates: (v) => v || [],
  validateAssetMetadata: () => [], safeName: String, mediaCacheControl: '',
  putObject: async () => {}, deleteObject: async () => {},
  createAssetPreview: async () => ({ thumbKey: null, durationSeconds: 1 }),
  hashFile: async () => crypto.randomBytes(32).toString('hex'),
  readMediaMetadata: async () => ({}), hydrateAsset: async (row) => row,
  uuidPattern: /^[0-9a-f-]{36}$/,
};
vm.createContext(context);
vm.runInContext([
  section('async function persistAsset(', 'function downloadObject('),
  section('async function ingestAsset(', 'async function hydrateAsset('),
  section('async function assetQuery(', 'function formatBytes('),
  section('async function organizeAsset(', "app.patch('/api/assets/:id/organize'"),
  section('const promptStatuses =', "app.get('/api/video-accounts'"),
  section("app.post('/api/prompts',", "app.delete('/api/prompts/:id'"),
].join('\n'), context);
async function main() {
  const owner = (await pool.query("INSERT INTO users(name,email,password_hash) VALUES('Pool regression',$1,'unused') RETURNING id",
    [`scale-pool-${crypto.randomUUID()}@example.invalid`])).rows[0].id;
  const checks = [];
  try {
    const imported = await context.persistAsset({ userId: owner, tempPath: '/stub', originalName: 'sample.mp4',
      mimeType: 'video/mp4', metadata: { name: 'Import regression' } });
    assert.ok(imported.id);
    assert.deepEqual(Array.from(imported.tags), ['待整理']);
    checks.push('import returns full detail with the only pool connection already held');
    const uploaded = await context.ingestAsset({ userId: owner, tempPath: '/stub', objectKey: 'fixture/pool-regression',
      type: 'video', name: 'Upload regression', source: 'local', sourceUrl: '', characterName: '', characterCategory: '',
      folder: '灵感收集', used: false, tags: ['pool-check'], parentIds: [imported.id],
      mimeType: 'video/mp4', sizeBytes: 100, deleteObjectsOnFailure: false });
    assert.deepEqual(Array.from(uploaded.parent_asset_ids), [imported.id]);
    assert.deepEqual(Array.from(uploaded.tags), ['pool-check']);
    checks.push('upload returns tags and parent relationships without a second connection');
    const organize = async (id, value) => {
      let status = 200, result;
      await context.organizeAsset({ params: { id }, user: { id: owner }, body: { note: value } },
        { status(n) { status = n; return this; }, json(v) { result = v; } }, (error) => { throw error; });
      assert.equal(status, 200);
      assert.equal(result.asset.note, value);
    };
    await Promise.all(Array.from({ length: 5 }, (_, i) => organize(uploaded.id, `pool note ${i}`)));
    checks.push('five concurrent writes complete through one connection and preserve hydrated results');
    const promptRequest = async (route, body, id) => {
      let status = 200, result;
      await routes.get(route)({ params: { id }, user: { id: owner }, body },
        { status(n) { status = n; return this; }, json(v) { result = v; } }, (error) => { throw error; });
      assert.equal(status, route.startsWith('POST') ? 201 : 200);
      return result.prompt;
    };
    const prompt = await promptRequest('POST /api/prompts', { title: 'Pool prompt', content: 'Full content',
      assetLinks: [{ assetId: uploaded.id, usageRole: 'input_video' }] });
    assert.equal(prompt.assetLinks[0].assetId, uploaded.id);
    const edited = await promptRequest('PATCH /api/prompts/:id', { note: 'Saved without pool wait' }, prompt.id);
    assert.equal(edited.note, 'Saved without pool wait');
    assert.equal(edited.assetLinks[0].assetId, uploaded.id);
    checks.push('prompt create and edit reuse the connection and preserve linked assets');
    assert.equal(pool.waitingCount, 0);
    assert.equal(pool.idleCount, 1);
    checks.push('connection returned to pool with no pending borrowers');
    const result = { passed: true, poolSize: 1, concurrentWrites: 5, checks };
    fs.writeFileSync('/tmp/scale-pool-regression.json', JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally {
    await pool.query('DELETE FROM users WHERE id=$1', [owner]);
  }
}
main().finally(() => pool.end()).catch((error) => { console.error(error.message); process.exitCode = 1; });

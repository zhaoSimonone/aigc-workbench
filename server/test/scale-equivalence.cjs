// Differential checks against the previous release, only on isolated data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { Pool } = require('pg');
assert.equal(new URL(process.env.DATABASE_URL).hostname, 'aigc-scale-db');
const pool = new Pool({ connectionString: process.env.DATABASE_URL, statement_timeout: 20000 });
function browser(module) {
  const routes = new Map();
  module.registerAssetBrowser({ get: (path, ...handlers) => routes.set(path, handlers.at(-1)) },
    { pool, requireUser() {}, stableSignedUrl: String, formatBytes: String, formatDuration: String });
  return async (owner, query, summary = false) => {
    let output;
    await routes.get('/api/asset-browser' + (summary ? '/summary' : ''))(
      { user: { id: owner }, query }, { json: (body) => { output = body; } }, (e) => { throw e; });
    return JSON.parse(JSON.stringify(output));
  };
}
const before = browser(require('/acceptance/baseline-src/asset-browser'));
const after = browser(require('../src/asset-browser'));
const checks = [];
async function compare(owner, query, summary) {
  const old = await before(owner, query, summary);
  assert.deepEqual(await after(owner, query, summary), old, JSON.stringify(query));
  checks.push(query);
}
async function main() {
  const owner = (await pool.query("SELECT id FROM users WHERE email='scale-1000@example.invalid'")).rows[0].id;
  for (const query of [
    {}, { offset: '960' }, { offset: '2000' }, { sort: '名称', offset: '600' },
    { sort: 'cover' }, { q: '素材000999' }, { q: '100%_' }, { q: '\\' }, { q: '标签1' }, { q: '不存在' },
    { q: '本地导入   标签' }, { q: '标签1 标签10' },
    { tag: '标签1' }, { filter: '已收藏' }, { filter: '已使用' },
    { folder: '角色设定' }, { mode: 'albums' }, { mode: 'albums', offset: '60' },
    { mode: 'albums', q: '不存在' }, { mode: 'albums', type: 'video' },
    { mode: 'albums', category: '正脸' },
  ]) await compare(owner, query);
  await compare(owner, {}, true);
  const ids = (await pool.query('SELECT id FROM assets WHERE user_id=$1 ORDER BY created_at LIMIT 2', [owner])).rows.map((r) => r.id);
  await compare(owner, { ids: ids.join(',') });
  const readDetail = (file) => {
    const text = fs.readFileSync(file, 'utf8');
    return vm.runInNewContext(text.slice(text.indexOf('async function assetQuery('), text.indexOf('\nfunction formatBytes(')) + '\nassetQuery', { pool });
  };
  const oldDetail = readDetail('/acceptance/baseline-src/index.js');
  const newDetail = readDetail(require.resolve('../src/index.js'));
  assert.deepEqual(JSON.parse(JSON.stringify(await newDetail(ids[0], owner))), JSON.parse(JSON.stringify(await oldDetail(ids[0], owner))));
  const other = (await pool.query("SELECT id FROM users WHERE email='scale-10000@example.invalid'")).rows[0].id;
  assert.equal(await newDetail(ids[0], other), undefined);
  assert.equal(await newDetail(ids[0], other, true), undefined);
  const media = await newDetail(ids[0], owner, true);
  assert.ok(media.object_key && !('note' in media) && !('tags' in media));
  // Reversible fixture writes verify search reads current data, not a stale projection.
  const tag = (await pool.query("SELECT id FROM tags WHERE user_id=$1 AND name='标签1'", [owner])).rows[0].id;
  try {
    await pool.query("UPDATE tags SET name='改名%_中文' WHERE id=$1", [tag]);
    await compare(owner, { q: '改名%_' });
    assert.equal((await after(owner, { q: '改名%_' })).total, 1000);
    await pool.query('UPDATE assets SET deleted_at=now() WHERE id=$1', [ids[0]]);
    assert.equal(await newDetail(ids[0], owner), undefined);
    assert.equal(await newDetail(ids[0], owner, true), undefined);
    assert.equal((await after(owner, {})).total, 999);
    await compare(owner, { q: '改名%_' });
  } finally {
    await pool.query("UPDATE tags SET name='标签1' WHERE id=$1", [tag]);
    await pool.query('UPDATE assets SET deleted_at=NULL WHERE id=$1', [ids[0]]);
  }
  const result = { passed: true, comparisons: checks.length, highFanoutDetail: true, mediaIsolation: true, liveWrites: true };
  fs.writeFileSync('/tmp/scale-equivalence.json', JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
}
main().finally(() => pool.end()).catch((e) => { console.error(e); process.exitCode = 1; });

// Run only inside the disposable scale API container, using its pg dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');
const { Pool } = require('pg');
const { registerAssetBrowser } = require('../src/asset-browser');
assert.equal(new URL(process.env.DATABASE_URL).hostname, 'aigc-scale-db');
const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 5, statement_timeout: 20000 });
const routes = new Map();
registerAssetBrowser({ get: (path, ...handlers) => routes.set(path, handlers.at(-1)) },
  { pool, requireUser() {}, stableSignedUrl: (s) => s, formatBytes: String, formatDuration: String });
const source = fs.readFileSync(require.resolve('../src/index.js'), 'utf8');
const detail = vm.runInNewContext(source.slice(source.indexOf('async function assetQuery('), source.indexOf('\nfunction formatBytes(')) + '\nassetQuery', { pool });
const results = [];
async function call(owner, query = {}, summary = false) {
  let output;
  await routes.get('/api/asset-browser' + (summary ? '/summary' : ''))(
    { user: { id: owner }, query }, { json: (value) => { output = value; } }, (error) => { throw error; });
  return output;
}
async function measure(name, run, repetitions = 7) {
  await run();
  const times = [];
  for (let i = 0; i < repetitions; i++) {
    const start = performance.now();
    await run();
    times.push(Math.round((performance.now() - start) * 10) / 10);
  }
  times.sort((a, b) => a - b);
  const result = { name, medianMs: times[Math.floor(times.length / 2)], maxMs: times.at(-1), samples: times };
  results.push(result);
  console.log(JSON.stringify(result));
}
async function seed(size) {
  const email = `scale-${size}@example.invalid`;
  const existing = await pool.query('SELECT id FROM users WHERE email=$1', [email]);
  if (existing.rows.length) return existing.rows[0].id;
  const owner = (await pool.query("INSERT INTO users(name,email,password_hash) VALUES('Scale',$1,'unused') RETURNING id", [email])).rows[0].id;
  await pool.query(`INSERT INTO assets(user_id,name,type,object_key,thumb_key,content_type,folder,
    character_name,character_category,note,media_metadata,created_at,favorite,used)
    SELECT $1, '素材' || lpad(i::text,6,'0') || CASE WHEN i%101=0 THEN ' 100%_中文测试' ELSE '' END,
      CASE WHEN i%2=0 THEN 'image' ELSE 'video' END,'fixture/'||i,'thumb/'||i,'video/mp4',
      CASE WHEN i%3=0 THEN '角色设定' ELSE '灵感收集' END,
      CASE WHEN i%3=0 THEN '角色'||(i%1000)::text ELSE '' END,
      CASE WHEN i%2=0 THEN '正脸' ELSE '场景' END,repeat(md5(i::text),30),
      jsonb_build_object('description',repeat(md5((i+1)::text),20)),
      now() - i * interval '1 second',i%7=0,i%9=0 FROM generate_series(1,$2::int) i`, [owner, size]);
  await pool.query("INSERT INTO tags(user_id,name) SELECT $1,'标签'||i FROM generate_series(1,30) i", [owner]);
  await pool.query(`INSERT INTO asset_tags SELECT a.id,t.id FROM
    (SELECT id,row_number() OVER(ORDER BY created_at) n FROM assets WHERE user_id=$1) a
    CROSS JOIN (SELECT id,row_number() OVER(ORDER BY name) n FROM tags WHERE user_id=$1) t
    WHERE t.n<=3 OR a.n=1`, [owner]);
  await pool.query("INSERT INTO character_albums(user_id,name) SELECT DISTINCT user_id,character_name FROM assets WHERE user_id=$1 AND character_name<>''", [owner]);
  const ids = (await pool.query('SELECT id FROM assets WHERE user_id=$1 ORDER BY created_at LIMIT 201', [owner])).rows.map((r) => r.id);
  await pool.query('INSERT INTO asset_relations SELECT id,$1 FROM unnest($2::uuid[]) id', [ids[0], ids.slice(1, 101)]);
  await pool.query('INSERT INTO asset_relations SELECT $1,id FROM unnest($2::uuid[]) id', [ids[0], ids.slice(101)]);
  await pool.query('ANALYZE');
  return owner;
}
async function main() {
  for (const size of [1000, 10000, 30000]) {
    const owner = await seed(size);
    const run = async (name, query, summary) => measure(`${size}:${name}`, () => call(owner, query, summary));
    await run('first', {});
    await run('deep', { offset: String(size - 60) });
    await run('name-sort', { sort: '名称' });
    await run('search-name', { q: '素材000999' });
    await run('search-literal', { q: '100%_' });
    await run('search-tag', { q: '标签1' });
    await run('search-miss', { q: '不存在' });
    await run('tag-filter', { tag: '标签1' });
    await run('albums', { mode: 'albums' });
    await run('summary', {}, true);
    const id = (await pool.query('SELECT id FROM assets WHERE user_id=$1 ORDER BY created_at LIMIT 1', [owner])).rows[0].id;
    await measure(`${size}:detail-30x100x100`, async () => {
      const row = await detail(id, owner);
      assert.equal(row.tags.length, 30);
      assert.equal(row.parent_asset_ids.length, 100);
      assert.equal(row.derived_asset_ids.length, 100);
    }, 3);
    if (size === 30000 && process.env.CONCURRENT) {
      await measure(`${size}:concurrent5-mixed`, () => Promise.all([
        call(owner), call(owner, { q: '不存在' }), call(owner, { mode: 'albums' }),
        call(owner, {}, true), detail(id, owner),
      ]), 10);
    }
  }
  fs.writeFileSync('/tmp/scale-results.json', JSON.stringify(results, null, 2));
}
main().finally(() => pool.end()).catch((e) => { console.error(e); process.exitCode = 1; });

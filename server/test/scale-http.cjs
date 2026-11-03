const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const { performance } = require('node:perf_hooks');
const { Pool } = require('pg');
assert.equal(new URL(process.env.DATABASE_URL).hostname, 'aigc-scale-db');
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
async function main() {
  const owner = (await pool.query("SELECT id FROM users WHERE email='scale-30000@example.invalid'")).rows[0].id;
  const id = (await pool.query('SELECT id FROM assets WHERE user_id=$1 ORDER BY created_at LIMIT 1', [owner])).rows[0].id;
  const token = crypto.randomBytes(32).toString('hex');
  const hash = crypto.createHash('sha256').update(token).digest('hex');
  await pool.query("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')", [hash, owner]);
  const samples = [];
  const request = async (path, check, record = true) => {
    const start = performance.now();
    const res = await fetch('http://127.0.0.1:18080/api' + path, { redirect: 'manual', headers: { Cookie: `aigc_session=${token}` } });
    const text = await res.text();
    const ms = Math.round(performance.now() - start);
    assert.equal(res.status, path.endsWith('/stream') ? 307 : 200, path);
    if (check) check(JSON.parse(text));
    if (record) samples.push({ path, ms, bytes: Buffer.byteLength(text) });
  };
  const workload = [
    ['/asset-browser', (p) => { assert.equal(p.total, 30000); assert.equal(p.assets.length, 60); }],
    ['/asset-browser?offset=29940', (p) => { assert.equal(p.assets.length, 60); assert.equal(p.hasMore, false); }],
    ['/asset-browser?q=' + encodeURIComponent('不存在'), (p) => assert.equal(p.total, 0)],
    ['/asset-browser?mode=albums', (p) => { assert.equal(p.total, 1000); assert.equal(p.albums.length, 60); }],
    ['/asset-browser/summary', (p) => assert.equal(p.counts.total, 30000)],
    [`/assets/${id}`, (p) => { assert.equal(p.asset.tags.length, 30); assert.equal(p.asset.parentAssetIds.length, 100); assert.equal(p.asset.derivedAssetIds.length, 100); }],
    [`/assets/${id}/stream`],
  ];
  try {
    for (const args of workload) await request(args[0], args[1], false);
    for (let run = 0; run < 10; run++) for (const args of workload) await request(...args);
    const serial = samples.splice(0);
    for (let run = 0; run < 10; run++) await Promise.all([workload[0], workload[2], workload[3], workload[4], workload[5]].map((args) => request(...args)));
    const summarize = (values) => [...new Set(values.map((v) => v.path))].map((path) => {
      const rows = values.filter((v) => v.path === path).sort((a,b) => a.ms - b.ms);
      return { path: path.replace(id, ':high-fanout-id'), requests: rows.length, medianMs: rows[Math.floor(rows.length / 2)].ms,
        p95Ms: rows[Math.ceil(rows.length * 0.95) - 1].ms, maxBytes: Math.max(...rows.map((r) => r.bytes)) };
    });
    const results = { passed: true, serial: summarize(serial), concurrent5: summarize(samples), errors: 0 };
    fs.writeFileSync('/tmp/scale-http.json', JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results, null, 2));
  } finally { await pool.query('DELETE FROM sessions WHERE token_hash=$1', [hash]); }
}
main().finally(() => pool.end()).catch((e) => { console.error(e); process.exitCode = 1; });

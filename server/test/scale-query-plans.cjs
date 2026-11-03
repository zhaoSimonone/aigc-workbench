const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { Pool } = require('pg');
assert.equal(new URL(process.env.DATABASE_URL).hostname, 'aigc-scale-db');
const pool = new Pool({ connectionString: process.env.DATABASE_URL, statement_timeout: 20000 });
async function main() {
  const owner = (await pool.query("SELECT id FROM users WHERE email='scale-30000@example.invalid'")).rows[0].id;
  const id = (await pool.query('SELECT id FROM assets WHERE user_id=$1 ORDER BY created_at LIMIT 1', [owner])).rows[0].id;
  const output = {};
  for (const [label, root] of [['before', '/acceptance/baseline-src'], ['after', '/app/src']]) {
    const queries = [];
    const recordingPool = { query: async (sql, values) => {
      queries.push([sql, values]);
      return pool.query(sql, values);
    } };
    let handler;
    require(root + '/asset-browser').registerAssetBrowser({ get: (path, ...handlers) => {
      if (path === '/api/asset-browser') handler = handlers.at(-1);
    } }, { pool: recordingPool, requireUser() {}, stableSignedUrl: String, formatBytes: String, formatDuration: String });
    await handler({ user: { id: owner }, query: { offset: '29940' } }, { json() {} }, (e) => { throw e; });
    const page = queries.at(-1);
    const text = fs.readFileSync(root + '/index.js', 'utf8');
    const detail = vm.runInNewContext(text.slice(text.indexOf('async function assetQuery('), text.indexOf('\nfunction formatBytes(')) + '\nassetQuery', { pool: recordingPool });
    await detail(id, owner);
    const highFanout = queries.at(-1);
    for (const [name, [sql, values]] of [['deepPage', page], ['detail', highFanout]]) {
      const result = (await pool.query('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + sql, values)).rows[0]['QUERY PLAN'][0];
      output[`${label}:${name}`] = result;
      const nodes = [];
      const visit = (node) => { nodes.push(node); (node.Plans || []).forEach(visit); };
      visit(result.Plan);
      console.log(JSON.stringify({ label, name, executionMs: result['Execution Time'],
        maxLoops: Math.max(...nodes.map((n) => n['Actual Loops'])),
        maxRows: Math.max(...nodes.map((n) => n['Actual Rows'])),
        tempWrittenBlocks: result.Plan['Temp Written Blocks'] || 0 }));
      if (label === 'after' && name === 'deepPage') assert.ok(Math.max(...nodes.filter((n) => n['Parent Relationship'] === 'SubPlan').map((n) => n['Actual Loops'])) <= 60);
    }
  }
  fs.writeFileSync('/tmp/scale-query-plans.json', JSON.stringify(output, null, 2));
}
main().finally(() => pool.end()).catch((e) => { console.error(e); process.exitCode = 1; });

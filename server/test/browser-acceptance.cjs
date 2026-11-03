// Execute only in the isolated candidate container, never against the production database.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const { Pool } = require('pg');
const bcrypt = require('bcryptjs');
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
assert.equal(new URL(process.env.DATABASE_URL).pathname, '/aigc_browser_acceptance_20261103');
const checks = [];
const check = (name) => { checks.push(name); console.log('PASS ' + name); };
async function main() {
  const email = 'browser-acceptance-20261103@example.invalid';
  const password = 'browser-acceptance-local-20261103';
  await pool.query("DELETE FROM users WHERE email LIKE 'browser-acceptance-%@example.invalid'");
  const owner = (await pool.query('INSERT INTO users(name,email,password_hash) VALUES($1,$2,$3) RETURNING id',
    ['分页验收', email, await bcrypt.hash(password, 4)])).rows[0].id;
  const other = (await pool.query("INSERT INTO users(name,email,password_hash) VALUES('Other','browser-acceptance-other@example.invalid','unused') RETURNING id")).rows[0].id;
  const media = JSON.parse(fs.readFileSync('/acceptance/media.json'));
  const fixture = [];
  for (let i = 0; i < 143; i++) {
    const image = i % 2 === 0;
    const row = (await pool.query(
      `INSERT INTO assets(user_id,name,type,object_key,thumb_key,content_type,folder,character_name,character_category,
         favorite,used,created_at,deleted_at,music_title,note)
       VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) RETURNING *`,
      [i === 142 ? other : owner, i === 1 ? '跨页唯一素材100%_原片' : `${['阿','波','陈','张'][i % 4]}素材${String(i).padStart(3, '0')}`,
        image ? 'image' : 'video', image ? media.thumb_key : media.object_key, media.thumb_key,
        image ? 'image/jpeg' : media.content_type,
        i % 3 === 0 ? '角色设定' : i % 3 === 1 ? '发型库' : '灵感收集',
        i % 5 === 0 ? '' : i % 2 ? '角色甲' : '角色乙', i % 4 === 0 ? '正脸' : '',
        i % 7 === 0, i % 9 === 0, new Date(Date.UTC(2026, 9, 1, 0, 0, Math.floor(i / 2))),
        i === 141 ? new Date() : null, i === 3 ? '跨页音乐搜索' : '', `完整备注${i}`],
    )).rows[0];
    if (i < 141) fixture.push(row);
  }
  const tag = (await pool.query("INSERT INTO tags(user_id,name) VALUES($1,'跨页标签') RETURNING id", [owner])).rows[0].id;
  await pool.query('INSERT INTO asset_tags(asset_id,tag_id) VALUES($1,$2)', [fixture[1].id, tag]);
  await pool.query('INSERT INTO asset_relations(source_asset_id,derived_asset_id) VALUES($1,$2)', [fixture[1].id, fixture[140].id]);
  await pool.query("INSERT INTO character_albums(user_id,name,cover_asset_id) VALUES($1,'角色甲',$2),($1,'空相册',NULL)", [owner, fixture[1].id]);
  const prompt = (await pool.query("INSERT INTO prompts(user_id,title,content) VALUES($1,'跨页关联提示词','完整提示词') RETURNING id", [owner])).rows[0].id;
  await pool.query("INSERT INTO prompt_asset_links(prompt_id,asset_id,user_id,usage_role) VALUES($1,$2,$3,'input_video')", [prompt, fixture[1].id, owner]);
  const token = crypto.randomBytes(32).toString('hex');
  await pool.query("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 day')", [crypto.createHash('sha256').update(token).digest('hex'), owner]);
  const get = async (path, status = 200, authenticated = true) => {
    const response = await fetch('http://127.0.0.1:18080/api' + path, { headers: authenticated ? { Cookie: `aigc_session=${token}` } : {} });
    assert.equal(response.status, status, path + ': ' + response.status);
    return response.json();
  };
  const expected = [...fixture].sort((a,b) => b.created_at - a.created_at || b.id.localeCompare(a.id));
  const pages = [];
  for (let offset = 0; offset < 180; offset += 60) pages.push(await get(`/asset-browser?offset=${offset}`));
  assert.deepEqual(pages.flatMap((p) => p.assets.map((a) => a.id)), expected.map((a) => a.id));
  assert.deepEqual(pages.map((p) => p.hasMore), [true, true, false]);
  assert.ok(pages.every((p) => p.total === 141));
  assert.ok(!('note' in pages[0].assets[0]) && !('src' in pages[0].assets[0]));
  check('141 fixtures: complete deterministic pagination, compact DTO, soft deletion and user isolation');
  for (const [filter, predicate] of [
    ['type=video', (a) => a.type === 'video'], ['filter=已收藏', (a) => a.favorite],
    ['filter=已使用', (a) => a.used], ['folder=角色设定', (a) => a.folder === '角色设定' || (a.folder === '发型库' && a.character_name)],
    ['folder=角色设定&character=待归类人物', (a) => a.folder === '角色设定' && !a.character_name],
    ['folder=角色设定&character=角色甲&category=发型', (a) => a.folder === '发型库' && a.character_name === '角色甲' && !a.character_category],
  ]) {
    const result = await get('/asset-browser?limit=200&' + encodeURI(filter));
    assert.deepEqual(result.assets.map((a) => a.id), expected.filter(predicate).map((a) => a.id), filter);
  }
  for (const q of ['100%_', '跨页唯一素材', '跨页标签', '跨页音乐搜索']) {
    const result = await get('/asset-browser?q=' + encodeURIComponent(q));
    assert.equal(result.total, 1, q);
  }
  const sorted = await get('/asset-browser?sort=' + encodeURIComponent('名称') + '&limit=200');
  assert.deepEqual(sorted.assets.map((a) => a.id), [...fixture].sort((a,b) => a.name.localeCompare(b.name,'zh') || a.id.localeCompare(b.id)).map((a) => a.id));
  check('cross-page literal/music/tag search, all filters and Chinese name sorting');
  const summary = await get('/asset-browser/summary');
  assert.equal(summary.counts.total, 141);
  assert.equal(summary.counts.favorite, fixture.filter((a) => a.favorite).length);
  assert.equal(summary.prompts, 1);
  assert.deepEqual(summary.tags, ['跨页标签']);
  const albums = await get('/asset-browser?mode=albums');
  assert.equal(albums.albums.find((a) => a.name === '空相册').count, 0);
  const fixed = albums.albums.find((a) => a.name === '角色甲');
  assert.equal(fixed.coverAssetId, fixture[1].id);
  assert.ok(fixed.cover);
  assert.equal(albums.albums.reduce((n,a) => n + a.count,0), summary.roles);
  assert.equal(summary.characters.reduce((n,a) => n+a.count,0), summary.roles);
  const oneAlbum = await get('/asset-browser?mode=albums&limit=1&offset=1');
  assert.equal(oneAlbum.albums.length, 1);
  assert.equal(oneAlbum.total, albums.total);
  const linked = await get(`/asset-browser?ids=${fixture[1].id}`);
  assert.equal(linked.assets[0].id, fixture[1].id);
  const full = await get('/assets');
  assert.equal(full.assets.length, 141);
  assert.equal(full.limit, null);
  assert.equal(full.assets.find((a) => a.id === fixture[140].id).parentAssetIds[0], fixture[1].id);
  const detail = await get('/assets/' + fixture[1].id);
  assert.equal(detail.asset.note, '完整备注1');
  check('global summary, empty/fixed-cover albums, categories, ID links and unchanged legacy/detail API');
  await get('/asset-browser?offset=-1', 400);
  await get('/asset-browser?ids=invalid', 400);
  await get('/asset-browser', 401, false);
  await get('/asset-browser/summary', 401, false);
  check('authentication and invalid parameter handling');
  fs.writeFileSync('/tmp/browser-fixture.json', JSON.stringify({ email, password, targetId: fixture[1].id, newestId: fixture[140].id, checks }));
}
main().finally(() => pool.end()).catch((e) => { console.error(e); process.exitCode = 1; });

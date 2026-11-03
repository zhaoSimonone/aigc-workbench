const { test } = require('node:test');
const assert = require('node:assert/strict');
const { browserQuery } = require('../src/asset-browser');

test('bounded pages and invalid query values', () => {
  assert.equal(browserQuery('owner', {}).limit, 60);
  assert.equal(browserQuery('owner', { limit: '10000' }).limit, 200);
  for (const query of [{ limit: '-1' }, { offset: '1.5' }, { offset: '9007199254740999' },
    { ids: 'invalid' }, { exclude: 'invalid' }, { type: 'other' }]) {
    assert.throws(() => browserQuery('owner', query));
  }
});
test('filters remain parameterized and searches treat SQL wildcard characters literally', () => {
  const query = browserQuery('owner', { q: "x%' OR 1=1", folder: "x' OR 1=1", tag: 'a', character: '待归类人物' });
  assert.match(query.where, /a.user_id=\$1.*deleted_at IS NULL/);
  assert.ok(!query.where.includes("OR 1=1"));
  assert.ok(query.params.includes("%x\\%' OR 1=1%"));
  assert.ok(query.params.includes(''));
});
test('character reuse, category fallback and deterministic sorts', () => {
  const role = browserQuery('owner', { folder: '角色设定', category: '发型' });
  assert.match(role.where, /a.folder='发型库' AND a.character_name<>''/);
  assert.match(role.where, /NULLIF\(a.character_category/);
  assert.equal(browserQuery('owner', {}).order, 'a.created_at DESC, a.id DESC');
  assert.match(browserQuery('owner', { sort: '名称' }).order, /zh-Hans-CN-x-icu.*a.id/);
});

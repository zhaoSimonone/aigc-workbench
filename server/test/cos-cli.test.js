const { test } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const COS = require('cos-nodejs-sdk-v5');
const { createCosRouter } = require('../src/cos-cli');

const BUCKET = 'aigc-1257258774';
const OWNER = 'x-cos-meta-aigc-cli-owner';
const UPLOAD = 'x-cos-meta-aigc-cli-upload';
const plan = { key: 'file/report.xlsx', size: 4, contentType: 'application/octet-stream', expires: 3600 };

async function fixture(t, options = {}) {
  const objects = new Map();
  const signed = [];
  let clock = Date.now();
  const sdk = new COS({ SecretId: 'test-id', SecretKey: 'test-secret', Protocol: 'https:' });
  const app = express();
  app.use(express.json());
  app.use('/api/cos', (req, res, next) => {
    if (!req.headers['x-test-user']) return res.status(401).json({ error: 'login required' });
    req.user = { id: req.headers['x-test-user'] };
    next();
  }, createCosRouter({
    bucket: BUCKET, region: 'ap-shanghai', secret: 'test-secret',
    now: () => clock,
    cos: {
      headObject(params, cb) {
        if (options.headError) return cb(options.headError);
        const headers = objects.get(params.Key);
        headers ? cb(null, { headers }) : cb({ statusCode: 404 });
      },
      getObjectUrl(params, cb) {
        signed.push(params);
        sdk.getObjectUrl(params, cb);
      },
    },
    ...options.config,
  }));
  const server = await new Promise(resolve => {
    const instance = app.listen(0, '127.0.0.1', () => resolve(instance));
  });
  t.after(() => new Promise(resolve => {
    server.close(resolve);
    server.closeAllConnections();
  }));
  return {
    objects, signed,
    advance: ms => { clock += ms; },
    save(ticket) {
      objects.set(ticket.key, Object.fromEntries(
        Object.entries(ticket.headers).map(([key, value]) => [key.toLowerCase(), value]),
      ));
    },
    async post(route, body, user = 'owner') {
      const response = await fetch(`http://127.0.0.1:${server.address().port}/api/cos/${route}`, {
        method: 'POST', headers: {
          'Content-Type': 'application/json', ...(user ? { 'x-test-user': user } : {}),
        }, body: JSON.stringify(body),
      });
      return { status: response.status, body: await response.json(), headers: response.headers };
    },
  };
}

test('authenticated ticket binds headers, bucket and key without exposing a secret', async t => {
  const f = await fixture(t);
  assert.equal((await f.post('upload-ticket', plan, '')).status, 401);
  const ticket = await f.post('upload-ticket', plan);
  assert.equal(ticket.status, 200);
  assert.equal(ticket.headers.get('cache-control'), 'no-store');
  assert.equal(ticket.body.bucket, BUCKET);
  assert.equal(ticket.body.headers[OWNER], 'owner');
  assert.equal(ticket.body.headers['x-cos-forbid-overwrite'], 'true');
  const headerList = new URL(ticket.body.url).searchParams.get('q-header-list').split(';');
  for (const header of ['host', 'content-type', 'content-length', OWNER, UPLOAD, 'x-cos-forbid-overwrite']) {
    assert.ok(headerList.includes(header), `signature must bind ${header}`);
  }
  assert.ok(!JSON.stringify(ticket.body).includes('test-secret'));
  f.save(ticket.body);
  const done = await f.post('finalize-upload', { uploadToken: ticket.body.uploadToken });
  assert.equal(done.status, 200);
  assert.equal(done.body.key, plan.key);
  assert.equal(done.body.expiresIn, 3600);
  assert.equal(f.signed.at(-1).Method, 'GET');
});

test('rejects traversal, reserved Shelf namespaces, invalid sizes and header injection', async t => {
  const f = await fixture(t);
  for (const change of [
    { key: '../private' }, { key: 'file//bad' }, { key: '/file/bad' },
    { key: 'file/\nheader' }, { key: 'file\\bad' }, { key: 'https://host/a' },
    { key: '11111111-2222-4333-8444-555555555555/image/a.png' },
    { size: -1 }, { size: 3 * 1024 ** 3 }, { size: '4' },
    { contentType: 'application/octet-stream\r\nX: bad' }, { expires: 604801 }, { force: 'true' },
  ]) {
    assert.ok([400, 403].includes((await f.post('upload-ticket', { ...plan, ...change })).status));
  }
  assert.equal(f.signed.length, 0);
});

test('only the creating user can explicitly overwrite CLI objects', async t => {
  const f = await fixture(t);
  f.objects.set(plan.key, { [OWNER]: 'other' });
  assert.equal((await f.post('upload-ticket', { ...plan, force: true })).status, 403);
  f.objects.set(plan.key, {});
  assert.equal((await f.post('upload-ticket', { ...plan, force: true })).status, 403);
  f.objects.set(plan.key, { [OWNER]: 'owner' });
  assert.equal((await f.post('upload-ticket', plan)).status, 409);
  const ticket = await f.post('upload-ticket', { ...plan, force: true });
  assert.equal(ticket.status, 200);
  assert.equal(ticket.body.headers['x-cos-forbid-overwrite'], undefined);
});

test('forged, expired and cross-user receipts cannot fetch download links', async t => {
  const f = await fixture(t);
  const { body } = await f.post('upload-ticket', plan);
  f.save(body);
  const changed = body.uploadToken.split('.');
  const payload = JSON.parse(Buffer.from(changed[0], 'base64url').toString());
  changed[0] = Buffer.from(JSON.stringify({ ...payload, key: 'private/secret' })).toString('base64url');
  assert.equal((await f.post('finalize-upload', { uploadToken: changed.join('.') })).status, 400);
  assert.equal((await f.post('finalize-upload', { uploadToken: body.uploadToken }, 'other')).status, 403);
  f.advance(86401 * 1000);
  assert.equal((await f.post('finalize-upload', { uploadToken: body.uploadToken })).status, 400);
  assert.equal(f.signed.length, 1);
});

test('finalization verifies existence, owner, upload marker, size and content type', async t => {
  const f = await fixture(t);
  const { body } = await f.post('upload-ticket', plan);
  assert.equal((await f.post('finalize-upload', { uploadToken: body.uploadToken })).status, 404);
  for (const change of [
    { [OWNER]: 'other' }, { [UPLOAD]: 'other' },
    { 'content-length': '5' }, { 'content-type': 'text/plain' },
  ]) {
    f.save(body);
    f.objects.set(plan.key, { ...f.objects.get(plan.key), ...change });
    assert.equal((await f.post('finalize-upload', { uploadToken: body.uploadToken })).status, 409);
  }
  assert.equal(f.signed.length, 1);
});

test('COS errors and bad server configuration fail closed without leaking details', async t => {
  const f = await fixture(t, { headError: { statusCode: 403, message: 'secret-sensitive-url' } });
  const response = await f.post('upload-ticket', plan);
  assert.equal(response.status, 502);
  assert.ok(!JSON.stringify(response.body).includes('secret-sensitive-url'));
  const wrong = await fixture(t, { config: { bucket: 'another-bucket' } });
  assert.equal((await wrong.post('upload-ticket', plan)).status, 503);
});

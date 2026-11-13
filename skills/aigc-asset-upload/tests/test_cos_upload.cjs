const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { configuration, upload } = require('../scripts/cos_upload.cjs');

const plan = {
  file: '/tmp/example.mp4', key: 'file/example.mp4', size: 4,
  contentType: 'video/mp4', expires: 3600, force: false,
};

function fakeCos({ exists = false, status = 404, size = 4, failUpload = false } = {}) {
  const calls = [];
  let uploaded = false;
  return {
    calls,
    headObject(params, cb) {
      calls.push(['head', params]);
      if (!exists && !uploaded) return cb({ statusCode: status, code: 'DeniedOrMissing' });
      cb(null, { headers: { 'content-length': String(size) } });
    },
    uploadFile(params, cb) {
      calls.push(['upload', params]);
      if (failUpload) return cb({ statusCode: 500 });
      uploaded = true;
      cb(null, {});
    },
    getObjectUrl(params, cb) {
      calls.push(['sign', params]);
      cb(null, { Url: 'https://example.invalid/file/example.mp4?signature=test' });
    },
  };
}

test('upload fixes bucket, streams local file, verifies size and signs GET', async () => {
  const cos = fakeCos();
  const result = await upload(cos, { ...plan, bucket: 'wrong' }, 'ap-shanghai', () => {});
  assert.deepEqual(cos.calls.map(([method]) => method), ['head', 'upload', 'head', 'sign']);
  for (const [, params] of cos.calls) {
    assert.equal(params.Bucket, 'aigc-1257258774');
    assert.equal(params.Key, plan.key);
  }
  assert.equal(cos.calls[1][1].FilePath, plan.file);
  assert.equal(cos.calls[1][1].ContentType, 'video/mp4');
  assert.equal(cos.calls[3][1].Expires, 3600);
  assert.equal(cos.calls[3][1].Method, 'GET');
  assert.equal(cos.calls[3][1].Sign, true);
  assert.ok(result.url.startsWith('https://'));
  assert.equal(result.expiresIn, 3600);
});

test('existing objects require force', async () => {
  const cos = fakeCos({ exists: true });
  await assert.rejects(upload(cos, plan, 'ap-shanghai'), /--force/);
  assert.equal(cos.calls.length, 1);
  await upload(cos, { ...plan, force: true }, 'ap-shanghai');
  assert.equal(cos.calls[1][0], 'upload');
});

test('permission failure does not upload', async () => {
  const cos = fakeCos({ status: 403 });
  await assert.rejects(upload(cos, plan, 'ap-shanghai'), error => error.statusCode === 403);
  assert.equal(cos.calls.length, 1);
});

test('upload and verification failures never return success links', async () => {
  for (const options of [{ size: 10 }, { failUpload: true }]) {
    const cos = fakeCos(options);
    await assert.rejects(upload(cos, plan, 'ap-shanghai'));
    assert.ok(!cos.calls.some(([method]) => method === 'sign'));
  }
});

test('config file plus environment override, region and temporary credentials', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'cos-config-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, 'cos.env');
  fs.writeFileSync(file, 'test fixture', { mode: 0o600 });
  const parse = () => ({
    TENCENT_COS_SECRET_ID: 'file-id', TENCENT_COS_SECRET_KEY: 'file-key',
    TENCENT_COS_REGION: 'ap-shanghai',
  });
  const config = configuration({ envFile: file }, parse, {
    TENCENT_COS_SECRET_ID: 'env-id', TENCENT_COS_SECURITY_TOKEN: 'temporary-token',
  });
  assert.equal(config.SecretId, 'env-id');
  assert.equal(config.SecretKey, 'file-key');
  assert.equal(config.SecurityToken, 'temporary-token');
  assert.equal(config.region, 'ap-shanghai');
  assert.equal(configuration({ envFile: file, region: 'ap-guangzhou' }, parse, {}).region, 'ap-guangzhou');
  assert.throws(() => configuration({ envFile: file }, () => ({}), {}), /TENCENT_COS_SECRET_ID/);
  assert.throws(() => configuration({ envFile: file + '.missing' }, parse, {}), /不存在/);
});

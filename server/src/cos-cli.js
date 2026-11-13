const crypto = require('node:crypto');
const express = require('express');

const BUCKET = 'aigc-1257258774';
const OWNER = 'x-cos-meta-aigc-cli-owner';
const UPLOAD = 'x-cos-meta-aigc-cli-upload';
const UUID_PREFIX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const failure = (status, message) => Object.assign(new Error(message), { httpStatus: status });

function validate(body, maxBytes) {
  const { key, size, contentType, expires = 86400, force = false } = body;
  if (typeof key !== 'string' || !key || Buffer.byteLength(key) > 1024
      || /[\\\x00-\x1f\x7f]/.test(key) || key.includes('://')
      || key.split('/').some(part => !part || part === '.' || part === '..')) {
    throw failure(400, 'COS 对象路径无效');
  }
  // Existing Shelf objects live under user UUIDs; generic uploads cannot enter them.
  if (UUID_PREFIX.test(key.split('/')[0])) throw failure(403, '不能上传到素材库专用目录');
  if (!Number.isSafeInteger(size) || size < 0 || size > maxBytes) {
    throw failure(400, `文件大小无效或超过服务端限制 ${maxBytes} 字节`);
  }
  if (typeof contentType !== 'string' || contentType.length > 200
      || !/^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/i.test(contentType)) {
    throw failure(400, 'Content-Type 无效');
  }
  if (!Number.isInteger(expires) || expires < 1 || expires > 604800 || typeof force !== 'boolean') {
    throw failure(400, '链接有效期或覆盖参数无效');
  }
  return { key, size, contentType, expires, force };
}

function createCosRouter({ cos, bucket, region, secret, maxBytes = 2 * 1024 ** 3, now = Date.now }) {
  const router = express.Router();
  const call = (method, params) => new Promise((resolve, reject) => {
    cos[method]({ Bucket: BUCKET, Region: region, ...params },
      (error, result) => error ? reject(error) : resolve(result));
  });
  const mac = encoded => crypto.createHmac('sha256', secret)
    .update('aigc-cos-cli-v1\n').update(encoded).digest();
  const seal = payload => {
    const encoded = Buffer.from(JSON.stringify(payload)).toString('base64url');
    return `${encoded}.${mac(encoded).toString('base64url')}`;
  };
  function unseal(token, userId) {
    if (typeof token !== 'string' || token.length > 8192) throw failure(400, '上传凭证无效');
    const [encoded, signature, extra] = token.split('.');
    const supplied = Buffer.from(signature || '', 'base64url');
    const expected = mac(encoded || '');
    if (extra !== undefined || supplied.length !== expected.length
        || !crypto.timingSafeEqual(supplied, expected)) throw failure(400, '上传凭证无效');
    let payload;
    try { payload = JSON.parse(Buffer.from(encoded, 'base64url').toString()); }
    catch { throw failure(400, '上传凭证无效'); }
    if (payload.userId !== userId) throw failure(403, '上传凭证不属于当前账号');
    if (payload.deadline <= now()) throw failure(400, '上传凭证已过期，请重新上传');
    return payload;
  }
  async function head(key) {
    try { return await call('headObject', { Key: key }); }
    catch (error) {
      if (Number(error.statusCode) === 404) return null;
      throw error;
    }
  }
  const handle = action => async (req, res) => {
    res.set('Cache-Control', 'no-store');
    try {
      if (bucket !== BUCKET || !region || !secret || !Number.isSafeInteger(maxBytes) || maxBytes < 1) {
        throw failure(503, '服务端 COS 配置不完整或桶不匹配');
      }
      await action(req, res);
    } catch (error) {
      // Never forward SDK error objects containing signed URLs or authorization.
      res.status(error.httpStatus || 502).json({
        error: error.httpStatus ? error.message : 'COS 请求失败，请稍后重试或检查服务端配置',
      });
    }
  };

  router.post('/upload-ticket', handle(async (req, res) => {
    const plan = validate(req.body || {}, Math.min(maxBytes, 5 * 1024 ** 3));
    const existing = await head(plan.key);
    if (existing && existing.headers?.[OWNER] !== req.user.id) {
      throw failure(403, '该路径已被占用，不能覆盖其他账号或非 CLI 创建的对象');
    }
    if (existing && !plan.force) throw failure(409, '已有同名对象；使用 --force 覆盖或指定其他目录');
    const nonce = crypto.randomUUID();
    const headers = {
      'Content-Type': plan.contentType,
      'Content-Length': String(plan.size),
      [OWNER]: req.user.id,
      [UPLOAD]: nonce,
      // Also enforce non-overwrite at COS to close the HEAD/PUT race.
      ...(!existing ? { 'x-cos-forbid-overwrite': 'true' } : {}),
    };
    const signed = await call('getObjectUrl', {
      Key: plan.key, Method: 'PUT', Sign: true, Expires: 3600, Headers: headers,
    });
    if (!signed?.Url) throw failure(502, '生成上传地址失败');
    const uploadToken = seal({
      ...plan, userId: req.user.id, nonce, deadline: now() + 86400 * 1000,
    });
    res.json({
      bucket: BUCKET, region, key: plan.key, size: plan.size,
      url: signed.Url, headers, uploadToken, expiresIn: 3600,
    });
  }));

  router.post('/finalize-upload', handle(async (req, res) => {
    const plan = unseal(req.body?.uploadToken, req.user.id);
    const result = await head(plan.key);
    if (!result) throw failure(404, '上传对象不存在，请重新上传');
    const headers = result.headers || {};
    if (headers[OWNER] !== req.user.id || headers[UPLOAD] !== plan.nonce) {
      throw failure(409, '对象与本次上传不匹配，请重新上传');
    }
    if (Number(headers['content-length']) !== plan.size
        || headers['content-type'] !== plan.contentType) {
      throw failure(409, '上传后文件大小或类型校验失败');
    }
    const signed = await call('getObjectUrl', {
      Key: plan.key, Method: 'GET', Sign: true, Expires: plan.expires,
    });
    if (!signed?.Url) throw failure(502, '生成访问地址失败');
    res.json({
      bucket: BUCKET, region, key: plan.key, size: plan.size, url: signed.Url,
      expiresIn: plan.expires, expiresAt: new Date(now() + plan.expires * 1000).toISOString(),
    });
  }));
  return router;
}

module.exports = { createCosRouter, validate };

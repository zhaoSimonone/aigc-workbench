#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const serverRequire = createRequire(path.resolve(__dirname, '../../../server/package.json'));
const ROOT = path.resolve(__dirname, '../../..');
const BUCKET = 'aigc-1257258774';

function configuration(plan, parse, env = process.env) {
  const candidates = plan.envFile
    ? [plan.envFile]
    : [
        path.join(os.homedir(), '.config/aigc-workbench/cos.env'),
        path.join(ROOT, 'server/.env'),
        path.join(ROOT, '.env'),
      ];
  const file = candidates.find(candidate => fs.existsSync(candidate));
  if (plan.envFile && !file) throw new Error('COS 配置文件不存在');
  const config = { ...(file ? parse(fs.readFileSync(file)) : {}), ...env };
  if (!config.TENCENT_COS_SECRET_ID || !config.TENCENT_COS_SECRET_KEY) {
    throw new Error('缺少 COS 密钥。请在 ~/.config/aigc-workbench/cos.env 或环境变量中配置 TENCENT_COS_SECRET_ID 和 TENCENT_COS_SECRET_KEY');
  }
  const region = plan.region || config.TENCENT_COS_REGION || 'ap-shanghai';
  if (!/^[a-z]+-[a-z]+(?:-\d+)?$/.test(region)) throw new Error('COS 地域格式无效');
  return {
    region,
    SecretId: config.TENCENT_COS_SECRET_ID,
    SecretKey: config.TENCENT_COS_SECRET_KEY,
    SecurityToken: config.TENCENT_COS_SECURITY_TOKEN || undefined,
  };
}

function call(cos, method, params) {
  return new Promise((resolve, reject) => {
    cos[method](params, (error, result) => error ? reject(error) : resolve(result));
  });
}

async function upload(cos, plan, region, onProgress) {
  const object = { Bucket: BUCKET, Region: region, Key: plan.key };
  if (!plan.force) {
    let exists = true;
    try {
      await call(cos, 'headObject', object);
    } catch (error) {
      if (Number(error.statusCode) !== 404) throw error;
      exists = false;
    }
    if (exists) throw new Error('COS 中已有同名对象；使用 --force 允许覆盖，或指定其他目录');
  }
  await call(cos, 'uploadFile', {
    ...object,
    FilePath: plan.file,
    ContentType: plan.contentType,
    SliceSize: 8 * 1024 * 1024,
    ChunkSize: 8 * 1024 * 1024,
    AsyncLimit: 3,
    onProgress,
  });
  // Confirm the completed object before returning a downloadable link.
  const head = await call(cos, 'headObject', object);
  if (Number(head.headers?.['content-length']) !== plan.size) {
    throw new Error('上传后大小校验失败；请检查 COS 对象，不返回成功链接');
  }
  const signedAt = Date.now();
  const signed = await call(cos, 'getObjectUrl', {
    ...object, Method: 'GET', Sign: true, Expires: plan.expires,
  });
  return {
    bucket: BUCKET, region, key: plan.key, size: plan.size,
    url: signed.Url,
    expiresAt: new Date(signedAt + plan.expires * 1000).toISOString(),
    expiresIn: plan.expires,
  };
}

async function main() {
  let COS;
  let dotenv;
  try {
    COS = serverRequire('cos-nodejs-sdk-v5');
    dotenv = serverRequire('dotenv');
  } catch {
    throw new Error('缺少 COS SDK 依赖，请在仓库执行 npm ci --prefix server');
  }
  const plan = JSON.parse(fs.readFileSync(0, 'utf8'));
  const { region, ...credentials } = configuration(plan, dotenv.parse);
  const cos = new COS({ ...credentials, Protocol: 'https:', Timeout: 60000 });
  console.error(`上传 ${plan.file}\n目标 cos://${BUCKET}/${plan.key}`);
  let lastProgress = 0;
  const result = await upload(cos, plan, region, progress => {
    const now = Date.now();
    if (now - lastProgress < 2000 && progress.percent !== 1) return;
    lastProgress = now;
    console.error(`上传进度 ${(progress.percent * 100).toFixed(1)}%`);
  });
  if (plan.json) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.error(`已上传 ${result.size} 字节，链接有效期 ${result.expiresIn} 秒（临时密钥失效时可能提前失效）`);
    console.log(result.url);
  }
}

if (require.main === module) {
  main().catch(error => {
    // SDK errors may contain authorization headers; never print the raw object.
    const message = error.code || error.name === 'Error' && error.message || 'COS 请求失败';
    console.error(`失败：${message}${error.statusCode ? ` (HTTP ${error.statusCode})` : ''}`);
    process.exitCode = 1;
  });
}

module.exports = { configuration, upload };

const path = require('path');
const { Pool } = require('pg');
const COS = require('cos-nodejs-sdk-v5');
require('dotenv').config({ path: process.env.DOTENV_CONFIG_PATH || path.resolve(process.cwd(), '.env') });

const connectionString = process.env.DATABASE_URL;
const bucket = process.env.TENCENT_COS_BUCKET;
const region = process.env.TENCENT_COS_REGION;
const secretId = process.env.TENCENT_COS_SECRET_ID;
const secretKey = process.env.TENCENT_COS_SECRET_KEY;
const mediaCacheControl = 'private, max-age=86400, immutable';

if (!connectionString || !bucket || !region || !secretId || !secretKey) {
  throw new Error('Missing DATABASE_URL or Tencent COS configuration');
}

const pool = new Pool({ connectionString });
const cos = new COS({ SecretId: secretId, SecretKey: secretKey, Protocol: 'https:' });

function encodeCosSegment(segment) {
  return encodeURIComponent(segment).replace(/[!'()*]/g, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
}

function replaceCacheMetadata(key, host) {
  return new Promise((resolve, reject) => {
    // 当前目标对象只包括应用生成的 JPG 缩略图和 JPG 头像；若未来支持其他格式，
    // 应先读取并保留原 Content-Type，不能继续硬编码 image/jpeg。
    cos.putObjectCopy({
      Bucket: bucket,
      Region: region,
      Key: key,
      CopySource: `${host}/${key.split('/').map(encodeCosSegment).join('/')}`,
      MetadataDirective: 'Replaced',
      ContentType: 'image/jpeg',
      CacheControl: mediaCacheControl,
    }, (error, data) => (error ? reject(error) : resolve(data)));
  });
}

async function main() {
  const dryRun = process.argv.includes('--dry-run');
  const { rows } = await pool.query(`
    SELECT thumb_key AS key
      FROM assets
     WHERE thumb_key IS NOT NULL AND deleted_at IS NULL
    UNION
    SELECT avatar_key AS key
      FROM video_accounts
     WHERE avatar_key IS NOT NULL
    ORDER BY key
  `);
  const host = `${bucket}.cos.${region}.myqcloud.com`;
  console.log(`Found ${rows.length} thumbnail/avatar object(s)${dryRun ? ' (dry run)' : ''}`);

  if (dryRun) {
    for (const row of rows.slice(0, 50)) console.log(`[dry-run] ${row.key}`);
    if (rows.length > 50) console.log(`[dry-run] ... and ${rows.length - 50} more`);
    return;
  }

  let done = 0;
  let failed = 0;
  for (const row of rows) {
    try {
      await replaceCacheMetadata(row.key, host);
      done += 1;
    } catch (error) {
      failed += 1;
      console.error(`Failed ${row.key}: ${error.message}`);
    }
    if ((done + failed) % 50 === 0) console.log(`progress: ${done + failed}/${rows.length}`);
  }

  console.log(`Done. ok=${done} failed=${failed}`);
  if (failed) process.exitCode = 1;
}

main()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => pool.end());

const crypto = require('crypto');
const fs = require('fs');
const fsp = fs.promises;
const os = require('os');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');
const { Pool } = require('pg');
const COS = require('cos-nodejs-sdk-v5');
const ffmpegPath = require('@ffmpeg-installer/ffmpeg').path;
const ffprobePath = require('@ffprobe-installer/ffprobe').path;
require('dotenv').config({ path: process.env.DOTENV_CONFIG_PATH || path.resolve(process.cwd(), '.env') });

const execFileAsync = promisify(execFile);
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const bucket = process.env.TENCENT_COS_BUCKET;
const region = process.env.TENCENT_COS_REGION || 'ap-shanghai';
const cos = new COS({ SecretId: process.env.TENCENT_COS_SECRET_ID, SecretKey: process.env.TENCENT_COS_SECRET_KEY });

function getObjectToFile(key, output) {
  return new Promise((resolve, reject) => {
    cos.getObject({ Bucket: bucket, Region: region, Key: key, Output: output }, (error, data) => error ? reject(error) : resolve(data));
  });
}

function putObject(key, filePath, contentType) {
  return new Promise((resolve, reject) => {
    cos.putObject({ Bucket: bucket, Region: region, Key: key, Body: fs.createReadStream(filePath), ContentType: contentType }, (error, data) => error ? reject(error) : resolve(data));
  });
}

async function generatePreview(input, output) {
  const [{ stdout }] = await Promise.all([
    execFileAsync(ffprobePath, ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', input], { timeout: 60000 }),
    execFileAsync(ffmpegPath, ['-y', '-i', input, '-frames:v', '1', '-vf', 'scale=min\\(720\\,iw\\):-2', '-q:v', '4', output], { timeout: 120000 }),
  ]);
  const duration = Number.parseFloat(String(stdout).trim());
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

async function main() {
  const { rows } = await pool.query("SELECT id, object_key FROM assets WHERE type='video' AND thumb_key IS NULL AND deleted_at IS NULL ORDER BY created_at ASC");
  console.log(`Found ${rows.length} video(s) without previews`);
  for (const row of rows) {
    const base = path.join(os.tmpdir(), `aigc-backfill-${crypto.randomUUID()}`);
    const input = `${base}.video`;
    const output = `${base}.jpg`;
    try {
      await getObjectToFile(row.object_key, input);
      const duration = await generatePreview(input, output);
      const thumbKey = `${row.object_key}.jpg`;
      await putObject(thumbKey, output, 'image/jpeg');
      await pool.query('UPDATE assets SET thumb_key=$1, duration_seconds=$2 WHERE id=$3', [thumbKey, duration, row.id]);
      console.log(`Backfilled ${row.id}`);
    } catch (error) {
      console.error(`Failed ${row.id}: ${error.message}`);
    } finally {
      await Promise.all([fsp.unlink(input).catch(() => {}), fsp.unlink(output).catch(() => {})]);
    }
  }
}

main().finally(() => pool.end());

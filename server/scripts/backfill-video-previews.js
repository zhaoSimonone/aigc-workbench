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
const region = process.env.TENCENT_COS_REGION;
const secretId = process.env.TENCENT_COS_SECRET_ID;
const secretKey = process.env.TENCENT_COS_SECRET_KEY;
const mediaCacheControl = 'private, max-age=86400, immutable';

if (!process.env.DATABASE_URL || !bucket || !region || !secretId || !secretKey) {
  throw new Error('Missing DATABASE_URL or Tencent COS configuration');
}

const cos = new COS({ SecretId: secretId, SecretKey: secretKey, Protocol: 'https:' });

function getObjectToFile(key, output) {
  return new Promise((resolve, reject) => {
    cos.getObject({ Bucket: bucket, Region: region, Key: key, Output: output }, (error, data) => error ? reject(error) : resolve(data));
  });
}

function putObject(key, filePath, contentType, cacheControl) {
  return new Promise((resolve, reject) => {
    cos.putObject({
      Bucket: bucket,
      Region: region,
      Key: key,
      Body: fs.createReadStream(filePath),
      ContentType: contentType,
      ...(cacheControl ? { CacheControl: cacheControl } : {}),
    }, (error, data) => error ? reject(error) : resolve(data));
  });
}

async function readVideoDuration(input) {
  const { stdout } = await execFileAsync(ffprobePath, ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', input], { timeout: 60000 });
  const duration = Number.parseFloat(String(stdout).trim());
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

function scoreGrayFrame(buffer) {
  if (!buffer || !buffer.length) return { mean: 0, std: 0 };
  let sum = 0;
  for (const value of buffer) sum += value;
  const mean = sum / buffer.length;
  let variance = 0;
  for (const value of buffer) variance += (value - mean) ** 2;
  return { mean, std: Math.sqrt(variance / buffer.length) };
}

async function extractGrayFrame(input, seek) {
  const { stdout } = await execFileAsync(ffmpegPath, ['-ss', String(seek), '-i', input, '-frames:v', '1', '-vf', 'scale=32:32', '-pix_fmt', 'gray', '-f', 'rawvideo', '-'], { timeout: 30000, maxBuffer: 1024 * 1024, encoding: 'buffer' });
  return stdout;
}

async function pickPreviewTime(input, duration) {
  const limit = Math.max(0, duration - 0.05);
  const fractions = [0.02, 0.08, 0.15, 0.25, 0.35, 0.5, 0.65, 0.8];
  const seen = new Set();
  let best = null;
  for (const fraction of fractions) {
    const seek = Math.round(Math.min(duration * fraction, limit) * 100) / 100;
    if (seen.has(seek)) continue;
    seen.add(seek);
    let scored;
    try {
      scored = scoreGrayFrame(await extractGrayFrame(input, seek));
    } catch (_) {
      continue;
    }
    if (!best || scored.std > best.std) best = { seek, ...scored };
    if (scored.std >= 16 && scored.mean >= 18 && scored.mean <= 238) return seek;
  }
  return best ? best.seek : 0;
}

async function generatePreview(input, output, type) {
  if (type === 'image') {
    await execFileAsync(ffmpegPath, ['-y', '-i', input, '-vf', 'scale=min\\(480\\,iw\\):-2', '-frames:v', '1', '-q:v', '4', output], { timeout: 60000 });
    return null;
  }
  const duration = await readVideoDuration(input);
  const seek = duration && duration > 0.2 ? await pickPreviewTime(input, duration) : 0;
  await execFileAsync(ffmpegPath, ['-y', '-ss', String(seek), '-i', input, '-frames:v', '1', '-vf', 'scale=min\\(720\\,iw\\):-2', '-q:v', '4', output], { timeout: 120000 });
  return duration;
}

async function main() {
  const regenerate = process.argv.includes('--regenerate');
  const dryRun = process.argv.includes('--dry-run');
  const { rows } = await pool.query(
    regenerate
      ? "SELECT id, type, object_key FROM assets WHERE deleted_at IS NULL AND type IN ('video','image') ORDER BY created_at ASC"
      : "SELECT id, type, object_key FROM assets WHERE deleted_at IS NULL AND thumb_key IS NULL AND type IN ('video','image') ORDER BY created_at ASC",
  );
  console.log(`${regenerate ? 'Regenerating' : 'Found'} ${rows.length} asset(s)${regenerate ? ' (all previews)' : ' without previews'}`);
  if (dryRun) {
    for (const row of rows.slice(0, 20)) console.log(`[dry-run] ${row.type} ${row.id} ${row.object_key}`);
    if (rows.length > 20) console.log(`[dry-run] ... and ${rows.length - 20} more`);
    return;
  }
  for (const row of rows) {
    const base = path.join(os.tmpdir(), `aigc-backfill-${crypto.randomUUID()}`);
    const input = `${base}.source`;
    const output = `${base}.jpg`;
    try {
      await getObjectToFile(row.object_key, input);
      const duration = await generatePreview(input, output, row.type);
      const thumbKey = `${row.object_key}.jpg`;
      await putObject(thumbKey, output, 'image/jpeg', mediaCacheControl);
      await pool.query('UPDATE assets SET thumb_key=$1, duration_seconds=$2 WHERE id=$3', [thumbKey, duration, row.id]);
      console.log(`Backfilled ${row.type} ${row.id}`);
    } catch (error) {
      console.error(`Failed ${row.id}: ${error.message}`);
    } finally {
      await Promise.all([fsp.unlink(input).catch(() => {}), fsp.unlink(output).catch(() => {})]);
    }
  }
}

main().finally(() => pool.end());

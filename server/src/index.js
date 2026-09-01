const crypto = require('crypto');
const fs = require('fs');
const fsp = fs.promises;
const os = require('os');
const path = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');
const express = require('express');
const multer = require('multer');
const bcrypt = require('bcryptjs');
const { Pool } = require('pg');
const COS = require('cos-nodejs-sdk-v5');
const ffmpegPath = require('@ffmpeg-installer/ffmpeg').path;
const ffprobePath = require('@ffprobe-installer/ffprobe').path;
require('dotenv').config({ path: process.env.DOTENV_CONFIG_PATH || path.resolve(process.cwd(), '.env') });

const app = express();
const port = Number(process.env.PORT || 18080);
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const uploadDir = process.env.UPLOAD_TMP_DIR || path.join(os.tmpdir(), 'aigc-shelf-uploads');
const sessionDays = Number(process.env.SESSION_DAYS || 30);
const bucket = process.env.TENCENT_COS_BUCKET;
const region = process.env.TENCENT_COS_REGION || 'ap-guangzhou';
const cos = new COS({
  SecretId: process.env.TENCENT_COS_SECRET_ID,
  SecretKey: process.env.TENCENT_COS_SECRET_KEY,
  Protocol: 'https:',
});
const execFileAsync = promisify(execFile);

app.disable('x-powered-by');
app.use(express.json({ limit: '1mb' }));
app.use((req, res, next) => {
  res.setHeader('Cache-Control', 'no-store');
  next();
});

const upload = multer({
  dest: uploadDir,
  limits: { fileSize: Number(process.env.MAX_UPLOAD_BYTES || 2 * 1024 * 1024 * 1024) },
});

function hashToken(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function readCookie(req, name) {
  const header = req.headers.cookie || '';
  const match = header.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : '';
}

function setSessionCookie(res, token, maxAge) {
  const flags = [
    `aigc_session=${encodeURIComponent(token)}`,
    `Max-Age=${maxAge}`,
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
  ];
  if (process.env.NODE_ENV === 'production') flags.push('Secure');
  res.setHeader('Set-Cookie', flags.join('; '));
}

async function requireUser(req, res, next) {
  try {
    const token = readCookie(req, 'aigc_session');
    if (!token) return res.status(401).json({ error: '未登录' });
    const { rows } = await pool.query(
      `SELECT u.id, u.name, u.email
         FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = $1 AND s.expires_at > now()`,
      [hashToken(token)],
    );
    if (!rows[0]) return res.status(401).json({ error: '登录已过期' });
    req.user = rows[0];
    next();
  } catch (error) {
    next(error);
  }
}

function publicUser(row) {
  return { id: row.id, name: row.name, email: row.email };
}

function objectUrl(key) {
  return new Promise((resolve, reject) => {
    cos.getObjectUrl({ Bucket: bucket, Region: region, Key: key, Sign: true, Expires: 3600 }, (err, data) => {
      if (err) reject(err);
      else resolve(data.Url);
    });
  });
}

function putObject(key, filePath, contentType) {
  return new Promise((resolve, reject) => {
    cos.putObject({
      Bucket: bucket,
      Region: region,
      Key: key,
      Body: fs.createReadStream(filePath),
      ContentType: contentType,
    }, (err, data) => (err ? reject(err) : resolve(data)));
  });
}

function headObject(key) {
  return new Promise((resolve, reject) => {
    cos.headObject({ Bucket: bucket, Region: region, Key: key }, (err, data) => (err ? reject(err) : resolve(data)));
  });
}

function deleteObject(key) {
  if (!key) return Promise.resolve();
  return new Promise((resolve, reject) => {
    cos.deleteObject({ Bucket: bucket, Region: region, Key: key }, (err, data) => (err ? reject(err) : resolve(data)));
  });
}

function hashFile(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('error', reject);
    stream.on('end', () => resolve(hash.digest('hex')));
  });
}

async function inspectVideo(filePath, previewPath) {
  const [{ stdout: durationOutput }] = await Promise.all([
    execFileAsync(ffprobePath, ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', filePath], { timeout: 60000 }),
    execFileAsync(ffmpegPath, ['-y', '-i', filePath, '-frames:v', '1', '-vf', 'scale=min\\(720\\,iw\\):-2', '-q:v', '4', previewPath], { timeout: 120000 }),
  ]);
  const duration = Number.parseFloat(String(durationOutput).trim());
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

function parseTags(value) {
  if (Array.isArray(value)) return [...new Set(value.map(String).map((item) => item.trim()).filter(Boolean))].slice(0, 30);
  try {
    const parsed = JSON.parse(value || '[]');
    if (Array.isArray(parsed)) return parseTags(parsed);
  } catch (_) {}
  return String(value || '').split(/[,，\\s]+/).map((item) => item.trim()).filter(Boolean).slice(0, 30);
}

function safeName(name) {
  return String(name || '未命名素材').replace(/[\\\\/]+/g, '_').slice(0, 180) || '未命名素材';
}

async function hydrateAsset(row) {
  const [url, thumbUrl] = await Promise.all([
    row.type === 'video' ? Promise.resolve(`/api/assets/${row.id}/stream`) : objectUrl(row.object_key),
    row.thumb_key ? objectUrl(row.thumb_key) : Promise.resolve(''),
  ]);
  return {
    id: row.id,
    name: row.name,
    type: row.type,
    source: row.source,
    sourceUrl: row.source_url,
    characterName: row.character_name || '',
    characterCategory: row.character_category || '',
    src: url,
    thumb: thumbUrl,
    duration: row.duration_seconds ? formatDuration(row.duration_seconds) : row.type === 'video' ? '待识别' : null,
    date: new Date(row.created_at).toISOString(),
    size: formatBytes(Number(row.size_bytes)),
    tags: row.tags || [],
    favorite: row.favorite,
    used: row.used,
    folder: row.folder,
    note: row.note,
    parentAssetIds: row.parent_asset_ids || [],
    derivedAssetIds: row.derived_asset_ids || [],
  };
}

async function assetQuery(id, userId) {
  const result = await pool.query(
    `SELECT a.*, COALESCE(array_agg(DISTINCT t.name) FILTER (WHERE t.name IS NOT NULL), '{}') AS tags,
        COALESCE(array_agg(DISTINCT r.source_asset_id) FILTER (WHERE r.source_asset_id IS NOT NULL), '{}') AS parent_asset_ids,
        COALESCE(array_agg(DISTINCT d.derived_asset_id) FILTER (WHERE d.derived_asset_id IS NOT NULL), '{}') AS derived_asset_ids
       FROM assets a
       LEFT JOIN asset_tags at ON at.asset_id=a.id
       LEFT JOIN tags t ON t.id=at.tag_id
       LEFT JOIN asset_relations r ON r.derived_asset_id=a.id
       LEFT JOIN asset_relations d ON d.source_asset_id=a.id
      WHERE a.id=$1 AND a.user_id=$2 AND a.deleted_at IS NULL
      GROUP BY a.id`,
    [id, userId],
  );
  return result.rows[0];
}

function formatBytes(bytes) {
  if (!bytes) return '0 KB';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds)));
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

app.get('/api/health', async (_req, res) => {
  try {
    await pool.query('SELECT 1');
    res.json({ ok: true });
  } catch (error) {
    res.status(503).json({ ok: false });
  }
});

app.get('/api/auth/me', requireUser, (req, res) => res.json({ user: publicUser(req.user) }));

app.post('/api/auth/register', async (req, res, next) => {
  try {
    const name = String(req.body.name || '').trim();
    const email = String(req.body.email || '').trim().toLowerCase();
    const password = String(req.body.password || '');
    if (!name || !email || password.length < 6) return res.status(400).json({ error: '请完整填写信息，密码至少需要 6 位' });
    const passwordHash = await bcrypt.hash(password, 12);
    const { rows } = await pool.query(
      'INSERT INTO users(name,email,password_hash) VALUES($1,$2,$3) RETURNING id,name,email',
      [name, email, passwordHash],
    );
    const token = crypto.randomBytes(32).toString('hex');
    await pool.query("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES($1,$2,now()+($3 * interval '1 day'))", [hashToken(token), rows[0].id, sessionDays]);
    setSessionCookie(res, token, sessionDays * 86400);
    res.status(201).json({ user: publicUser(rows[0]) });
  } catch (error) {
    if (error.code === '23505') return res.status(409).json({ error: '该邮箱已注册，请直接登录' });
    next(error);
  }
});

app.post('/api/auth/login', async (req, res, next) => {
  try {
    const email = String(req.body.email || '').trim().toLowerCase();
    const password = String(req.body.password || '');
    const { rows } = await pool.query('SELECT id,name,email,password_hash FROM users WHERE email=$1', [email]);
    if (!rows[0] || !(await bcrypt.compare(password, rows[0].password_hash))) return res.status(401).json({ error: '邮箱或密码不正确' });
    const token = crypto.randomBytes(32).toString('hex');
    await pool.query("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES($1,$2,now()+($3 * interval '1 day'))", [hashToken(token), rows[0].id, sessionDays]);
    setSessionCookie(res, token, sessionDays * 86400);
    res.json({ user: publicUser(rows[0]) });
  } catch (error) {
    next(error);
  }
});

app.post('/api/auth/logout', async (req, res, next) => {
  try {
    const token = readCookie(req, 'aigc_session');
    if (token) await pool.query('DELETE FROM sessions WHERE token_hash=$1', [hashToken(token)]);
    setSessionCookie(res, '', 0);
    res.status(204).end();
  } catch (error) {
    next(error);
  }
});

app.get('/api/character-albums', requireUser, async (req, res, next) => {
  try {
    const { rows } = await pool.query(
      `SELECT ca.id, ca.name, ca.created_at, COUNT(a.id)::int AS asset_count
         FROM character_albums ca
         LEFT JOIN assets a ON a.user_id=ca.user_id AND a.character_name=ca.name AND a.folder='角色设定' AND a.deleted_at IS NULL
        WHERE ca.user_id=$1
        GROUP BY ca.id
        ORDER BY ca.created_at ASC, ca.name ASC`,
      [req.user.id],
    );
    res.json({ albums: rows.map((row) => ({ id: row.id, name: row.name, assetCount: row.asset_count, createdAt: row.created_at })) });
  } catch (error) {
    next(error);
  }
});

app.post('/api/character-albums', requireUser, async (req, res, next) => {
  try {
    const name = String(req.body.name || '').trim().slice(0, 120);
    if (!name) return res.status(400).json({ error: '人物相册名称不能为空' });
    const { rows } = await pool.query(
      'INSERT INTO character_albums(user_id,name) VALUES($1,$2) RETURNING id,name,created_at',
      [req.user.id, name],
    );
    res.status(201).json({ album: { id: rows[0].id, name: rows[0].name, assetCount: 0, createdAt: rows[0].created_at } });
  } catch (error) {
    if (error.code === '23505') return res.status(409).json({ error: '这个人物相册已经存在' });
    next(error);
  }
});

app.get('/api/assets', requireUser, async (req, res, next) => {
  try {
    const { rows } = await pool.query(
      `SELECT a.*, COALESCE(array_agg(DISTINCT t.name) FILTER (WHERE t.name IS NOT NULL), '{}') AS tags,
          COALESCE(array_agg(DISTINCT r.source_asset_id) FILTER (WHERE r.source_asset_id IS NOT NULL), '{}') AS parent_asset_ids,
          COALESCE(array_agg(DISTINCT d.derived_asset_id) FILTER (WHERE d.derived_asset_id IS NOT NULL), '{}') AS derived_asset_ids
         FROM assets a
         LEFT JOIN asset_tags at ON at.asset_id=a.id
         LEFT JOIN tags t ON t.id=at.tag_id
         LEFT JOIN asset_relations r ON r.derived_asset_id=a.id
         LEFT JOIN asset_relations d ON d.source_asset_id=a.id
        WHERE a.user_id=$1 AND a.deleted_at IS NULL
        GROUP BY a.id
        ORDER BY a.created_at DESC`,
      [req.user.id],
    );
    res.json({ assets: await Promise.all(rows.map(hydrateAsset)) });
  } catch (error) {
    next(error);
  }
});

app.get('/api/assets/:id/stream', requireUser, async (req, res, next) => {
  try {
    const row = await assetQuery(req.params.id, req.user.id);
    if (!row) return res.status(404).json({ error: '素材不存在' });
    const metadata = await headObject(row.object_key);
    const total = Number(metadata.headers?.['content-length'] || row.size_bytes || 0);
    const range = req.headers.range;
    let start = 0;
    let end = Math.max(0, total - 1);
    if (range) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range);
      if (!match || (!match[1] && !match[2])) return res.status(416).set('Content-Range', `bytes */${total}`).end();
      if (match[1]) start = Number(match[1]);
      if (match[2]) end = Number(match[2]);
      else end = Math.min(total - 1, start + 4 * 1024 * 1024 - 1);
      if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start >= total || end < start) {
        return res.status(416).set('Content-Range', `bytes */${total}`).end();
      }
      end = Math.min(end, total - 1);
    }
    const length = end - start + 1;
    res.status(range ? 206 : 200);
    res.set({
      'Content-Type': row.content_type || 'video/mp4',
      'Content-Length': String(length),
      'Accept-Ranges': 'bytes',
      'Content-Disposition': 'inline',
      ...(range ? { 'Content-Range': `bytes ${start}-${end}/${total}` } : {}),
    });
    const headers = range ? { Range: `bytes=${start}-${end}` } : {};
    cos.getObject({ Bucket: bucket, Region: region, Key: row.object_key, Headers: headers, Output: res }, (error) => {
      if (error && !res.headersSent) next(error);
    });
  } catch (error) {
    next(error);
  }
});

app.get('/api/assets/:id/download', requireUser, async (req, res, next) => {
  try {
    const row = await assetQuery(req.params.id, req.user.id);
    if (!row) return res.status(404).json({ error: '素材不存在' });
    const metadata = await headObject(row.object_key);
    const total = Number(metadata.headers?.['content-length'] || row.size_bytes || 0);
    const objectFilename = path.basename(row.object_key).replace(/^[0-9a-f-]{36}-/i, '');
    const filename = objectFilename || `${safeName(row.name)}${row.type === 'video' ? '.mp4' : ''}`;
    const encodedFilename = encodeURIComponent(filename).replace(/[!'()*]/g, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
    res.set({
      'Content-Type': row.content_type || 'application/octet-stream',
      'Content-Length': String(total),
      'Content-Disposition': `attachment; filename="download"; filename*=UTF-8''${encodedFilename}`,
    });
    cos.getObject({ Bucket: bucket, Region: region, Key: row.object_key, Output: res }, (error) => {
      if (error && !res.headersSent) next(error);
    });
  } catch (error) {
    next(error);
  }
});

app.post('/api/assets', requireUser, upload.single('file'), async (req, res, next) => {
  let tempPath = req.file && req.file.path;
  try {
    if (!req.file) return res.status(400).json({ error: '请选择文件' });
    const type = req.file.mimetype.startsWith('image/') ? 'image' : req.file.mimetype.startsWith('video/') ? 'video' : '';
    if (!type) return res.status(415).json({ error: '仅支持图片和视频文件' });
    const tags = parseTags(req.body.tags);
    const source = String(req.body.source || '本地导入').trim().slice(0, 120) || '本地导入';
    const sourceUrl = String(req.body.sourceUrl || '').trim().slice(0, 2000);
    const characterName = String(req.body.characterName || '').trim().slice(0, 120);
    const requestedFolder = String(req.body.folder || '灵感收集').trim();
    const folder = (requestedFolder === '成片' ? '我的创作' : requestedFolder).slice(0, 80) || '灵感收集';
    const characterCategory = String(req.body.characterCategory || '').trim().slice(0, 40);
    const name = safeName(String(req.body.name || path.basename(req.file.originalname, path.extname(req.file.originalname))));
    const objectKey = `${req.user.id}/${type}/${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}-${safeName(req.file.originalname)}`;
    await putObject(objectKey, tempPath, req.file.mimetype);
    let thumbKey = null;
    let durationSeconds = null;
    if (type === 'video') {
      const previewPath = `${tempPath}.jpg`;
      try {
        durationSeconds = await inspectVideo(tempPath, previewPath);
        thumbKey = `${objectKey}.jpg`;
        await putObject(thumbKey, previewPath, 'image/jpeg');
      } catch (error) {
        console.warn('Video preview generation skipped:', error.message);
      } finally {
        await fsp.unlink(previewPath).catch(() => {});
      }
    }
    const sha256 = await hashFile(tempPath);
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      if (folder === '角色设定' && characterName) {
        await client.query(
          'INSERT INTO character_albums(user_id,name) VALUES($1,$2) ON CONFLICT(user_id,name) DO NOTHING',
          [req.user.id, characterName],
        );
      }
      const { rows } = await client.query(
        `INSERT INTO assets(user_id,name,type,source,source_url,character_name,character_category,object_key,thumb_key,content_type,size_bytes,sha256,duration_seconds,folder,used,note)
         VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) RETURNING *`,
        [req.user.id, name, type, source, sourceUrl, characterName, characterCategory, objectKey, thumbKey, req.file.mimetype, req.file.size, sha256, durationSeconds, folder, req.body.used === 'true', '新上传素材，等待补充备注。'],
      );
      for (const tag of tags.length ? tags : ['待整理']) {
        const tagRow = await client.query('INSERT INTO tags(user_id,name) VALUES($1,$2) ON CONFLICT(user_id,name) DO UPDATE SET name=EXCLUDED.name RETURNING id', [req.user.id, tag]);
        await client.query('INSERT INTO asset_tags(asset_id,tag_id) VALUES($1,$2) ON CONFLICT DO NOTHING', [rows[0].id, tagRow.rows[0].id]);
      }
      const parentIds = parseTags(req.body.parentAssetIds);
      for (const parentId of parentIds) {
        await client.query('INSERT INTO asset_relations(source_asset_id,derived_asset_id) SELECT id,$1 FROM assets WHERE id=$2 AND user_id=$3 ON CONFLICT DO NOTHING', [rows[0].id, parentId, req.user.id]);
      }
      await client.query('COMMIT');
      const hydrated = await pool.query(`SELECT a.*, COALESCE(array_agg(DISTINCT t.name) FILTER (WHERE t.name IS NOT NULL), '{}') AS tags, COALESCE(array_agg(DISTINCT r.source_asset_id) FILTER (WHERE r.source_asset_id IS NOT NULL), '{}') AS parent_asset_ids, COALESCE(array_agg(DISTINCT d.derived_asset_id) FILTER (WHERE d.derived_asset_id IS NOT NULL), '{}') AS derived_asset_ids FROM assets a LEFT JOIN asset_tags at ON at.asset_id=a.id LEFT JOIN tags t ON t.id=at.tag_id LEFT JOIN asset_relations r ON r.derived_asset_id=a.id LEFT JOIN asset_relations d ON d.source_asset_id=a.id WHERE a.id=$1 GROUP BY a.id`, [rows[0].id]);
      res.status(201).json({ asset: await hydrateAsset(hydrated.rows[0]) });
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    next(error);
  } finally {
    if (tempPath) await fsp.unlink(tempPath).catch(() => {});
  }
});

app.post('/api/assets/:id/file', requireUser, upload.single('file'), async (req, res, next) => {
  let tempPath = req.file && req.file.path;
  let uploadedKey = '';
  let uploadedThumbKey = '';
  try {
    if (!req.file) return res.status(400).json({ error: '请选择要替换的文件' });
    const current = await assetQuery(req.params.id, req.user.id);
    if (!current) return res.status(404).json({ error: '素材不存在' });
    const type = req.file.mimetype.startsWith('image/') ? 'image' : req.file.mimetype.startsWith('video/') ? 'video' : '';
    if (!type) return res.status(415).json({ error: '仅支持图片和视频文件' });
    if (type !== current.type) return res.status(400).json({ error: `只能替换为同类型${current.type === 'video' ? '视频' : '图片'}文件` });

    uploadedKey = `${req.user.id}/${type}/${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}-${safeName(req.file.originalname)}`;
    await putObject(uploadedKey, tempPath, req.file.mimetype);
    let thumbKey = null;
    let durationSeconds = null;
    if (type === 'video') {
      const previewPath = `${tempPath}.jpg`;
      try {
        durationSeconds = await inspectVideo(tempPath, previewPath);
        thumbKey = `${uploadedKey}.jpg`;
        uploadedThumbKey = thumbKey;
        await putObject(thumbKey, previewPath, 'image/jpeg');
      } catch (error) {
        console.warn('Replacement video preview generation skipped:', error.message);
      } finally {
        await fsp.unlink(previewPath).catch(() => {});
      }
    }
    const sha256 = await hashFile(tempPath);
    const { rows } = await pool.query(
      `UPDATE assets
          SET object_key=$1, thumb_key=$2, content_type=$3, size_bytes=$4, sha256=$5, duration_seconds=$6
        WHERE id=$7 AND user_id=$8 AND deleted_at IS NULL
        RETURNING *`,
      [uploadedKey, thumbKey, req.file.mimetype, req.file.size, sha256, durationSeconds, req.params.id, req.user.id],
    );
    if (!rows[0]) {
      await Promise.all([
        deleteObject(uploadedKey).catch(() => {}),
        deleteObject(uploadedThumbKey).catch(() => {}),
      ]);
      return res.status(404).json({ error: '素材不存在' });
    }
    const hydrated = await assetQuery(req.params.id, req.user.id);
    await Promise.all([
      deleteObject(current.object_key).catch((error) => console.warn('Old asset cleanup skipped:', error.message)),
      deleteObject(current.thumb_key).catch((error) => console.warn('Old thumbnail cleanup skipped:', error.message)),
    ]);
    res.json({ asset: await hydrateAsset(hydrated) });
  } catch (error) {
    await Promise.all([
      deleteObject(uploadedKey).catch(() => {}),
      deleteObject(uploadedThumbKey).catch(() => {}),
    ]);
    next(error);
  } finally {
    if (tempPath) await fsp.unlink(tempPath).catch(() => {});
  }
});

app.patch('/api/assets/:id', requireUser, async (req, res, next) => {
  try {
    const allowed = ['favorite', 'used', 'note', 'folder', 'name', 'source', 'sourceUrl', 'characterName', 'characterCategory'];
    const fields = [];
    const values = [];
    for (const key of allowed) {
      if (Object.prototype.hasOwnProperty.call(req.body, key)) {
        const dbKey = key === 'sourceUrl' ? 'source_url' : key === 'characterName' ? 'character_name' : key === 'characterCategory' ? 'character_category' : key;
        const value = key === 'characterName'
          ? String(req.body[key] || '').trim().slice(0, 120)
          : key === 'characterCategory'
            ? String(req.body[key] || '').trim().slice(0, 40)
            : key === 'folder' && String(req.body[key]) === '成片'
              ? '我的创作'
              : req.body[key];
        values.push(value);
        fields.push(`${dbKey}=$${values.length}`);
      }
    }
    if (!fields.length) return res.status(400).json({ error: '没有可更新字段' });
    values.push(req.params.id, req.user.id);
    const { rows } = await pool.query(`UPDATE assets SET ${fields.join(', ')} WHERE id=$${values.length - 1} AND user_id=$${values.length} AND deleted_at IS NULL RETURNING *`, values);
    if (!rows[0]) return res.status(404).json({ error: '素材不存在' });
    const hydrated = await assetQuery(rows[0].id, req.user.id);
    res.json({ asset: await hydrateAsset(hydrated) });
  } catch (error) {
    next(error);
  }
});

app.delete('/api/assets/:id', requireUser, async (req, res, next) => {
  try {
    const { rows } = await pool.query(
      'UPDATE assets SET deleted_at=now() WHERE id=$1 AND user_id=$2 AND deleted_at IS NULL RETURNING id',
      [req.params.id, req.user.id],
    );
    if (!rows[0]) return res.status(404).json({ error: '素材不存在' });
    res.status(204).end();
  } catch (error) {
    next(error);
  }
});

async function assertOwnedAsset(assetId, userId) {
  const { rows } = await pool.query(
    'SELECT id FROM assets WHERE id=$1 AND user_id=$2 AND deleted_at IS NULL',
    [assetId, userId],
  );
  return rows[0];
}

function publicComment(row) {
  return {
    id: row.id,
    assetId: row.asset_id,
    content: row.content,
    author: row.author_name,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

app.get('/api/assets/:id/comments', requireUser, async (req, res, next) => {
  try {
    if (!await assertOwnedAsset(req.params.id, req.user.id)) return res.status(404).json({ error: '素材不存在' });
    const { rows } = await pool.query(
      `SELECT c.*, u.name AS author_name
         FROM asset_comments c JOIN users u ON u.id=c.user_id
        WHERE c.asset_id=$1 AND c.user_id=$2
        ORDER BY c.created_at DESC`,
      [req.params.id, req.user.id],
    );
    res.json({ comments: rows.map(publicComment) });
  } catch (error) {
    next(error);
  }
});

app.post('/api/assets/:id/comments', requireUser, async (req, res, next) => {
  try {
    if (!await assertOwnedAsset(req.params.id, req.user.id)) return res.status(404).json({ error: '素材不存在' });
    const content = String(req.body.content || '').trim();
    if (!content) return res.status(400).json({ error: '评论内容不能为空' });
    if (content.length > 5000) return res.status(400).json({ error: '评论最多 5000 个字符' });
    const { rows } = await pool.query(
      `INSERT INTO asset_comments(asset_id,user_id,content)
       VALUES($1,$2,$3)
       RETURNING id,asset_id,content,created_at,updated_at`,
      [req.params.id, req.user.id, content],
    );
    res.status(201).json({ comment: publicComment({ ...rows[0], author_name: req.user.name }) });
  } catch (error) {
    next(error);
  }
});

app.patch('/api/comments/:id', requireUser, async (req, res, next) => {
  try {
    const content = String(req.body.content || '').trim();
    if (!content) return res.status(400).json({ error: '评论内容不能为空' });
    if (content.length > 5000) return res.status(400).json({ error: '评论最多 5000 个字符' });
    const { rows } = await pool.query(
      `UPDATE asset_comments c SET content=$1, updated_at=now()
        WHERE c.id=$2 AND c.user_id=$3
        RETURNING c.id,c.asset_id,c.content,c.created_at,c.updated_at`,
      [content, req.params.id, req.user.id],
    );
    if (!rows[0]) return res.status(404).json({ error: '评论不存在' });
    res.json({ comment: publicComment({ ...rows[0], author_name: req.user.name }) });
  } catch (error) {
    next(error);
  }
});

app.delete('/api/comments/:id', requireUser, async (req, res, next) => {
  try {
    const { rows } = await pool.query(
      'DELETE FROM asset_comments WHERE id=$1 AND user_id=$2 RETURNING id',
      [req.params.id, req.user.id],
    );
    if (!rows[0]) return res.status(404).json({ error: '评论不存在' });
    res.status(204).end();
  } catch (error) {
    next(error);
  }
});

app.post('/api/assets/:id/tags', requireUser, async (req, res, next) => {
  try {
    const tags = parseTags(req.body.tags);
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const exists = await client.query('SELECT id FROM assets WHERE id=$1 AND user_id=$2 AND deleted_at IS NULL', [req.params.id, req.user.id]);
      if (!exists.rows[0]) {
        await client.query('ROLLBACK');
        return res.status(404).json({ error: '素材不存在' });
      }
      await client.query('DELETE FROM asset_tags WHERE asset_id=$1', [req.params.id]);
      for (const tag of tags) {
        const row = await client.query('INSERT INTO tags(user_id,name) VALUES($1,$2) ON CONFLICT(user_id,name) DO UPDATE SET name=EXCLUDED.name RETURNING id', [req.user.id, tag]);
        await client.query('INSERT INTO asset_tags(asset_id,tag_id) VALUES($1,$2)', [req.params.id, row.rows[0].id]);
      }
      await client.query('COMMIT');
      res.status(204).end();
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    next(error);
  }
});

app.use((error, _req, res, _next) => {
  console.error(error);
  res.status(error.code === 'LIMIT_FILE_SIZE' ? 413 : 500).json({ error: error.code === 'LIMIT_FILE_SIZE' ? '文件超过 2 GB 限制' : '服务器内部错误' });
});

async function start() {
  await fsp.mkdir(uploadDir, { recursive: true });
  const schema = await fsp.readFile(path.join(__dirname, '..', 'schema.sql'), 'utf8');
  await pool.query(schema);
  app.listen(port, '0.0.0.0', () => console.log(`AIGC Shelf API listening on ${port}`));
}

start().catch((error) => {
  console.error(error);
  process.exit(1);
});

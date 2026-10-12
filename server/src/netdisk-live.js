// 百度网盘「实时目录」服务端能力，范围被刻意锁死：
//   1) 加密保存当前用户的 Access Token（AES-256-GCM，按用户隔离）；
//   2) 只调用 GET /api/list 读取 /AIGC 下一层目录；
//   3) 实时失败时降级到 netdisk_nodes 缓存。
//
// 本文件**不包含也不允许包含**任何网盘写操作：不上传、不下载、不删除、不创建目录、
// 不代理下载链接。需要这些能力时，请在浏览器外用本机 skills/baidu-netdisk-archive。
const crypto = require('crypto');

const { assertNetdiskPath, loadNetdiskConfig } = require('./netdisk-catalog');

const NETDISK_API = 'https://pan.baidu.com';
const PAGE_SIZE = 1000;
const MAX_ATTEMPTS = 4;
const RETRYABLE_ERRNO = new Set([-6, -8, -11, 112, 158]);
const REQUEST_TIMEOUT_MS = 8000;
const LEVEL_TIMEOUT_MS = 15000;
const CIPHER_VERSION = 'v1';
const TOKEN_MAX_LENGTH = 512;
const PASTE_MAX_LENGTH = 2048;
const DEFAULT_EXPIRES_IN = 2592000;
const EXPIRING_SOON_SECONDS = 3 * 24 * 3600;

const CREDENTIAL_PURPOSE = '仅用于读取百度网盘 /AIGC 目录列表，不会用此 Token 上传、下载或删除文件。';
const AUTHORIZE_URL = 'https://openapi.baidu.com/oauth/2.0/authorize?response_type=token&client_id=QHOuRXiepJBMjtk0esLhrPoNlQyYd0mF&redirect_uri=oob&scope=basic,netdisk&qrcode=1';

const IMAGE_EXT = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif', '.bmp', '.tiff', '.avif']);
const VIDEO_EXT = new Set(['.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.wmv', '.flv', '.mpg', '.mpeg', '.ts']);
const AUDIO_EXT = new Set(['.wav', '.m4a', '.mp3', '.aac', '.flac', '.ogg', '.aiff', '.aif', '.opus']);
const DOCUMENT_EXT = new Set([
  '.md', '.markdown', '.txt', '.log', '.json', '.jsonl', '.csv', '.tsv', '.js', '.jsx', '.mjs', '.cjs',
  '.ts', '.tsx', '.py', '.sh', '.css', '.scss', '.html', '.htm', '.xml', '.yaml', '.yml', '.toml',
  '.ini', '.conf', '.sql', '.go', '.rs', '.java', '.c', '.cpp', '.h', '.swift', '.rb', '.php',
  '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.pdf', '.rtf',
]);
const ARCHIVE_EXT = new Set(['.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.7z', '.rar', '.dmg', '.pkg', '.iso']);

class LiveError extends Error {
  constructor(code, message, extra = {}) {
    super(message);
    this.code = code;
    Object.assign(this, extra);
  }
}

function classifyKind(name, isDir) {
  if (isDir) return 'directory';
  const dot = name.lastIndexOf('.');
  const ext = dot > 0 ? name.slice(dot).toLowerCase() : '';
  if (IMAGE_EXT.has(ext)) return 'image';
  if (VIDEO_EXT.has(ext)) return 'video';
  if (AUDIO_EXT.has(ext)) return 'audio';
  if (DOCUMENT_EXT.has(ext)) return 'document';
  if (ARCHIVE_EXT.has(ext)) return 'archive';
  return 'other';
}

// ---------------------------------------------------------------- encryption

function readEncryptionKey(env = process.env) {
  const raw = String(env.NETDISK_TOKEN_ENCRYPTION_KEY || '').trim();
  if (!raw) throw new LiveError('encryption_key_missing', '服务器未配置 NETDISK_TOKEN_ENCRYPTION_KEY，无法保存网盘授权');
  let key;
  try {
    key = Buffer.from(raw, 'base64');
  } catch (_) {
    throw new LiveError('encryption_key_missing', 'NETDISK_TOKEN_ENCRYPTION_KEY 不是合法的 Base64');
  }
  // base64 解码是宽松的，长度校验才是真正的关卡
  if (key.length !== 32) throw new LiveError('encryption_key_missing', `NETDISK_TOKEN_ENCRYPTION_KEY 解码后必须是 32 字节，当前是 ${key.length} 字节`);
  return key;
}

function encryptToken(plainToken, key) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(String(plainToken), 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return [CIPHER_VERSION, iv.toString('base64'), tag.toString('base64'), encrypted.toString('base64')].join('.');
}

function decryptToken(payload, key) {
  const parts = String(payload || '').split('.');
  if (parts.length !== 4 || parts[0] !== CIPHER_VERSION) throw new LiveError('credential_corrupt', '凭据密文格式不正确，请重新授权');
  try {
    const iv = Buffer.from(parts[1], 'base64');
    const tag = Buffer.from(parts[2], 'base64');
    const data = Buffer.from(parts[3], 'base64');
    const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(data), decipher.final()]).toString('utf8');
  } catch (_) {
    throw new LiveError('credential_corrupt', '凭据解密失败，请重新授权');
  }
}

// ------------------------------------------------------- pasted token parsing

// 用户可能粘贴三种东西：整段 login_success 回调、只有 #hash 片段、或纯 Token。
// 注意 access_token 在 hash（#）后面而不是 ? query 里。
function parsePastedCredential(input) {
  const raw = String(input || '').trim();
  if (!raw) throw new LiveError('credential_empty', '请粘贴 access_token 或授权成功后的整段地址');
  if (raw.length > PASTE_MAX_LENGTH) throw new LiveError('credential_too_long', `粘贴内容过长，最多 ${PASTE_MAX_LENGTH} 个字符`);

  const marker = raw.indexOf('access_token=');
  if (marker === -1) {
    // 纯 Token
    if (!/^[\x21-\x7e]+$/.test(raw)) throw new LiveError('credential_invalid', 'Token 含有非法字符');
    if (raw.length > TOKEN_MAX_LENGTH) throw new LiveError('credential_invalid', 'Token 长度异常');
    return { token: raw, expiresIn: DEFAULT_EXPIRES_IN, scope: '' };
  }

  // access_token 在 hash 里。用户有时会连 # 一起复制，有时只复制 hash 内容，
  // 也可能连 query 形式一起贴；这里统一取最后一个分隔符之后的片段。
  const hashIndex = raw.indexOf('#');
  const queryIndex = raw.indexOf('?');
  const cut = hashIndex !== -1 ? hashIndex : queryIndex;
  const fragment = cut === -1 ? raw : raw.slice(cut + 1);
  const params = new URLSearchParams(fragment);
  const token = (params.get('access_token') || '').trim();
  if (!token) throw new LiveError('credential_invalid', '这段地址里没有 access_token，请复制授权成功后地址栏的整段链接');
  if (!/^[\x21-\x7e]+$/.test(token) || token.length > TOKEN_MAX_LENGTH) throw new LiveError('credential_invalid', 'Token 格式异常');

  const expiresInRaw = Number.parseInt(params.get('expires_in') || '', 10);
  const expiresIn = Number.isFinite(expiresInRaw) && expiresInRaw > 0 ? Math.min(expiresInRaw, 400 * 24 * 3600) : DEFAULT_EXPIRES_IN;
  const scope = (params.get('scope') || '').trim().slice(0, 120);
  return { token, expiresIn, scope };
}

// ------------------------------------------------------------- credential store

function createCredentialStore({ pool, env = process.env, now = () => new Date() }) {
  const provider = 'baidu_netdisk';

  async function load(userId) {
    const { rows } = await pool.query('SELECT * FROM netdisk_credentials WHERE user_id=$1', [userId]);
    const row = rows[0];
    if (!row) return { configured: false };
    const current = now();
    // 库里的 active 只是保存时的状态，真正的有效期以 expires_at 为准
    const expired = row.expires_at ? current.getTime() >= new Date(row.expires_at).getTime() : false;
    const status = expired ? 'expired' : row.status;
    return { configured: true, row, status, expired, now: current };
  }

  function describe(loaded) {
    if (!loaded.configured) {
      return { configured: false, status: 'none', tokenLast4: '', scope: '', obtainedAt: null, expiresAt: null, expiresInSec: null, expiringSoon: false, lastVerifiedAt: null, lastError: '', purpose: CREDENTIAL_PURPOSE, authorizeUrl: AUTHORIZE_URL };
    }
    const { row, status, now: current } = loaded;
    const expiresAt = row.expires_at || null;
    const expiresInSec = expiresAt ? Math.round((new Date(expiresAt).getTime() - current.getTime()) / 1000) : null;
    return {
      configured: true,
      status,
      tokenLast4: row.token_last4 || '',
      scope: row.scope || '',
      obtainedAt: row.obtained_at,
      expiresAt,
      expiresInSec,
      expiringSoon: expiresInSec !== null && expiresInSec > 0 && expiresInSec <= EXPIRING_SOON_SECONDS,
      lastVerifiedAt: row.last_verified_at || null,
      lastError: row.last_error || '',
      purpose: CREDENTIAL_PURPOSE,
      authorizeUrl: AUTHORIZE_URL,
    };
  }

  async function save(userId, pasted) {
    const key = readEncryptionKey(env);
    const parsed = parsePastedCredential(pasted);
    const expiresAt = new Date(now().getTime() + parsed.expiresIn * 1000);
    await pool.query(
      `INSERT INTO netdisk_credentials(user_id, provider, encrypted_token, token_last4, scope, status, expires_at, obtained_at, last_error, updated_at)
       VALUES($1,$2,$3,$4,$5,'active',$6,$7,'',now())
       ON CONFLICT(user_id) DO UPDATE SET
         provider=EXCLUDED.provider, encrypted_token=EXCLUDED.encrypted_token,
         token_last4=EXCLUDED.token_last4, scope=EXCLUDED.scope, status='active',
         expires_at=EXCLUDED.expires_at, obtained_at=EXCLUDED.obtained_at,
         last_error='', updated_at=now()`,
      [userId, provider, encryptToken(parsed.token, key), parsed.token.slice(-4), parsed.scope, expiresAt, now()],
    );
    return { token: parsed.token, expiresIn: parsed.expiresIn };
  }

  async function markInvalid(userId, message) {
    await pool.query(
      `UPDATE netdisk_credentials SET status='invalid', last_error=$2, updated_at=now()
        WHERE user_id=$1 AND status<>'expired'`,
      [userId, String(message || '').slice(0, 300)],
    );
  }

  async function markVerified(userId, at) {
    await pool.query(`UPDATE netdisk_credentials SET status='active', last_error='', last_verified_at=$2, updated_at=now() WHERE user_id=$1`, [userId, at || now()]);
  }

  // 只有库内 active 且未过期才返回明文；过期/失效一律不碰百度。
  async function resolveToken(userId) {
    const loaded = await load(userId);
    if (!loaded.configured) throw new LiveError('need_credential', '尚未配置百度网盘授权');
    if (loaded.status !== 'active') throw new LiveError(loaded.status === 'expired' ? 'credential_expired' : 'token_invalid', loaded.status === 'expired' ? '百度网盘授权已过期，请重新授权' : '百度网盘授权已失效，请重新授权');
    return { token: decryptToken(loaded.row.encrypted_token, readEncryptionKey(env)), userId };
  }

  async function remove(userId) {
    // 只删凭据，已缓存的 netdisk_nodes 保留，页面仍可看缓存目录
    await pool.query('DELETE FROM netdisk_credentials WHERE user_id=$1', [userId]);
  }

  return { describe, load, markInvalid, markVerified, parsePastedCredential, readEncryptionKey, remove, resolveToken, save };
}

// ------------------------------------------------------------- list-only client

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createListClient({ fetchImpl = globalThis.fetch, env = process.env, logger = console } = {}) {
  const rootPath = loadNetdiskConfig(env).rootPath;

  async function requestList(token, dir, page, deadline) {
    const query = new URLSearchParams({
      access_token: token,
      dir,
      web: '1',
      page: String(page),
      num: String(PAGE_SIZE),
      order: 'name',
      desc: '0',
    });
    const url = `${NETDISK_API}/api/list?${query.toString()}`;
    const remaining = Math.max(500, Math.min(REQUEST_TIMEOUT_MS, deadline - Date.now()));
    let response;
    try {
      response = await fetchImpl(url, { method: 'GET', signal: AbortSignal.timeout(remaining) });
    } catch (error) {
      // 日志只记路径和错误类型，绝不记 URL（query 里带 token）
      throw new LiveError('network_error', `读取网盘目录失败：${dir}`, { cause: error && error.name });
    }
    if (!response.ok) {
      const retryable = response.status === 408 || response.status === 429 || response.status >= 500;
      throw new LiveError('http_error', `百度返回 HTTP ${response.status}`, { statusCode: response.status, retryable });
    }
    let payload;
    try {
      payload = await response.json();
    } catch (_) {
      throw new LiveError('bad_response', '百度返回了非 JSON 响应');
    }
    // 只有明确表示授权不可用的 errno 才算 token 失效。
    // 注意 -6 在 RETRYABLE_ERRNO 里（与 Python 实现一致），必须走重试而不是判死。
    if (payload.errno === 111 || payload.errno === 110) {
      throw new LiveError('token_invalid', '百度网盘授权已失效，请重新授权', { errno: payload.errno });
    }
    if (payload.errno !== 0) {
      throw new LiveError('api_error', `百度接口返回 errno=${payload.errno}`, { errno: payload.errno, retryable: RETRYABLE_ERRNO.has(payload.errno) });
    }
    return payload;
  }

  async function requestListWithRetry(token, dir, page, deadline) {
    let lastError;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
      try {
        return await requestList(token, dir, page, deadline);
      } catch (error) {
        lastError = error;
        const retryable = error.code === 'network_error' || error.retryable === true;
        if (!retryable || attempt === MAX_ATTEMPTS || Date.now() >= deadline) break;
        await sleep(Math.min(2 ** attempt * 1000, 10000));
      }
    }
    throw lastError;
  }

  // 只列一层；同层翻页拉完。errno -9（目录不存在）当作空目录。
  async function listLayer(token, dirPath) {
    const dir = assertNetdiskPath(dirPath, rootPath);
    const deadline = Date.now() + LEVEL_TIMEOUT_MS;
    const items = [];
    for (let page = 1; ; page += 1) {
      let payload;
      try {
        payload = await requestListWithRetry(token, dir, page, deadline);
      } catch (error) {
        if (page === 1 && (error.errno === -9 || /errno=-9/.test(error.message))) {
          logger.log?.(`[netdisk] ${dir} 不存在，按空目录处理`);
          return [];
        }
        throw error;
      }
      const batch = Array.isArray(payload.list) ? payload.list : [];
      items.push(...batch);
      if (batch.length < PAGE_SIZE) break;
      if (Date.now() >= deadline) throw new LiveError('level_timeout', '读取网盘目录超时，请稍后重试');
    }
    return items;
  }

  return { listLayer, rootPath };
}

// 清洗百度 list 返回的条目：只保留允许字段，丢掉缩略图、dlink、打码 md5 和任何凭据。
function cleanListItem(item, rootPath) {
  const rawPath = String(item?.path || '');
  if (!rawPath.startsWith(`${rootPath}/`) && rawPath !== rootPath) return null;
  const itemPath = assertNetdiskPath(rawPath, rootPath);
  const name = itemPath.split('/').pop() || '';
  const isDir = Number(item?.isdir || 0) === 1;
  const serverMtime = Number(item?.server_mtime || 0);
  return {
    fsId: String(item?.fs_id ?? '0').replace(/[^0-9]/g, '') || '0',
    path: itemPath,
    parentPath: itemPath.split('/').slice(0, -1).join('/') || rootPath,
    name,
    isDir,
    kind: classifyKind(name, isDir),
    sizeBytes: Math.max(0, Number(item?.size || 0) || 0),
    serverMtime: Number.isFinite(serverMtime) && serverMtime > 0 ? new Date(serverMtime * 1000) : null,
    category: Number(item?.category || 0) || 0,
    extra: {},
  };
}

// 只清理当前这一层：不在本次结果里的节点删除；被删的是目录则连子孙一起删。
async function pruneStaleLayer(client, userId, parentPath, keptPaths) {
  const { rows } = await client.query(
    'SELECT id, path, is_dir FROM netdisk_nodes WHERE user_id=$1 AND parent_path=$2',
    [userId, parentPath],
  );
  const stale = rows.filter((row) => !keptPaths.has(row.path));
  if (!stale.length) return 0;
  const ids = new Set(stale.map((row) => row.id));

  // 子孙不在 rows 里（rows 只是这一层），必须单独按前缀再查一次，否则目录删掉会留下孤儿节点。
  // 用 position(前缀 IN path)=1 做前缀判断：目录名里的 % 和 _ 不需要转义，
  // 也不会误伤同前缀的兄弟目录（LIKE 会把它们当通配符）。
  const staleDirs = stale.filter((row) => row.is_dir).map((row) => row.path);
  if (staleDirs.length) {
    const { rows: descendants } = await client.query(
      `SELECT n.id FROM netdisk_nodes n
        WHERE n.user_id = $1
          AND EXISTS (
            SELECT 1 FROM unnest($2::text[]) AS prefix
             WHERE position((prefix || '/') IN n.path) = 1
          )`,
      [userId, staleDirs],
    );
    descendants.forEach((row) => ids.add(row.id));
  }

  await client.query('DELETE FROM netdisk_nodes WHERE user_id=$1 AND id = ANY($2::uuid[])', [userId, [...ids]]);
  return ids.size;
}

async function upsertLayer(client, userId, snapshotId, nodes) {
  for (let index = 0; index < nodes.length; index += 500) {
    const chunk = nodes.slice(index, index + 500);
    const values = [];
    const tuples = chunk.map((node) => {
      values.push(
        userId, snapshotId, node.fsId, node.path, node.parentPath, node.name,
        node.isDir, node.kind, node.sizeBytes, node.serverMtime, node.category,
        'unknown', 0, JSON.stringify(node.extra),
      );
      const start = values.length - 13;
      return `($${start},$${start + 1},$${start + 2},$${start + 3},$${start + 4},$${start + 5},$${start + 6},$${start + 7},$${start + 8},$${start + 9},$${start + 10},$${start + 11},$${start + 12},$${start + 13}::jsonb)`;
    });
    await client.query(
      `INSERT INTO netdisk_nodes(user_id, snapshot_id, fs_id, path, parent_path, name, is_dir, kind, size_bytes, server_mtime, category, local_state, local_size_bytes, extra)
       VALUES ${tuples.join(',')}
       ON CONFLICT(user_id, path) DO UPDATE SET
         snapshot_id=EXCLUDED.snapshot_id, fs_id=EXCLUDED.fs_id, parent_path=EXCLUDED.parent_path,
         name=EXCLUDED.name, is_dir=EXCLUDED.is_dir, kind=EXCLUDED.kind, size_bytes=EXCLUDED.size_bytes,
         server_mtime=EXCLUDED.server_mtime, category=EXCLUDED.category,
         local_state='unknown', local_size_bytes=0, extra=EXCLUDED.extra, updated_at=now()`,
      values,
    );
  }
}

// 读一层目录：优先实时 list，失败降级到缓存。永远不会因为网盘不可用而让页面报错。
//
// 关于 netdisk_snapshots：实时 list 只覆盖当前这一层，写进快照表会让「最近同步」看起来
// 像是整盘统计，所以这里不写快照行，live 写入的节点 snapshot_id 保持 NULL。
// 「上次从网盘读到的时间」由 nodes.updated_at 体现，前端优先显示本次 fetchedAt。
function createLiveDirectoryReader({ pool, credentials, listClient }) {
  const rootPath = listClient.rootPath;

  async function cachedNodes(userId, parentPath) {
    const { rows } = await pool.query(
      'SELECT * FROM netdisk_nodes WHERE user_id=$1 AND parent_path=$2 ORDER BY is_dir DESC, name ASC',
      [userId, parentPath],
    );
    return rows;
  }

  async function readLayer({ userId, dirPath, log = console }) {
    const requestedPath = assertNetdiskPath(dirPath, rootPath);
    const credentialView = credentials.describe(await credentials.load(userId));
    const base = {
      path: requestedPath,
      rootPath,
      source: 'none',
      fetchedAt: null,
      warning: '',
      errorCode: '',
      credential: credentialView,
      nodes: [],
      snapshot: null,
    };

    let token;
    try {
      ({ token } = await credentials.resolveToken(userId));
    } catch (error) {
      // 没配 / 过期 / 失效：绝不拿旧 Token 打百度，直接给缓存
      const cached = await cachedNodes(userId, requestedPath);
      return {
        ...base,
        source: cached.length ? 'cache' : 'none',
        nodes: cached,
        errorCode: error.code || 'need_credential',
        warning: cached.length ? `${error.message}正在显示上次缓存的目录。` : '',
      };
    }

    let items;
    try {
      items = await listClient.listLayer(token, requestedPath);
      await credentials.markVerified(userId);
    } catch (error) {
      const code = error.code === 'token_invalid' ? 'token_invalid' : (error.code || 'live_unavailable');
      if (code === 'token_invalid') {
        await credentials.markInvalid(userId, '百度返回 errno=111，授权已失效');
        log.warn?.(`[netdisk] ${requestedPath} 授权失效（errno=111）`);
      } else {
        log.warn?.(`[netdisk] ${requestedPath} 实时读取失败：${error.code || 'unknown'}`);
      }
      const cached = await cachedNodes(userId, requestedPath);
      return {
        ...base,
        source: cached.length ? 'cache' : 'none',
        nodes: cached,
        errorCode: code,
        warning: cached.length ? '网盘暂时读不到，正在显示上次缓存的目录。' : '网盘暂时读不到，请稍后重试。',
        credential: credentials.describe(await credentials.load(userId)),
      };
    }

    // /api/list should return direct children only. Enforce that boundary
    // ourselves so a malformed/upstream response cannot poison another layer.
    const cleaned = items
      .map((item) => cleanListItem(item, rootPath))
      .filter((node) => node && node.parentPath === requestedPath);
    const fetchedAt = new Date();
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await upsertLayer(client, userId, null, cleaned);
      const removed = await pruneStaleLayer(client, userId, requestedPath, new Set(cleaned.map((node) => node.path)));
      await client.query('COMMIT');
      log.log?.(`[netdisk] ${requestedPath} 实时读取 ${cleaned.length} 项，清理 ${removed} 项`);
    } catch (error) {
      await client.query('ROLLBACK').catch(() => {});
      const cached = await cachedNodes(userId, requestedPath);
      return {
        ...base,
        source: cached.length ? 'cache' : 'none',
        nodes: cached,
        errorCode: 'cache_write_failed',
        warning: '目录已读取但缓存写入失败，正在显示上次缓存。',
      };
    } finally {
      client.release();
    }

    return {
      ...base,
      source: 'live',
      fetchedAt,
      nodes: await cachedNodes(userId, requestedPath),
    };
  }

  return { cachedNodes, readLayer };
}

module.exports = {
  AUTHORIZE_URL,
  CREDENTIAL_PURPOSE,
  DEFAULT_EXPIRES_IN,
  EXPIRING_SOON_SECONDS,
  LiveError,
  PAGE_SIZE,
  RETRYABLE_ERRNO,
  classifyKind,
  cleanListItem,
  createCredentialStore,
  createListClient,
  createLiveDirectoryReader,
  decryptToken,
  encryptToken,
  parsePastedCredential,
  pruneStaleLayer,
  readEncryptionKey,
  upsertLayer,
};
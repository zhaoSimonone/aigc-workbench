// 百度网盘目录元数据：只收节点 JSON，只渲染目录，不中转任何文件字节。
// 这一层永远不会持有百度 Token，也不会把网盘文件代理给浏览器。
const fsp = require('fs').promises;
const path = require('path');

const DEFAULT_ROOT_PATH = '/AIGC';
const MAX_NODES_PER_CATALOG = 20000;
const MAX_CATALOG_BODY_BYTES = 8 * 1024 * 1024;
const FORBIDDEN_KEYS = new Set([
  'access_token', 'accesstoken', 'access-token', 'dlink', 'cookie', 'set-cookie',
  'session_secret', 'sessionsecret', 'token', 'refresh_token', 'password', 'secret', 'sign', 'signature',
]);
const VALID_KINDS = new Set(['image', 'video', 'audio', 'document', 'archive', 'other', 'directory']);

function netdiskError(message, statusCode = 400) {
  return Object.assign(new Error(message), { statusCode, code: 'NETDISK_REQUEST_INVALID' });
}

function loadNetdiskConfig(env = process.env) {
  return {
    rootPath: ('/' + String(env.NETDISK_ROOT_PATH || DEFAULT_ROOT_PATH).trim().replace(/^\/+|\/+$/g, '')).replace(/\/+$/, '') || DEFAULT_ROOT_PATH,
    // 本机降级导入只允许读取这一个固定文件，不接受任意路径
    catalogImportFile: 'outputs/netdisk-catalog.json',
  };
}

function normaliseRoot(value) {
  const trimmed = String(value || DEFAULT_ROOT_PATH).trim();
  return '/' + trimmed.replace(/^\/+|\/+$/g, '') || DEFAULT_ROOT_PATH;
}

function normaliseNodePath(value) {
  const raw = String(value || '').trim();
  // 网盘路径不需要相对语义，出现 .. 直接拒绝，避免用穿越手法绕过前缀白名单
  if (raw.split('/').some((segment) => segment === '..')) throw netdiskError('网盘路径不允许包含 .. 片段');
  return '/' + raw.replace(/^\/+|\/+$/g, '').replace(/\/{2,}/g, '/');
}

// 目录白名单：只允许 /AIGC 前缀，禁止把网盘其它目录或整个网盘刷进工作台。
function assertNetdiskPath(candidate, rootPath) {
  const root = normaliseRoot(rootPath);
  const value = normaliseNodePath(candidate);
  if (value === '/' || value === '') throw netdiskError('网盘根路径无效');
  if (value !== root && !value.startsWith(`${root}/`)) {
    throw netdiskError(`路径 ${value} 不在允许的网盘根目录 ${root} 内`);
  }
  return value;
}

// 请求体只允许是目录元数据。任何凭据字段或疑似文件内容的字段都要直接拒绝。
function assertNoFileContent(payload) {
  const offenders = [];
  const visit = (value, depth) => {
    if (depth > 8 || offenders.length >= 5) return;
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, depth + 1));
      return;
    }
    if (!value || typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value)) {
      if (FORBIDDEN_KEYS.has(String(key).toLowerCase())) {
        offenders.push(key);
        continue;
      }
      if (Buffer.isBuffer(item) || ArrayBuffer.isView(item) || item instanceof ArrayBuffer) {
        // 二进制载荷（Buffer/ArrayBuffer）一定是文件内容，不是目录元数据
        offenders.push(key);
        continue;
      }
      if (typeof item === 'string') {
        // base64 载荷或 data URI 一律视为文件内容，目录接口不接收
        if (item.length > 4096 || /^(data:|base64,)/i.test(item) || /^[A-Za-z0-9+/]{512,}={0,2}$/.test(item)) {
          offenders.push(key);
          continue;
        }
      }
      visit(item, depth + 1);
    }
  };
  visit(payload, 0);
  if (offenders.length) throw netdiskError(`目录接口只接受元数据，拒绝字段：${offenders.join(', ')}`);
}

function parseCatalogBody(body, rootPath) {
  if (!body || typeof body !== 'object') throw netdiskError('请求体必须是目录节点 JSON');
  assertNoFileContent(body);
  const nodes = Array.isArray(body) ? body : body.nodes;
  if (!Array.isArray(nodes)) throw netdiskError('请求体缺少 nodes 数组');
  if (nodes.length > MAX_NODES_PER_CATALOG) throw netdiskError(`单次最多提交 ${MAX_NODES_PER_CATALOG} 个节点，请分批提交或缩小 root`, 413);

  const root = normaliseRoot(body.root || rootPath);
  if (root !== normaliseRoot(rootPath)) throw netdiskError(`只接受 ${normaliseRoot(rootPath)} 的目录快照`);
  const parsed = nodes.map((node) => {
    if (!node || typeof node !== 'object') throw netdiskError('节点必须是对象');
    const nodePath = assertNetdiskPath(node.path, rootPath);
    const name = String(node.name || nodePath.split('/').pop() || '').trim();
    if (!name) throw netdiskError(`节点缺少名称：${nodePath}`);
    const isDir = node.isDir === true || node.is_dir === true;
    const kind = isDir ? 'directory' : (VALID_KINDS.has(String(node.kind)) ? String(node.kind) : 'other');
    const sizeBytes = Number(node.sizeBytes ?? node.size_bytes ?? 0);
    const serverMtime = Number(node.serverMtime ?? node.server_mtime ?? 0);
    const declaredParent = node.parentPath ?? node.parent_path;
    // 根节点自身没有父目录，用空串表示，否则它会把自己列成自己的子节点
    const rawParent = declaredParent || nodePath.split('/').slice(0, -1).join('/');
    const parentPath = nodePath === root || !rawParent ? '' : assertNetdiskPath(rawParent, rootPath);
    return {
      // 百度 fs_id 是 64 位整数，走 JS Number 会丢精度，按字符串透传给 BIGINT 列
      fsId: String(node.fsId ?? node.fs_id ?? '0').replace(/[^0-9]/g, '') || '0',
      path: nodePath,
      parentPath,
      name,
      isDir,
      kind,
      sizeBytes: Number.isFinite(sizeBytes) && sizeBytes > 0 ? Math.trunc(sizeBytes) : 0,
      serverMtime: Number.isFinite(serverMtime) && serverMtime > 0 ? new Date(serverMtime * 1000) : null,
      category: Number(node.category) || 0,
      localState: ['unknown', 'present', 'missing', 'size_mismatch'].includes(String(node.localState)) ? String(node.localState) : 'unknown',
      localSizeBytes: Math.max(0, Number(node.localSizeBytes) || 0),
      extra: node.extra && typeof node.extra === 'object' && !Array.isArray(node.extra) ? node.extra : {},
    };
  });
  // 只有完整覆盖 /AIGC 的快照才能清理"已消失"的节点；
  // 截断快照（--depth / --max-nodes）或子目录快照只做 upsert，避免误删界面上的兄弟目录。
  const complete = body.complete === true;
  return { complete, root, nodes: parsed };
}

function publicNode(row) {
  return {
    path: row.path,
    name: row.name,
    parentPath: row.parent_path,
    isDir: row.is_dir,
    kind: row.kind,
    sizeBytes: Number(row.size_bytes || 0),
    serverMtime: row.server_mtime,
    fsId: String(row.fs_id === null || row.fs_id === undefined ? 0 : row.fs_id),
    category: Number(row.category || 0),
    localState: row.local_state || 'unknown',
    localSizeBytes: Number(row.local_size_bytes || 0),
  };
}

function likePrefix(value) {
  // LIKE 把 %/_ 当通配符。网盘路径可能含这些字符，必须转义后再拼前缀。
  return String(value || '').replace(/[\\%_]/g, (ch) => `\\${ch}`) + '/%';
}

// 一次发布 = 插入快照 + upsert 节点 + 删除本次已消失的节点，全部在一个事务里。
async function applyCatalog({ pool, userId, body, rootPath, source }) {
  const { root, nodes, complete } = parseCatalogBody(body, rootPath);
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const snapshot = await client.query(
      `INSERT INTO netdisk_snapshots(user_id,root_path,source,file_count,dir_count,total_bytes,status)
       VALUES($1,$2,$3,$4,$5,$6,'ready') RETURNING *`,
      [
        userId,
        root,
        String(source || 'cli').slice(0, 32),
        nodes.filter((node) => !node.isDir).length,
        nodes.filter((node) => node.isDir).length,
        nodes.reduce((sum, node) => sum + (node.isDir ? 0 : node.sizeBytes), 0),
      ],
    );
    const snapshotId = snapshot.rows[0].id;

    for (let index = 0; index < nodes.length; index += 500) {
      const chunk = nodes.slice(index, index + 500);
      const values = [];
      const tuples = chunk.map((node) => {
        values.push(
          userId, snapshotId, node.fsId, node.path, node.parentPath, node.name,
          node.isDir, node.kind, node.sizeBytes, node.serverMtime, node.category,
          node.localState, node.localSizeBytes, JSON.stringify(node.extra),
        );
        const start = values.length - 13;
        return `($${start},$${start + 1},$${start + 2},$${start + 3},$${start + 4},$${start + 5},$${start + 6},$${start + 7},$${start + 8},$${start + 9},$${start + 10},$${start + 11},$${start + 12},$${start + 13}::jsonb)`;
      });
      await client.query(
        `INSERT INTO netdisk_nodes(user_id,snapshot_id,fs_id,path,parent_path,name,is_dir,kind,size_bytes,server_mtime,category,local_state,local_size_bytes,extra)
         VALUES ${tuples.join(',')}
         ON CONFLICT(user_id, path) DO UPDATE SET
           snapshot_id=EXCLUDED.snapshot_id, fs_id=EXCLUDED.fs_id, parent_path=EXCLUDED.parent_path,
           name=EXCLUDED.name, is_dir=EXCLUDED.is_dir, kind=EXCLUDED.kind, size_bytes=EXCLUDED.size_bytes,
           server_mtime=EXCLUDED.server_mtime, category=EXCLUDED.category,
           local_state=EXCLUDED.local_state, local_size_bytes=EXCLUDED.local_size_bytes,
           extra=EXCLUDED.extra, updated_at=now()`,
        values,
      );
    }

    // 清掉本次快照里已经没有的节点，避免界面残留已在网盘删除的文件。
    // 仅在完整覆盖根目录、且节点清单非空时执行；空 complete 快照会把整棵树删光。
    let removedNodes = 0;
    if (complete && nodes.length) {
      const deleted = await client.query(
        `DELETE FROM netdisk_nodes
          WHERE user_id=$1
            AND (path=$2 OR path LIKE $3 ESCAPE '\\')
            AND NOT (path = ANY($4::text[]))`,
        [userId, root, likePrefix(root), nodes.map((node) => node.path)],
      );
      removedNodes = deleted.rowCount;
    }
    await client.query('COMMIT');
    return {
      snapshotId,
      root,
      source: snapshot.rows[0].source,
      fileCount: snapshot.rows[0].file_count,
      dirCount: snapshot.rows[0].dir_count,
      totalBytes: Number(snapshot.rows[0].total_bytes || 0),
      removedNodes,
      complete,
      createdAt: snapshot.rows[0].created_at,
    };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}

async function readWhitelistedCatalog({ pool, userId, rootPath, requestedFile, repoRoot }) {
  const config = loadNetdiskConfig();
  const wanted = path.resolve(repoRoot, requestedFile || config.catalogImportFile);
  const allowed = path.resolve(repoRoot, config.catalogImportFile);
  if (wanted !== allowed) {
    throw netdiskError(`只允许导入 ${config.catalogImportFile}，不接受其它路径`, 403);
  }
  let raw;
  try {
    raw = await fsp.readFile(wanted, 'utf8');
  } catch (_) {
    throw netdiskError(`找不到 ${config.catalogImportFile}，请先运行 netdisk_sync.py publish-catalog`, 404);
  }
  if (Buffer.byteLength(raw, 'utf8') > MAX_CATALOG_BODY_BYTES) throw netdiskError('目录快照文件过大', 413);
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (_) {
    throw netdiskError('目录快照不是合法 JSON');
  }
  return applyCatalog({ pool, userId, body: parsed, rootPath, source: parsed?.source || 'cli' });
}

module.exports = {
  DEFAULT_ROOT_PATH,
  MAX_CATALOG_BODY_BYTES,
  MAX_NODES_PER_CATALOG,
  applyCatalog,
  assertNetdiskPath,
  assertNoFileContent,
  likePrefix,
  loadNetdiskConfig,
  normaliseNodePath,
  parseCatalogBody,
  publicNode,
  readWhitelistedCatalog,
};
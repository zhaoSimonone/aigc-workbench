// 本地目录归档：服务端配置、路径白名单校验、文件分类与只读扫描。
// 该模块不依赖数据库和 COS，可单独用于单元测试。
const fs = require('fs');
const os = require('os');
const path = require('path');

const fsp = fs.promises;

const DEFAULT_CONCURRENCY = 2;
const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_TEXT_PREVIEW_MAX_BYTES = 1024 * 1024;
const DEFAULT_SCAN_ENTRY_LIMIT = 200;
const DEFAULT_SCAN_MAX_ENTRIES = 50000;

const SKIP_DIR_NAMES = new Set(['.git', 'node_modules', '.venv', '.cache', '.svn', '__pycache__', '.idea', '.vscode']);
const SKIP_FILE_NAMES = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini', 'Icon\r']);
const TEMP_FILE_SUFFIXES = ['~', '.tmp', '.temp', '.swp', '.crdownload', '.part', '.download'];
const SENSITIVE_EXTENSIONS = new Set(['.pem', '.key', '.p12', '.pfx', '.jks', '.keystore', '.asc', '.gpg', '.ppk']);
const SENSITIVE_NAME_PATTERN = /(^|[^a-z])(secret|secrets|token|tokens|credential|credentials|password|passwd|apikey|api_key)([^a-z]|$)/i;

const MIME_BY_KIND = {
  image: { '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp', '.heic': 'image/heic', '.heif': 'image/heif', '.bmp': 'image/bmp', '.tiff': 'image/tiff', '.avif': 'image/avif' },
  video: { '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.m4v': 'video/x-m4v', '.webm': 'video/webm', '.mkv': 'video/x-matroska', '.avi': 'video/x-msvideo', '.wmv': 'video/x-ms-wmv', '.flv': 'video/x-flv', '.mpg': 'video/mpeg', '.mpeg': 'video/mpeg', '.ts': 'video/mp2t' },
  audio: { '.wav': 'audio/wav', '.m4a': 'audio/mp4', '.mp3': 'audio/mpeg', '.aac': 'audio/aac', '.flac': 'audio/flac', '.ogg': 'audio/ogg', '.aiff': 'audio/aiff', '.aif': 'audio/aiff', '.opus': 'audio/opus' },
  document: {
    '.md': 'text/markdown', '.markdown': 'text/markdown', '.txt': 'text/plain', '.log': 'text/plain',
    '.json': 'application/json', '.jsonl': 'application/x-ndjson', '.csv': 'text/csv', '.tsv': 'text/tab-separated-values',
    '.js': 'text/javascript', '.jsx': 'text/javascript', '.mjs': 'text/javascript', '.cjs': 'text/javascript',
    '.ts': 'text/plain', '.tsx': 'text/plain', '.py': 'text/x-python', '.sh': 'text/x-shellscript',
    '.css': 'text/css', '.scss': 'text/css', '.html': 'text/html', '.htm': 'text/html', '.xml': 'application/xml',
    '.yaml': 'text/yaml', '.yml': 'text/yaml', '.toml': 'text/plain', '.ini': 'text/plain', '.conf': 'text/plain',
    '.sql': 'text/plain', '.go': 'text/plain', '.rs': 'text/plain', '.java': 'text/plain', '.c': 'text/plain',
    '.cpp': 'text/plain', '.h': 'text/plain', '.swift': 'text/plain', '.rb': 'text/plain', '.php': 'text/plain',
    '.doc': 'application/msword', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint', '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.pdf': 'application/pdf', '.rtf': 'application/rtf',
  },
  archive: { '.zip': 'application/zip', '.tar': 'application/x-tar', '.gz': 'application/gzip', '.tgz': 'application/gzip', '.bz2': 'application/x-bzip2', '.xz': 'application/x-xz', '.7z': 'application/x-7z-compressed', '.rar': 'application/vnd.rar', '.dmg': 'application/x-apple-diskimage', '.pkg': 'application/x-newton-compatible-pkg', '.iso': 'application/x-iso9660-image' },
};

const KINDS = ['image', 'video', 'audio', 'document', 'archive', 'other'];
const TEXT_PREVIEW_EXTENSIONS = new Set([
  '.md', '.markdown', '.txt', '.log', '.json', '.jsonl', '.csv', '.tsv', '.js', '.jsx', '.mjs', '.cjs',
  '.ts', '.tsx', '.py', '.sh', '.css', '.scss', '.html', '.htm', '.xml', '.yaml', '.yml', '.toml',
  '.ini', '.conf', '.sql', '.go', '.rs', '.java', '.c', '.cpp', '.h', '.swift', '.rb', '.php',
]);

function pathError(message, statusCode) {
  return Object.assign(new Error(message), { statusCode, code: 'ARCHIVE_PATH_ERROR' });
}

function readListEnv(value) {
  return String(value || '')
    .split(path.delimiter)
    .map((item) => item.trim())
    .filter(Boolean);
}

function readNumberEnv(value, fallback, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  if (!Number.isFinite(parsed) || parsed < min) return fallback;
  return Math.min(parsed, max);
}

function loadArchiveConfig(env = process.env) {
  return {
    storageBackend: String(env.ARCHIVE_STORAGE_BACKEND || 'cos').trim() === 'local' ? 'local' : 'cos',
    localRoot: String(env.ARCHIVE_LOCAL_ROOT || '').trim(),
    allowedRoots: readListEnv(env.ARCHIVE_ALLOWED_SOURCE_ROOTS),
    cosPrefix: (String(env.ARCHIVE_COS_PREFIX || 'archives').trim() || 'archives').replace(/^\/+|\/+$/g, ''),
    maxConcurrency: readNumberEnv(env.ARCHIVE_MAX_CONCURRENCY, DEFAULT_CONCURRENCY, { max: 8 }),
    maxRetries: readNumberEnv(env.ARCHIVE_MAX_RETRIES, DEFAULT_MAX_RETRIES, { max: 10 }),
    textPreviewMaxBytes: readNumberEnv(env.ARCHIVE_TEXT_PREVIEW_MAX_BYTES, DEFAULT_TEXT_PREVIEW_MAX_BYTES, { min: 1024, max: 64 * 1024 * 1024 }),
    scanEntryLimit: readNumberEnv(env.ARCHIVE_SCAN_ENTRY_LIMIT, DEFAULT_SCAN_ENTRY_LIMIT, { max: 5000 }),
    scanMaxEntries: readNumberEnv(env.ARCHIVE_SCAN_MAX_ENTRIES, DEFAULT_SCAN_MAX_ENTRIES, { max: 500000 }),
    multipartThresholdBytes: readNumberEnv(env.ARCHIVE_MULTIPART_THRESHOLD_BYTES, 32 * 1024 * 1024, { min: 1024 * 1024, max: 512 * 1024 * 1024 }),
  };
}

function normaliseExtension(name) {
  const extension = path.extname(String(name || '')).toLowerCase();
  return /^\.[a-z0-9]{1,16}$/.test(extension) ? extension : '';
}

function classifyKind(name) {
  const extension = normaliseExtension(name);
  for (const kind of KINDS) {
    if (kind !== 'other' && MIME_BY_KIND[kind][extension]) return kind;
  }
  return 'other';
}

function mimeTypeFor(name, kind = classifyKind(name)) {
  const extension = normaliseExtension(name);
  return MIME_BY_KIND[kind]?.[extension] || (kind === 'document' ? 'text/plain' : 'application/octet-stream');
}

function isTextPreviewKind(name) {
  return TEXT_PREVIEW_EXTENSIONS.has(normaliseExtension(name));
}

function isSensitiveName(name) {
  const base = path.basename(String(name || ''));
  if (!base) return false;
  if (/^\.env(\..+)?$/i.test(base)) return true;
  if (SENSITIVE_EXTENSIONS.has(path.extname(base).toLowerCase())) return true;
  return SENSITIVE_NAME_PATTERN.test(base);
}

function shouldSkipDirectory(name) {
  const base = path.basename(String(name || ''));
  return SKIP_DIR_NAMES.has(base) || base.startsWith('.');
}

function shouldSkipFile(name) {
  const base = path.basename(String(name || ''));
  if (SKIP_FILE_NAMES.has(base)) return true;
  return TEMP_FILE_SUFFIXES.some((suffix) => base.endsWith(suffix));
}

function expandHome(input) {
  const value = String(input || '');
  if (value === '~') return os.homedir();
  if (value.startsWith('~/')) return path.join(os.homedir(), value.slice(2));
  return value;
}

function isInside(parent, child) {
  if (!parent || !child) return false;
  if (parent === child) return true;
  const relative = path.relative(parent, child);
  return Boolean(relative) && !relative.startsWith('..') && !path.isAbsolute(relative);
}

async function realpathOrNull(target) {
  try {
    return await fsp.realpath(target);
  } catch (_) {
    return null;
  }
}

// 白名单校验：resolve + realpath 双层校验，拒绝 .. 逃逸、符号链接逃逸和名单外路径。
async function resolveSourcePath(inputPath, allowedRoots) {
  const roots = (Array.isArray(allowedRoots) ? allowedRoots : []).filter(Boolean);
  if (!roots.length) throw pathError('未配置 ARCHIVE_ALLOWED_SOURCE_ROOTS，服务端未开放本地目录归档', 403);
  const raw = expandHome(inputPath).trim();
  if (!raw) throw pathError('请填写要归档的本地目录路径', 400);
  if (raw.includes('\0')) throw pathError('路径包含非法字符', 400);
  if (raw.split('/').includes('..')) throw pathError('路径不允许包含 .. 片段', 400);
  const resolved = path.resolve(raw);
  if (!path.isAbsolute(resolved)) throw pathError('必须填写绝对路径', 400);

  const matchedRoot = roots.find((root) => isInside(path.resolve(expandHome(root)), resolved));
  if (!matchedRoot) throw pathError('该目录不在允许归档的白名单内', 403);

  const realRoots = [];
  for (const root of roots) {
    const realRoot = await realpathOrNull(path.resolve(expandHome(root)));
    if (realRoot) realRoots.push(realRoot);
  }
  const real = await realpathOrNull(resolved);
  if (!real) throw pathError('目录不存在或无法访问', 404);
  const realStat = await fsp.stat(real);
  if (!realStat.isDirectory()) throw pathError('只支持归档目录', 400);
  if (!realRoots.some((realRoot) => isInside(realRoot, real))) {
    throw pathError('符号链接指向白名单之外的目录，已拒绝', 403);
  }
  return { resolved, realPath: real, relativePath: path.relative(realRoots.find((realRoot) => isInside(realRoot, real)), real) };
}

function archiveObjectKey({ prefix, userId, sha256, sizeBytes }) {
  const safePrefix = String(prefix || 'archives').replace(/^\/+|\/+$/g, '');
  return `${safePrefix}/${userId}/objects/${sha256.slice(0, 2)}/${sha256}-${sizeBytes}`;
}

// 只读扫描：只读 stat / 目录结构，不读取文件内容、不计算哈希、不修改源目录。
async function scanDirectory({
  sourcePath,
  allowedRoots,
  maxEntries = DEFAULT_SCAN_MAX_ENTRIES,
  includeSensitive = false,
}) {
  const { realPath } = await resolveSourcePath(sourcePath, allowedRoots);
  const entries = [];
  const skipped = [];
  const riskFiles = [];
  const directories = new Map();
  const byKind = Object.fromEntries(KINDS.map((kind) => [kind, 0]));
  const byExtension = new Map();
  let totalBytes = 0;
  let fileCount = 0;
  let truncated = false;
  let emptyDirectories = 0;

  const stack = [realPath];
  while (stack.length) {
    const currentDir = stack.pop();
    let dirents;
    try {
      dirents = await fsp.readdir(currentDir, { withFileTypes: true });
    } catch (error) {
      skipped.push({ path: currentDir, reason: 'unreadable_directory', message: error.code || error.message });
      continue;
    }
    if (!dirents.length) emptyDirectories += 1;
    for (const dirent of dirents) {
      const absolute = path.join(currentDir, dirent.name);
      const relative = path.relative(realPath, absolute);
      if (dirent.isSymbolicLink()) {
        skipped.push({ path: relative, reason: 'symbolic_link' });
        continue;
      }
      if (dirent.isDirectory()) {
        if (shouldSkipDirectory(dirent.name)) {
          skipped.push({ path: relative, reason: 'skipped_directory' });
          continue;
        }
        stack.push(absolute);
        continue;
      }
      if (!dirent.isFile()) {
        skipped.push({ path: relative, reason: 'special_file' });
        continue;
      }
      if (shouldSkipFile(dirent.name)) {
        skipped.push({ path: relative, reason: 'skipped_system_file' });
        continue;
      }
      const sensitive = isSensitiveName(dirent.name);
      if (sensitive && !includeSensitive) {
        riskFiles.push({ path: relative, name: dirent.name, reason: 'sensitive_file' });
        skipped.push({ path: relative, reason: 'sensitive_file' });
        continue;
      }
      let stats;
      try {
        stats = await fsp.lstat(absolute);
      } catch (error) {
        skipped.push({ path: relative, reason: 'stat_failed', message: error.code || error.message });
        continue;
      }
      if (!stats.isFile()) {
        skipped.push({ path: relative, reason: 'special_file' });
        continue;
      }
      const extension = normaliseExtension(dirent.name);
      const kind = classifyKind(dirent.name);
      byKind[kind] += 1;
      byExtension.set(extension || '(无扩展名)', (byExtension.get(extension || '(无扩展名)') || 0) + 1);
      fileCount += 1;
      totalBytes += stats.size;
      const topDirectory = relative.includes(path.sep) ? relative.split(path.sep)[0] : '.';
      const directory = directories.get(topDirectory) || { name: topDirectory, fileCount: 0, totalBytes: 0, byKind: Object.fromEntries(KINDS.map((item) => [item, 0])) };
      directory.fileCount += 1;
      directory.totalBytes += stats.size;
      directory.byKind[kind] += 1;
      directories.set(topDirectory, directory);
      if (entries.length < maxEntries) {
        entries.push({
          relativePath: relative,
          name: dirent.name,
          extension,
          kind,
          mimeType: mimeTypeFor(dirent.name, kind),
          sizeBytes: stats.size,
          sourceMtime: stats.mtime.toISOString(),
          sensitive,
        });
      } else {
        truncated = true;
      }
    }
  }

  return {
    sourcePath: realPath,
    displayName: path.basename(realPath) || realPath,
    fileCount,
    totalBytes,
    byKind,
    byExtension: [...byExtension.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 30)
      .map(([extension, count]) => ({ extension, count })),
    directories: [...directories.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh')),
    emptyDirectoryCount: emptyDirectories,
    skipped: skipped.slice(0, 200),
    skippedCount: skipped.length,
    riskFiles: riskFiles.slice(0, 200),
    riskFileCount: riskFiles.length,
    entries,
    entriesTruncated: truncated,
  };
}

function matchesIncludedPath(relativePath, includeRelativePaths) {
  // '.' 表示整棵目录；归一化后为空同样视为全选
  const list = (Array.isArray(includeRelativePaths) ? includeRelativePaths : [])
    .map((item) => String(item || '').trim().replace(/^\.\//, '').replace(/^\/+|\/+$/g, ''))
    .filter((item) => item && item !== '.');
  if (!list.length) return true;
  const normalised = String(relativePath || '').replace(/^\.\//, '');
  return list.some((prefix) => normalised === prefix || normalised.startsWith(`${prefix}/`));
}

module.exports = {
  KINDS,
  SKIP_DIR_NAMES,
  SKIP_FILE_NAMES,
  TEXT_PREVIEW_EXTENSIONS,
  archiveObjectKey,
  classifyKind,
  expandHome,
  isInside,
  isSensitiveName,
  isTextPreviewKind,
  loadArchiveConfig,
  matchesIncludedPath,
  mimeTypeFor,
  normaliseExtension,
  resolveSourcePath,
  scanDirectory,
  shouldSkipDirectory,
  shouldSkipFile,
};
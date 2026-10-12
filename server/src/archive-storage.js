// 归档存储后端：COS（推荐，备份后删除本地文件可释放空间）或 local（开发/外置盘）。
// 统一走流式读写，不把整个文件读进内存；对象 key 由 SHA-256 + 大小决定，不含用户路径。
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const COS = require('cos-nodejs-sdk-v5');

const { archiveObjectKey, isInside, expandHome } = require('./archive-paths');

const fsp = fs.promises;
const ARCHIVE_CACHE_CONTROL = 'private, max-age=31536000, immutable';
const SIGNATURE_EXPIRES_SECONDS = 300;

function storageError(message, code = 'ARCHIVE_STORAGE_ERROR') {
  return Object.assign(new Error(message), { code, statusCode: 500 });
}

function hashStream(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('error', reject);
    stream.on('end', () => resolve(hash.digest('hex')));
  });
}

function encodeCosSegment(segment) {
  return encodeURIComponent(segment).replace(/[!'()*]/g, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
}

function headerValue(headers, name) {
  const target = name.toLowerCase();
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() === target) return Array.isArray(value) ? value[0] : value;
  }
  return '';
}

function promisifyCos(client, method, params) {
  return new Promise((resolve, reject) => {
    client[method](params, (error, data) => (error ? reject(error) : resolve(data)));
  });
}

function createLocalBackend({ localRoot, cosPrefix }) {
  if (!String(localRoot || '').trim()) throw storageError('未配置 ARCHIVE_LOCAL_ROOT，无法使用 local 归档后端', 'ARCHIVE_BACKEND_UNAVAILABLE');
  const root = path.resolve(expandHome(localRoot));
  const objectPath = (key) => {
    const resolved = path.resolve(root, key);
    if (resolved !== root && !isInside(root, resolved)) throw storageError('对象路径越界', 'ARCHIVE_KEY_INVALID');
    return resolved;
  };
  return {
    backend: 'local',
    root,
    objectPath,
    async prepare() {
      await fsp.mkdir(root, { recursive: true });
      return { root };
    },
    buildKey({ userId, sha256, sizeBytes }) {
      return archiveObjectKey({ prefix: cosPrefix, userId, sha256, sizeBytes });
    },
    async putObject({ key, filePath, contentType, sha256, sizeBytes }) {
      const target = objectPath(key);
      const temp = `${target}.${crypto.randomUUID()}.part`;
      await fsp.mkdir(path.dirname(target), { recursive: true });
      try {
        await new Promise((resolve, reject) => {
          const input = fs.createReadStream(filePath);
          const output = fs.createWriteStream(temp);
          input.on('error', reject);
          output.on('error', reject);
          output.on('finish', resolve);
          input.pipe(output);
        });
        const written = (await fsp.stat(temp)).size;
        if (Number(sizeBytes) && written !== Number(sizeBytes)) {
          throw storageError(`本地写入大小不一致：期望 ${sizeBytes}，实际 ${written}`, 'ARCHIVE_SIZE_MISMATCH');
        }
        // 原子 rename：校验通过后才成为正式对象，避免中断留下半个对象被当成可用对象。
        await fsp.rename(temp, target);
        await fsp.writeFile(`${target}.meta.json`, JSON.stringify({ sha256, sizeBytes: written, contentType, verifiedAt: new Date().toISOString() }), 'utf8');
        return { sizeBytes: written };
      } catch (error) {
        await fsp.unlink(temp).catch(() => {});
        throw error;
      }
    },
    async headObject(key) {
      const target = objectPath(key);
      const stats = await fsp.stat(target).catch(() => null);
      if (!stats || !stats.isFile()) throw storageError('本地归档对象不存在', 'ARCHIVE_OBJECT_NOT_FOUND');
      let meta = {};
      try {
        meta = JSON.parse(await fsp.readFile(`${target}.meta.json`, 'utf8'));
      } catch (_) {
        meta = {};
      }
      return {
        sizeBytes: stats.size,
        contentType: meta.contentType || 'application/octet-stream',
        sha256Metadata: meta.sha256 || '',
        modifiedAt: stats.mtime.toISOString(),
      };
    },
    createReadStream(key, { start, end } = {}) {
      return fs.createReadStream(objectPath(key), start === undefined ? {} : { start, end });
    },
    async deleteObject(key) {
      const target = objectPath(key);
      await Promise.all([fsp.unlink(target).catch(() => {}), fsp.unlink(`${target}.meta.json`).catch(() => {})]);
    },
    async signDownloadUrl() {
      return '';
    },
    async writeStreamTo(key, sourceStream, contentType) {
      const target = objectPath(key);
      await fsp.mkdir(path.dirname(target), { recursive: true });
      const temp = `${target}.${crypto.randomUUID()}.part`;
      try {
        await new Promise((resolve, reject) => {
          const output = fs.createWriteStream(temp);
          sourceStream.on('error', reject);
          output.on('error', reject);
          output.on('finish', resolve);
          sourceStream.pipe(output);
        });
        await fsp.rename(temp, target);
        const written = (await fsp.stat(target)).size;
        await fsp.writeFile(`${target}.meta.json`, JSON.stringify({ sizeBytes: written, contentType, verifiedAt: new Date().toISOString() }), 'utf8');
        return { sizeBytes: written };
      } catch (error) {
        await fsp.unlink(temp).catch(() => {});
        throw error;
      }
    },
  };
}

function createCosBackend({ bucket, region, sliceCacheDir, cosPrefix = 'archives' }) {
  if (!bucket || !region) throw storageError('COS 配置不完整，无法使用 cos 归档后端', 'ARCHIVE_BACKEND_UNAVAILABLE');
  const client = new COS({
    SecretId: process.env.TENCENT_COS_SECRET_ID,
    SecretKey: process.env.TENCENT_COS_SECRET_KEY,
    Protocol: 'https:',
    // 分片上传缓存目录：进程重启后可凭缓存的 UploadId 续传未完成的大文件
    ...(sliceCacheDir ? { ConfCwd: sliceCacheDir } : {}),
  });
  return {
    backend: 'cos',
    async prepare() {
      if (sliceCacheDir) await fsp.mkdir(sliceCacheDir, { recursive: true });
      return { bucket, region };
    },
    buildKey({ userId, sha256, sizeBytes }) {
      return archiveObjectKey({ prefix: cosPrefix, userId, sha256, sizeBytes });
    },
    async putObject({ key, filePath, contentType, sha256, sizeBytes, multipartThresholdBytes }) {
      const meta = { 'x-cos-meta-sha256': sha256, 'x-cos-meta-size-bytes': String(sizeBytes) };
      const useMultipart = Number(sizeBytes) >= Number(multipartThresholdBytes || 0);
      if (useMultipart) {
        await promisifyCos(client, 'sliceUploadFile', {
          Bucket: bucket,
          Region: region,
          Key: key,
          FilePath: filePath,
          ContentType: contentType || 'application/octet-stream',
          CacheControl: ARCHIVE_CACHE_CONTROL,
          ...meta,
        });
      } else {
        await promisifyCos(client, 'putObject', {
          Bucket: bucket,
          Region: region,
          Key: key,
          Body: fs.createReadStream(filePath),
          ContentLength: Number(sizeBytes),
          ContentType: contentType || 'application/octet-stream',
          CacheControl: ARCHIVE_CACHE_CONTROL,
          ...meta,
        });
      }
      return { sizeBytes: Number(sizeBytes), multipart: useMultipart };
    },
    async headObject(key) {
      const data = await promisifyCos(client, 'headObject', { Bucket: bucket, Region: region, Key: key });
      return {
        sizeBytes: Number(headerValue(data.headers, 'content-length') || 0),
        contentType: headerValue(data.headers, 'content-type') || 'application/octet-stream',
        sha256Metadata: headerValue(data.headers, 'x-cos-meta-sha256'),
        etag: headerValue(data.headers, 'etag'),
        modifiedAt: headerValue(data.headers, 'last-modified'),
      };
    },
    pipeTo(key, output, { start, end } = {}) {
      const headers = start === undefined ? {} : { Range: `bytes=${start}-${end}` };
      return promisifyCos(client, 'getObject', { Bucket: bucket, Region: region, Key: key, Headers: headers, Output: output });
    },
    async deleteObject(key) {
      await promisifyCos(client, 'deleteObject', { Bucket: bucket, Region: region, Key: key });
    },
    async signDownloadUrl(key, { filename, inline = false }) {
      const encoded = encodeURIComponent(String(filename || 'download')).replace(/[!'()*]/g, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
      const disposition = inline ? 'inline' : 'attachment';
      const data = await promisifyCos(client, 'getObjectUrl', {
        Bucket: bucket,
        Region: region,
        Key: key,
        Sign: true,
        Expires: SIGNATURE_EXPIRES_SECONDS,
        Query: {
          'response-content-disposition': `${disposition}; filename="download"; filename*=UTF-8''${encoded}`,
          ...(inline ? {} : { 'response-content-type': 'application/octet-stream' }),
        },
      });
      return data?.Url || '';
    },
    async writeStreamTo(key, sourceStream) {
      throw storageError('COS 后端不支持直接写入响应流', 'ARCHIVE_BACKEND_UNAVAILABLE');
    },
  };
}

// local 后端把源文件复制到同一块磁盘不会释放空间：这里只做判断，实际删除由批次清理接口拦截。
async function isSameDevice(firstPath, secondPath) {
  try {
    const [first, second] = await Promise.all([fsp.stat(firstPath), fsp.stat(secondPath)]);
    return first.dev === second.dev;
  } catch (_) {
    return false;
  }
}

function createArchiveStorage({ config, bucket, region, sliceCacheDir }) {
  return config.storageBackend === 'local'
    ? createLocalBackend({ localRoot: config.localRoot, cosPrefix: config.cosPrefix })
    : createCosBackend({ bucket, region, sliceCacheDir, cosPrefix: config.cosPrefix });
}

module.exports = {
  ARCHIVE_CACHE_CONTROL,
  SIGNATURE_EXPIRES_SECONDS,
  createArchiveStorage,
  createCosBackend,
  createLocalBackend,
  hashStream,
  headerValue,
  isSameDevice,
  storageError,
};
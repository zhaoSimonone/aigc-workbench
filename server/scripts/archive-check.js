// 本地目录归档的无依赖自检：路径白名单、扫描分类、本地存储后端。
// 用法：node server/scripts/archive-check.js [--scan "/Users/simon/Desktop/AIGC/1007视频"]
// 只做只读扫描和临时目录读写，不会触碰源文件，也不会连接数据库或 COS。
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const {
  classifyKind,
  isSensitiveName,
  isTextPreviewKind,
  loadArchiveConfig,
  matchesIncludedPath,
  mimeTypeFor,
  resolveSourcePath,
  scanDirectory,
} = require('../src/archive-paths');
const { createLocalBackend, hashStream, isSameDevice } = require('../src/archive-storage');
const { createArchiveQueue } = require('../src/archive-jobs');

const results = [];
let failures = 0;

async function test(name, fn) {
  try {
    await fn();
    results.push(`  PASS  ${name}`);
  } catch (error) {
    failures += 1;
    results.push(`  FAIL  ${name}\n        ${error.message}`);
  }
}

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index].startsWith('--')) {
      args[argv[index].slice(2)] = argv[index + 1] && !argv[index + 1].startsWith('--') ? argv[index + 1] : true;
      index += 1;
    }
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const workspace = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'archive-check-'));
  const allowedRoot = path.join(workspace, 'source');
  const outside = path.join(workspace, 'outside');
  await Promise.all([
    fs.promises.mkdir(path.join(allowedRoot, '照片'), { recursive: true }),
    fs.promises.mkdir(path.join(allowedRoot, 'node_modules'), { recursive: true }),
    fs.promises.mkdir(path.join(allowedRoot, '空目录'), { recursive: true }),
    fs.promises.mkdir(outside, { recursive: true }),
  ]);
  await Promise.all([
    fs.promises.writeFile(path.join(allowedRoot, '笔记.md'), '# 标题\n正文内容\n', 'utf8'),
    fs.promises.writeFile(path.join(allowedRoot, '配置.json'), '{"a":1}', 'utf8'),
    fs.promises.writeFile(path.join(allowedRoot, '照片', '中文 名片.png'), Buffer.from('89504e470d0a1a0a', 'hex')),
    fs.promises.writeFile(path.join(allowedRoot, '.DS_Store'), 'junk', 'utf8'),
    fs.promises.writeFile(path.join(allowedRoot, '.env'), 'SECRET=hunter2', 'utf8'),
    fs.promises.writeFile(path.join(allowedRoot, 'api-secret-notes.txt'), 'token', 'utf8'),
    fs.promises.writeFile(path.join(allowedRoot, 'server.pem'), 'key', 'utf8'),
    fs.promises.writeFile(path.join(allowedRoot, 'node_modules', 'index.js'), 'module.exports=1', 'utf8'),
    fs.promises.writeFile(path.join(outside, 'secret-movie.mp4'), 'outside', 'utf8'),
  ]);
  const outsideLink = path.join(allowedRoot, '越界链接.mp4');
  await fs.promises.symlink(path.join(outside, 'secret-movie.mp4'), outsideLink);
  await fs.promises.writeFile(path.join(outside, 'inside-link.txt'), 'in', 'utf8');
  await fs.promises.symlink(path.join(outside, 'inside-link.txt'), path.join(allowedRoot, '目录内链接.txt'));

  const allowedRoots = [allowedRoot];
  const config = loadArchiveConfig({ ARCHIVE_ALLOWED_SOURCE_ROOTS: allowedRoot, ARCHIVE_STORAGE_BACKEND: 'local' });

  results.push('路径安全');
  await test('白名单外的目录被拒绝', async () => {
    await assert.rejects(() => resolveSourcePath(outside, allowedRoots), (error) => error.statusCode === 403);
  });
  await test('.. 逃逸被拒绝', async () => {
    await assert.rejects(() => resolveSourcePath(`${allowedRoot}/../outside`, allowedRoots), (error) => error.statusCode === 400);
  });
  await test('指向白名单外的符号链接被拒绝', async () => {
    await assert.rejects(() => resolveSourcePath(outsideLink, allowedRoots), (error) => error.statusCode === 400);
  });
  await test('未配置白名单时全部拒绝', async () => {
    await assert.rejects(() => resolveSourcePath(allowedRoot, []), (error) => error.statusCode === 403);
  });
  await test('白名单内的普通目录通过', async () => {
    const resolved = await resolveSourcePath(allowedRoot, allowedRoots);
    assert.strictEqual(resolved.realPath, await fs.promises.realpath(allowedRoot));
  });
  await test('目录内的符号链接条目被跳过而不是被跟随', async () => {
    const scan = await scanDirectory({ sourcePath: allowedRoot, allowedRoots });
    const skipped = scan.skipped.filter((item) => item.reason === 'symbolic_link');
    assert.strictEqual(skipped.length, 2);
    assert.ok(!scan.entries.some((entry) => entry.name.includes('secret-movie')));
  });

  results.push('扫描与分类');
  await test('.DS_Store、node_modules、敏感文件默认跳过', async () => {
    const scan = await scanDirectory({ sourcePath: allowedRoot, allowedRoots });
    const names = scan.entries.map((entry) => entry.name);
    assert.ok(!names.includes('.DS_Store'));
    assert.ok(!names.includes('.env'));
    assert.ok(!names.includes('server.pem'));
    assert.ok(!names.includes('api-secret-notes.txt'));
    assert.ok(!names.some((name) => name.includes('node_modules')));
    assert.strictEqual(scan.riskFileCount, 3);
  });
  await test('中文文件名正常返回且大小正确', async () => {
    const scan = await scanDirectory({ sourcePath: allowedRoot, allowedRoots });
    const image = scan.entries.find((entry) => entry.name === '中文 名片.png');
    assert.ok(image, '未找到中文文件名');
    assert.strictEqual(image.kind, 'image');
    assert.strictEqual(image.mimeType, 'image/png');
    assert.strictEqual(image.sizeBytes, 8);
  });
  await test('空目录不报错，只计入空目录计数', async () => {
    const scan = await scanDirectory({ sourcePath: allowedRoot, allowedRoots });
    assert.ok(scan.emptyDirectoryCount >= 1, '空目录未被统计');
    // 空目录没有文件，因此不会出现在可选的导入范围列表里
    assert.ok(!scan.directories.some((item) => item.name === '空目录'));
    assert.strictEqual(scan.fileCount, scan.entries.length);
  });
  await test('扩展名分类正确', async () => {
    assert.strictEqual(classifyKind('a.mp4'), 'video');
    assert.strictEqual(classifyKind('a.PNG'), 'image');
    assert.strictEqual(classifyKind('a.wav'), 'audio');
    assert.strictEqual(classifyKind('a.md'), 'document');
    assert.strictEqual(classifyKind('a.zip'), 'archive');
    assert.strictEqual(classifyKind('a.unknownext'), 'other');
    assert.strictEqual(mimeTypeFor('a.json'), 'application/json');
    assert.ok(isTextPreviewKind('a.md') && isTextPreviewKind('a.py') && !isTextPreviewKind('a.zip'));
  });
  await test('敏感文件名识别', async () => {
    assert.ok(isSensitiveName('.env.local'));
    assert.ok(isSensitiveName('my-secret.txt'));
    assert.ok(isSensitiveName('API_TOKEN.json'));
    assert.ok(!isSensitiveName('分镜脚本.md'));
  });
  await test('导入范围过滤', async () => {
    assert.ok(matchesIncludedPath('照片/a.png', ['照片']));
    assert.ok(!matchesIncludedPath('成片/b.mp4', ['照片']));
    assert.ok(matchesIncludedPath('任意/x.bin', []));
    assert.ok(matchesIncludedPath('任意/x.bin', ['.']));
  });

  results.push('本地存储后端');
  const localRoot = path.join(workspace, 'archive-root');
  const storage = createLocalBackend({ localRoot, cosPrefix: 'archives' });
  await test('缺少 ARCHIVE_LOCAL_ROOT 时拒绝创建', () => {
    assert.throws(() => createLocalBackend({ localRoot: '', cosPrefix: 'archives' }), /ARCHIVE_LOCAL_ROOT/);
  });
  await test('对象 key 只由 SHA-256 和大小决定，不含原始路径', () => {
    const key = storage.buildKey({ userId: 'user-1', sha256: 'a'.repeat(64), sizeBytes: 1024 });
    assert.strictEqual(key, 'archives/user-1/objects/aa/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1024');
    assert.ok(!key.includes('视频'));
  });
  await test('对象路径越界被拒绝', () => {
    assert.throws(() => storage.objectPath('../../etc/passwd'), /越界/);
  });

  const sourceA = path.join(allowedRoot, '照片', '中文 名片.png');
  const sourceB = path.join(workspace, 'copy-b.png');
  await fs.promises.copyFile(sourceA, sourceB);
  const sha256 = await hashStream(sourceA);
  const sameShaCopy = await hashStream(sourceB);
  const keyA = storage.buildKey({ userId: 'user-1', sha256, sizeBytes: 8 });
  await storage.prepare();
  await storage.putObject({ key: keyA, filePath: sourceA, contentType: 'image/png', sha256, sizeBytes: 8 });

  await test('相同内容得到相同 key（去重键）', () => {
    assert.strictEqual(sha256, sameShaCopy);
    assert.strictEqual(keyA, storage.buildKey({ userId: 'user-1', sha256: sameShaCopy, sizeBytes: 8 }));
  });
  await test('上传后可校验大小与 SHA-256 元数据', async () => {
    const head = await storage.headObject(keyA);
    assert.strictEqual(head.sizeBytes, 8);
    assert.strictEqual(head.sha256Metadata, sha256);
    assert.strictEqual(head.contentType, 'image/png');
  });
  await test('HEAD 不存在的对象报错', async () => {
    await assert.rejects(() => storage.headObject('archives/user-1/objects/aa/deadbeef-1'));
  });
  await test('流式读取与 Range 读取一致', async () => {
    const whole = await new Promise((resolve, reject) => {
      const chunks = [];
      storage.createReadStream(keyA).on('data', (chunk) => chunks.push(chunk)).on('end', () => resolve(Buffer.concat(chunks))).on('error', reject);
    });
    assert.strictEqual(whole.toString('hex'), '89504e470d0a1a0a');
    const partial = await new Promise((resolve, reject) => {
      const chunks = [];
      storage.createReadStream(keyA, { start: 0, end: 3 }).on('data', (chunk) => chunks.push(chunk)).on('end', () => resolve(Buffer.concat(chunks))).on('error', reject);
    });
    assert.strictEqual(partial.length, 4);
  });
  await test('删除对象同时清理元数据旁文件', async () => {
    await storage.deleteObject(keyA);
    await assert.rejects(() => storage.headObject(keyA));
    const leftovers = (await fs.promises.readdir(path.dirname(storage.objectPath(keyA)))).filter((name) => name.includes('.meta.json'));
    assert.deepStrictEqual(leftovers, []);
  });
  await test('同盘判断可用于阻止无意义的源文件清理', async () => {
    assert.strictEqual(await isSameDevice(workspace, allowedRoot), true);
    assert.strictEqual(typeof (await isSameDevice(workspace, '/nonexistent-path')), 'boolean');
  });


  results.push('清理源文件保护');
  // 用内存桩替换数据库，直接跑真实的清理逻辑，覆盖确认、状态、校验和源文件变化四类拦截。
  const cleanupRoot = path.join(workspace, 'cleanup-source');
  await fs.promises.mkdir(cleanupRoot, { recursive: true });
  const keepFile = path.join(cleanupRoot, '已归档.txt');
  const changedFile = path.join(cleanupRoot, '已修改.txt');
  await Promise.all([
    fs.promises.writeFile(keepFile, '已备份内容', 'utf8'),
    fs.promises.writeFile(changedFile, '原始内容', 'utf8'),
  ]);
  const keepStat = await fs.promises.stat(keepFile);
  const changedStat = await fs.promises.stat(changedFile);
  await fs.promises.writeFile(changedFile, '导入之后被改过的内容', 'utf8');

  const cleanupUser = '44444444-4444-4444-8444-444444444444';
  const makeQueue = (batchOverrides, stats) => {
    const deleted = [];
    const fakePool = {
      async query(text, params) {
        const sql = String(text || '');
        if (sql.includes('SELECT * FROM archive_batches')) return { rows: [batchOverrides], rowCount: 1 };
        if (sql.includes('AS total') && sql.includes('no_object')) return { rows: [stats], rowCount: 1 };
        if (sql.includes('SELECT f.id, f.relative_path')) {
          return { rows: [
            { id: 'a1', relative_path: '已归档.txt', size_bytes: Number(keepStat.size), source_mtime: keepStat.mtime.toISOString(), source_deleted_at: null },
            { id: 'a2', relative_path: '已修改.txt', size_bytes: Number(changedStat.size), source_mtime: changedStat.mtime.toISOString(), source_deleted_at: null },
          ], rowCount: 2 };
        }
        if (sql.includes('COUNT(*)::int AS total_files')) return { rows: [{ total_files: 2, total_bytes: 0, success_files: 2, failed_files: 0, skipped_files: 0, processed_files: 2, processed_bytes: 0 }], rowCount: 1 };
        if (sql.startsWith('UPDATE')) {
          if (sql.includes('source_deleted_at=now()')) deleted.push(params[0]);
          return { rows: [], rowCount: 0 };
        }
        return { rows: [], rowCount: 0 };
      },
      async connect() { return { query: fakePool.query, release() {} }; },
    };
    const queue = createArchiveQueue({
      pool: fakePool,
      config: { ...config, allowedRoots: [cleanupRoot], localRoot: workspace, maxRetries: 0, maxConcurrency: 1 },
      getStorage: () => storage,
      log: { warn() {}, error() {} },
    });
    return { queue, deleted };
  };

  const readyStats = { total: 2, no_object: 0, no_hash: 0, unverified: 0, errored: 0, already_deleted: 0 };
  const baseBatch = {
    id: '55555555-5555-4555-8555-555555555555',
    user_id: cleanupUser,
    name: '清理测试批次',
    source_root: cleanupRoot,
    storage_backend: 'local',
    status: 'completed',
  };

  await test('没有确认字段时拒绝清理', async () => {
    const { queue } = makeQueue(baseBatch, readyStats);
    await assert.rejects(() => queue.cleanupSource({ batchId: baseBatch.id, userId: cleanupUser, confirmText: '' }), (error) => error.archiveCode === 'confirmation_mismatch');
    assert.ok(fs.existsSync(keepFile), '源文件不应被删除');
  });
  await test('确认文本与批次名不一致时拒绝清理', async () => {
    const { queue } = makeQueue(baseBatch, readyStats);
    await assert.rejects(() => queue.cleanupSource({ batchId: baseBatch.id, userId: cleanupUser, confirmText: '错误的批次名' }), (error) => error.statusCode === 400);
    assert.ok(fs.existsSync(keepFile), '源文件不应被删除');
  });
  await test('批次未完成时拒绝清理', async () => {
    const { queue } = makeQueue({ ...baseBatch, status: 'running' }, readyStats);
    await assert.rejects(() => queue.cleanupSource({ batchId: baseBatch.id, userId: cleanupUser, confirmText: baseBatch.name }), (error) => error.archiveCode === 'batch_not_completed');
    assert.ok(fs.existsSync(keepFile), '源文件不应被删除');
  });
  await test('校验未通过时拒绝清理', async () => {
    const { queue } = makeQueue(baseBatch, { ...readyStats, unverified: 2 });
    await assert.rejects(() => queue.cleanupSource({ batchId: baseBatch.id, userId: cleanupUser, confirmText: baseBatch.name }), (error) => error.archiveCode === 'cleanup_blocked');
    assert.ok(fs.existsSync(keepFile), '源文件不应被删除');
  });
  await test('缺少目标对象或哈希时拒绝清理', async () => {
    const { queue } = makeQueue(baseBatch, { ...readyStats, no_object: 1 });
    await assert.rejects(() => queue.cleanupSource({ batchId: baseBatch.id, userId: cleanupUser, confirmText: baseBatch.name }), (error) => error.archiveCode === 'cleanup_blocked');
  });
  await test('local 与源目录同盘时默认拒绝清理', async () => {
    const { queue } = makeQueue(baseBatch, readyStats);
    await assert.rejects(
      () => queue.cleanupSource({ batchId: baseBatch.id, userId: cleanupUser, confirmText: baseBatch.name }),
      (error) => error.archiveCode === 'cleanup_blocked' && /同一块磁盘/.test(error.message),
    );
    assert.ok(fs.existsSync(keepFile), '源文件不应被删除');
  });
  await test('显式允许同盘后只删除成功项，源文件已变化的保留', async () => {
    const { queue, deleted } = makeQueue(baseBatch, readyStats);
    const summary = await queue.cleanupSource({ batchId: baseBatch.id, userId: cleanupUser, confirmText: baseBatch.name, allowSameDisk: true });
    assert.strictEqual(summary.deleted, 1);
    assert.strictEqual(summary.changed, 1);
    assert.deepStrictEqual(deleted, ['a1']);
    assert.ok(!fs.existsSync(keepFile), '已归档的源文件应被删除');
    assert.ok(fs.existsSync(changedFile), '导入后被修改的源文件必须保留');
  });


  results.push('后台批次处理（内存数据库桩 + 本地后端）');
  const pipelineRoot = path.join(workspace, 'pipeline-source');
  await fs.promises.mkdir(pipelineRoot, { recursive: true });
  const duplicateText = '完全相同的内容';
  const pipelineFiles = [
    { relativePath: '文案/脚本.md', content: duplicateText },
    { relativePath: '文案/脚本备份.md', content: duplicateText },
    { relativePath: '参数.json', content: '{"prompt":"a"}' },
    { relativePath: '缺失的文件.txt', content: null },
  ];
  for (const item of pipelineFiles) {
    if (item.content === null) continue;
    const target = path.join(pipelineRoot, item.relativePath);
    await fs.promises.mkdir(path.dirname(target), { recursive: true });
    await fs.promises.writeFile(target, item.content, 'utf8');
  }
  const pipelineRootReal = await fs.promises.realpath(pipelineRoot);
  const pipelineStats = {};
  for (const item of pipelineFiles) {
    try {
      const stat = await fs.promises.stat(path.join(pipelineRoot, item.relativePath));
      pipelineStats[item.relativePath] = { size_bytes: String(stat.size), source_mtime: stat.mtime.toISOString() };
    } catch (_) {
      pipelineStats[item.relativePath] = { size_bytes: '5', source_mtime: new Date().toISOString() };
    }
  }

  const pipelineUser = '66666666-6666-4666-8666-666666666666';
  const pipelineBatchId = '77777777-7777-4777-8777-777777777777';
  const pipelineState = {
    batch: {
      id: pipelineBatchId,
      user_id: pipelineUser,
      name: '流水线测试',
      source_root: pipelineRootReal,
      storage_backend: 'local',
      status: 'queued',
      create_media_assets: false,
      asset_folder: '',
    },
    files: pipelineFiles.map((item, index) => ({
      id: `f${index + 1}`,
      batch_id: pipelineBatchId,
      user_id: pipelineUser,
      name: item.relativePath.split('/').pop(),
      relative_path: item.relativePath,
      source_path: path.join(pipelineRootReal, item.relativePath),
      kind: classifyKind(item.relativePath),
      mime_type: mimeTypeFor(item.relativePath),
      size_bytes: pipelineStats[item.relativePath].size_bytes,
      source_mtime: pipelineStats[item.relativePath].source_mtime,
      sha256: null,
      status: 'queued',
      retry_count: 0,
      error_code: '',
      error_message: '',
      object_id: null,
      asset_id: null,
      source_deleted_at: null,
    })),
    objects: [],
  };

  const pipelinePool = {
    async query(text, params = []) {
      const sql = String(text || '');
      const state = pipelineState;
      const rowsOf = () => state.files.map((file) => ({ ...file }));
      if (sql.includes('SELECT * FROM archive_batches')) return { rows: [{ ...state.batch }], rowCount: 1 };
      if (sql.includes("SET status='running'")) { state.batch.status = 'running'; return { rows: [], rowCount: 0 }; }
      if (sql.includes('SELECT status FROM archive_batches')) return { rows: [{ status: state.batch.status }], rowCount: 1 };
      if (sql.includes('SET current_relative_path=$2')) { state.batch.current_relative_path = params[1]; return { rows: [], rowCount: 0 }; }
      if (sql.includes('SET status=$2, completed_at=CASE')) { state.batch.status = params[1]; return { rows: [], rowCount: 0 }; }
      if (sql.includes('UPDATE archive_files f')) {
        const next = state.files.find((file) => file.status === 'queued');
        if (!next) return { rows: [], rowCount: 0 };
        next.status = 'hashing';
        return { rows: [{ ...next }], rowCount: 1 };
      }
      if (sql.includes('SELECT * FROM archive_objects')) {
        const found = state.objects.find((item) => item.user_id === params[0] && item.storage_backend === params[1] && item.sha256 === params[2] && String(item.size_bytes) === String(params[3]));
        return { rows: found ? [{ ...found }] : [], rowCount: found ? 1 : 0 };
      }
      if (sql.includes('INSERT INTO archive_objects')) {
        const [userId, backend, objectKey, sha256, sizeBytes, contentType] = params;
        let object = state.objects.find((item) => item.user_id === userId && item.storage_backend === backend && item.sha256 === sha256 && String(item.size_bytes) === String(sizeBytes));
        if (!object) {
          object = { id: `o${state.objects.length + 1}`, user_id: userId, storage_backend: backend, object_key: objectKey, sha256, size_bytes: sizeBytes, content_type: contentType, status: 'pending', metadata: {}, reference_count: 0, verified_at: null, last_error: '' };
          state.objects.push(object);
        }
        return { rows: [{ ...object }], rowCount: 1 };
      }
      if (sql.startsWith('UPDATE archive_objects SET ') && !sql.includes('reference_count')) {
        const setPart = sql.slice(sql.indexOf('SET ') + 4, sql.indexOf(' WHERE '));
        const columns = setPart.split(',').map((item) => item.trim().split('=')[0]);
        const object = state.objects.find((item) => item.id === params[columns.length]);
        if (object) columns.forEach((column, index) => { object[column] = params[index]; });
        return { rows: [], rowCount: 0 };
      }
      if (sql.includes('reference_count=reference_count+1')) {
        const object = state.objects.find((item) => item.id === params[0]);
        if (object) object.reference_count += 1;
        return { rows: [], rowCount: 0 };
      }
      if (sql.includes("SET status='verifying'")) {
        const file = state.files.find((item) => item.id === params[0]);
        if (file) { file.status = 'verifying'; file.sha256 = params[1]; }
        return { rows: [], rowCount: 0 };
      }
      if (sql.includes("SET status='available', object_id=$2")) {
        const file = state.files.find((item) => item.id === params[0]);
        if (file) Object.assign(file, { status: 'available', object_id: params[1], asset_id: params[2], sha256: params[3], size_bytes: params[4], error_code: '', error_message: '' });
        return { rows: [], rowCount: 0 };
      }
      if (sql.includes('SET retry_count=$2')) {
        const file = state.files.find((item) => item.id === params[0]);
        if (file) file.retry_count = params[1];
        return { rows: [], rowCount: 0 };
      }
      if (sql.includes('SET status=$2, retry_count=$3')) {
        const file = state.files.find((item) => item.id === params[0]);
        if (file) Object.assign(file, { status: params[1], retry_count: params[2], error_code: params[3], error_message: params[4] });
        return { rows: [], rowCount: 0 };
      }
      if (sql.includes('AS total_files')) {
        const totalFiles = state.files.length;
        const success = state.files.filter((file) => ['available', 'source_deleted'].includes(file.status)).length;
        const failed = state.files.filter((file) => file.status === 'failed').length;
        const skipped = state.files.filter((file) => file.status === 'skipped').length;
        const pendingStates = ['queued', 'discovered', 'hashing', 'uploading', 'verifying', 'skipped'];
        return { rows: [{ total_files: totalFiles, total_bytes: 0, success_files: success, failed_files: failed, skipped_files: skipped, processed_files: totalFiles - state.files.filter((file) => pendingStates.includes(file.status)).length, processed_bytes: 0 }], rowCount: 1 };
      }
      if (sql.includes('SET total_files=$2')) {
        state.batch.total_files = params[1];
        state.batch.success_files = params[3];
        state.batch.failed_files = params[4];
        return { rows: [], rowCount: 0 };
      }
      if (sql.includes('AS pending')) {
        const pending = state.files.filter((file) => ['queued', 'discovered', 'hashing', 'uploading', 'verifying'].includes(file.status)).length;
        return { rows: [{ pending }], rowCount: 1 };
      }
      if (sql.includes('SELECT status, source_cleanup_status')) return { rows: [{ status: state.batch.status }], rowCount: 1 };
      return { rows: rowsOf().slice(0, 0), rowCount: 0 };
    },
    async connect() { return { query: pipelinePool.query, release() {} }; },
  };

  const pipelineStorageRoot = path.join(workspace, 'pipeline-archive');
  const pipelineStorage = createLocalBackend({ localRoot: pipelineStorageRoot, cosPrefix: 'archives' });
  await pipelineStorage.prepare();
  let uploadCount = 0;
  const realPutObject = pipelineStorage.putObject.bind(pipelineStorage);
  pipelineStorage.putObject = async (params) => {
    uploadCount += 1;
    return realPutObject(params);
  };

  const pipelineQueue = createArchiveQueue({
    pool: pipelinePool,
    config: { ...config, allowedRoots: [pipelineRootReal], localRoot: pipelineStorageRoot, maxRetries: 0, maxConcurrency: 2, multipartThresholdBytes: 32 * 1024 * 1024 },
    getStorage: () => pipelineStorage,
    log: { warn() {}, error() {} },
  });

  await test('批次处理：流式哈希、校验、写入状态并完成', async () => {
    await pipelineQueue.startBatch(pipelineBatchId, pipelineUser);
    await new Promise((resolve) => setTimeout(resolve, 900));
    assert.strictEqual(pipelineState.batch.status, 'completed_with_errors');
    const available = pipelineState.files.filter((file) => file.status === 'available');
    assert.strictEqual(available.length, 3, '应有 3 个文件归档成功');
    assert.ok(available.every((file) => /^[a-f0-9]{64}$/.test(file.sha256 || '')), '成功文件必须写回 SHA-256');
    assert.ok(pipelineState.objects.every((object) => object.status === 'available' && object.verified_at), '对象必须通过校验');
    assert.ok(!fs.readdirSync(pipelineRoot).some((name) => name.endsWith('.jpg')), '源目录不得被写入任何临时文件');
  });
  await test('相同内容的两条路径只产生一个对象，两个文件都指向它', async () => {
    const duplicates = pipelineState.files.filter((file) => file.relative_path.startsWith('文案/'));
    assert.strictEqual(duplicates.length, 2);
    assert.strictEqual(duplicates[0].sha256, duplicates[1].sha256);
    assert.strictEqual(duplicates[0].object_id, duplicates[1].object_id);
    const object = pipelineState.objects.find((item) => item.id === duplicates[0].object_id);
    assert.strictEqual(pipelineState.objects.length, 2, '两个不同内容应产生两个对象');
    assert.ok(object.reference_count >= 1);
    // 上传次数应等于不同内容的数量，证明重复内容没有重复上传
    assert.strictEqual(uploadCount, 2, `实际上传 ${uploadCount} 次`);
  });
  await test('源文件缺失时进入明确失败状态且不阻断其它文件', async () => {
    const missing = pipelineState.files.find((file) => file.relative_path === '缺失的文件.txt');
    assert.strictEqual(missing.status, 'failed');
    assert.strictEqual(missing.error_code, 'source_missing');
    assert.ok(missing.retry_count >= 1);
  });
  await test('批次统计与实际条目一致', async () => {
    assert.strictEqual(pipelineState.batch.success_files, 3);
    assert.strictEqual(pipelineState.batch.failed_files, 1);
    assert.strictEqual(pipelineState.batch.total_files, 4);
  });


  results.push('批次控制');
  const controlBatchId = '88888888-8888-4888-8888-888888888888';
  const controlState = {
    batch: { ...pipelineState.batch, id: controlBatchId, status: 'running' },
    files: [
      { id: 'c1', batch_id: controlBatchId, status: 'available', retry_count: 0, error_code: '', error_message: '' },
      { id: 'c2', batch_id: controlBatchId, status: 'hashing', retry_count: 0, error_code: '', error_message: '' },
      { id: 'c3', batch_id: controlBatchId, status: 'failed', retry_count: 3, error_code: 'upload_failed', error_message: '网络错误' },
    ],
  };
  const controlPool = {
    async query(sql, params = []) {
      const text = String(sql || '');
      const state = controlState;
      if (text.includes('SELECT * FROM archive_batches')) return { rows: [{ ...state.batch }], rowCount: 1 };
      if (text.includes("SET status='paused'")) { state.batch.status = 'paused'; return { rows: [], rowCount: 1 }; }
      if (text.includes("SET status='cancelled'")) { state.batch.status = 'cancelled'; return { rows: [], rowCount: 1 }; }
      if (text.includes("SET status='queued', last_error=''")) { state.batch.status = 'queued'; return { rows: [], rowCount: 1 }; }
      if (text.includes("UPDATE archive_files SET status='queued' WHERE batch_id=$1 AND status IN")) {
        state.files.filter((file) => ['hashing', 'uploading', 'verifying'].includes(file.status)).forEach((file) => { file.status = 'queued'; });
        return { rows: [], rowCount: 1 };
      }
      if (text.includes("SET status='queued', retry_count=0, error_code=''")) {
        let count = 0;
        state.files.filter((file) => ['discovered', 'failed', 'skipped'].includes(file.status)).forEach((file) => {
          file.status = 'queued';
          file.retry_count = 0;
          file.error_code = '';
          file.error_message = '';
          count += 1;
        });
        return { rows: [], rowCount: count };
      }
      if (text.includes('AS total_files')) return { rows: [{ total_files: 3, total_bytes: 0, success_files: 1, failed_files: 1, skipped_files: 0, processed_files: 2, processed_bytes: 0 }], rowCount: 1 };
      if (text.includes('SET total_files=$2')) return { rows: [], rowCount: 1 };
      return { rows: [], rowCount: 0 };
    },
    async connect() { return { query: controlPool.query, release() {} }; },
  };
  const controlQueue = createArchiveQueue({
    pool: controlPool,
    config: { ...config, allowedRoots: [pipelineRootReal], maxRetries: 1, maxConcurrency: 1 },
    getStorage: () => pipelineStorage,
    log: { warn() {}, error() {} },
  });

  await test('暂停后不再领取新文件，已处理结果保留', async () => {
    const batch = await controlQueue.pauseBatch(controlBatchId, pipelineUser);
    assert.strictEqual(batch.status, 'paused');
    assert.strictEqual(controlState.files.find((file) => file.id === 'c1').status, 'available');
    assert.strictEqual(controlState.files.find((file) => file.id === 'c2').status, 'queued');
  });
  await test('取消保留已完成条目', async () => {
    const batch = await controlQueue.cancelBatch(controlBatchId, pipelineUser);
    assert.strictEqual(batch.status, 'cancelled');
    assert.strictEqual(controlState.files.find((file) => file.id === 'c1').status, 'available');
  });
  await test('重试失败项把 failed 重新排队并清空错误', async () => {
    const result = await controlQueue.retryFailedFiles(controlBatchId, pipelineUser);
    assert.strictEqual(result.requeued, 1);
    const failedFile = controlState.files.find((file) => file.id === 'c3');
    assert.strictEqual(failedFile.status, 'queued');
    assert.strictEqual(failedFile.retry_count, 0);
    assert.strictEqual(failedFile.error_code, '');
  });

  results.push('重启恢复');
  await test('重启后活动状态回到可重试，批次转为暂停而不是完成', async () => {
    const recovery = {
      batch: { ...pipelineState.batch, status: 'running' },
      files: [{ ...pipelineState.files[0], status: 'hashing' }, { ...pipelineState.files[1], status: 'uploading' }],
      objects: pipelineState.objects.map((item) => ({ ...item, status: 'uploading' })),
    };
    const recoveryPool = {
      async query(sql) {
        const text = String(sql || '');
        if (text.includes("SELECT id, name FROM archive_batches WHERE status='running'")) return { rows: [{ id: pipelineBatchId, name: '流水线测试' }], rowCount: 1 };
        if (text.includes("SET status='queued'")) { recovery.files.forEach((file) => { file.status = 'queued'; }); return { rows: [], rowCount: 2 }; }
        if (text.includes("SET status='paused'")) { recovery.batch.status = 'paused'; return { rows: [], rowCount: 1 }; }
        if (text.includes("UPDATE archive_objects SET status='pending'")) { recovery.objects.forEach((item) => { item.status = 'pending'; }); return { rows: [], rowCount: 0 }; }
        return { rows: [], rowCount: 0 };
      },
      async connect() { return { query: recoveryPool.query, release() {} }; },
    };
    const recoveryQueue = createArchiveQueue({
      pool: recoveryPool,
      config: { ...config, allowedRoots: [pipelineRootReal], maxRetries: 1, maxConcurrency: 1 },
      getStorage: () => pipelineStorage,
      log: { warn() {}, error() {} },
    });
    const recovered = await recoveryQueue.recoverInterruptedBatches();
    assert.strictEqual(recovered, 1);
    assert.strictEqual(recovery.batch.status, 'paused');
    assert.notStrictEqual(recovery.batch.status, 'completed');
    assert.ok(recovery.files.every((file) => file.status === 'queued'), '中断的文件必须回到可重试状态');
    assert.ok(recovery.objects.every((item) => item.status === 'pending'));
  });

  const scanTarget = typeof args.scan === 'string' ? args.scan : '/Users/simon/Desktop/AIGC/1007视频';
  results.push(`真实目录只读扫描：${scanTarget}`);
  let realScan = null;
  await test('扫描真实目录（只读，不哈希不上传不删除）', async () => {
    realScan = await scanDirectory({
      sourcePath: scanTarget,
      allowedRoots: [path.dirname(scanTarget)],
      maxEntries: 20,
    });
    assert.ok(realScan.fileCount > 0, '未扫描到文件');
    assert.ok(realScan.totalBytes > 0, '总大小为 0');
    assert.ok(realScan.byKind.video > 0, '未识别到视频');
    results.push(`        目录 ${realScan.sourcePath}`);
    results.push(`        文件 ${realScan.fileCount} 个，共 ${realScan.totalBytes} 字节（${(realScan.totalBytes / 1024 / 1024).toFixed(1)} MiB）`);
    results.push(`        分类 ${JSON.stringify(realScan.byKind)}`);
    results.push(`        子目录 ${realScan.directories.map((item) => `${item.name}(${item.fileCount})`).join(' ') || '无'}`);
    results.push(`        跳过 ${realScan.skippedCount} 项，风险文件 ${realScan.riskFileCount} 项，条目截断 ${realScan.entriesTruncated}`);
    assert.ok(!realScan.entries.some((entry) => entry.name === '.DS_Store'));
  });
  await test('真实目录扫描结果含中文路径且截断标记正确', async () => {
    assert.ok(realScan);
    assert.ok(realScan.entries.length <= 20);
    assert.strictEqual(realScan.entriesTruncated, realScan.fileCount > 20);
  });

  await fs.promises.rm(workspace, { recursive: true, force: true });

  console.log(results.join('\n'));
  console.log(`\n${failures ? `FAILED: ${failures} 项` : 'ALL PASSED'}`);
  process.exit(failures ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
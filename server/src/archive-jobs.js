// 归档批次后台任务：流式 SHA-256 → 对象去重 → 上传 → 目标端校验 → 可选写入素材记录。
// 状态全部持久化在数据库里，进程重启后可继续；单个文件失败不影响其它文件。
const fs = require('fs');
const path = require('path');

const fsp = fs.promises;
const { hashStream, isSameDevice } = require('./archive-storage');
const { resolveSourcePath } = require('./archive-paths');

const RETRYABLE_BACKOFF_MS = [2000, 8000, 30000];
const MEDIA_KINDS = new Set(['image', 'video']);

function classifyFileError(error) {
  const code = String(error?.code || '');
  if (code === 'ENOENT') return { code: 'source_missing', message: '源文件已不存在' };
  if (code === 'EACCES' || code === 'EPERM') return { code: 'permission_denied', message: '没有权限读取源文件' };
  if (code === 'EISDIR') return { code: 'not_a_file', message: '源路径已变成目录' };
  if (error?.archiveCode === 'source_changed') return { code: 'source_changed', message: error.message };
  if (error?.archiveCode === 'verification_failed') return { code: 'verification_failed', message: error.message };
  if (error?.archiveCode === 'object_missing') return { code: 'object_missing', message: '目标对象不存在，需要重新上传' };
  if (code === 'ENOSPC') return { code: 'disk_full', message: '本地磁盘空间不足' };
  if (code === 'ENOTDIR') return { code: 'not_a_file', message: '源路径已不是文件' };
  if (typeof code === 'string' && /^5/.test(code)) return { code: 'storage_unavailable', message: '对象存储暂时不可用' };
  return { code: error?.archiveCode || 'archive_failed', message: String(error?.message || '归档失败').slice(0, 500) };
}

function archiveError(code, message) {
  const statusCode = code === 'not_found' ? 404 : code === 'storage_backend_unavailable' ? 409 : 500;
  return Object.assign(new Error(message), { archiveCode: code, statusCode });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sameStat(recorded, actual) {
  if (!recorded || !actual) return false;
  return Number(recorded) === Number(actual);
}

function createArchiveQueue({ pool, config, getStorage, createMediaAsset, log = console }) {
  const running = new Map();
  // 同一对象键的并发上传只发生一次：后到的任务先看目标端是否已经写好，避免重复传输同样的内容。
  const uploads = new Map();

  async function withUploadLock(key, task) {
    if (uploads.has(key)) {
      await uploads.get(key).catch(() => {});
      return false;
    }
    const promise = task();
    uploads.set(key, promise);
    try {
      await promise;
      return true;
    } finally {
      uploads.delete(key);
    }
  }

  function storageFor(backend) {
    const storage = getStorage(backend);
    if (!storage) throw archiveError('storage_backend_unavailable', `归档后端 ${backend} 不可用`);
    return storage;
  }

  async function loadBatch(batchId, userId, client = pool) {
    const { rows } = await client.query('SELECT * FROM archive_batches WHERE id=$1 AND user_id=$2', [batchId, userId]);
    return rows[0];
  }

  async function refreshBatchStats(batchId, client = pool) {
    const { rows } = await client.query(
      `SELECT
          COUNT(*)::int AS total_files,
          COALESCE(SUM(size_bytes), 0)::bigint AS total_bytes,
          COUNT(*) FILTER (WHERE status IN ('available','source_deleted'))::int AS success_files,
          COUNT(*) FILTER (WHERE status = 'failed')::int AS failed_files,
          COUNT(*) FILTER (WHERE status = 'skipped')::int AS skipped_files,
          COUNT(*) FILTER (WHERE status NOT IN ('queued','discovered','hashing','uploading','verifying','skipped'))::int AS processed_files,
          COALESCE(SUM(size_bytes) FILTER (WHERE status IN ('available','source_deleted','failed')), 0)::bigint AS processed_bytes
        FROM archive_files WHERE batch_id=$1`,
      [batchId],
    );
    const stats = rows[0];
    await client.query(
      `UPDATE archive_batches
          SET total_files=$2, total_bytes=$3, success_files=$4, failed_files=$5, skipped_files=$6,
              processed_files=$7, processed_bytes=$8, updated_at=now()
        WHERE id=$1`,
      [batchId, stats.total_files, stats.total_bytes, stats.success_files, stats.failed_files, stats.skipped_files, stats.processed_files, stats.processed_bytes],
    );
    return stats;
  }

  async function setCurrentFile(batchId, relativePath) {
    await pool.query('UPDATE archive_batches SET current_relative_path=$2, updated_at=now() WHERE id=$1', [batchId, String(relativePath || '').slice(0, 600)]);
  }

  async function claimNextFile(batchId) {
    const { rows } = await pool.query(
      `UPDATE archive_files f
          SET status='hashing', retry_count=f.retry_count, updated_at=now()
        WHERE f.id = (
          SELECT id FROM archive_files
           WHERE batch_id=$1 AND status='queued'
           ORDER BY size_bytes ASC, id ASC
           FOR UPDATE SKIP LOCKED
           LIMIT 1
        )
        RETURNING f.*`,
      [batchId],
    );
    return rows[0];
  }

  async function findReusableObject(userId, backend, sha256, sizeBytes) {
    const { rows } = await pool.query(
      `SELECT * FROM archive_objects
        WHERE user_id=$1 AND storage_backend=$2 AND sha256=$3 AND size_bytes=$4
        LIMIT 1`,
      [userId, backend, sha256, sizeBytes],
    );
    return rows[0];
  }

  async function ensureObject({ userId, backend, sha256, sizeBytes, contentType, key }) {
    const { rows } = await pool.query(
      `INSERT INTO archive_objects(user_id, storage_backend, object_key, sha256, size_bytes, content_type, status)
       VALUES($1,$2,$3,$4,$5,$6,'pending')
       ON CONFLICT(user_id, sha256, size_bytes, storage_backend)
       DO UPDATE SET content_type=EXCLUDED.content_type
       RETURNING *`,
      [userId, backend, key, sha256, sizeBytes, contentType],
    );
    return rows[0];
  }

  async function markObject(objectId, fields) {
    const columns = {
      status: 'status',
      lastError: 'last_error',
      verifiedAt: 'verified_at',
      metadata: 'metadata',
      objectKey: 'object_key',
      contentType: 'content_type',
    };
    const keys = Object.keys(fields).filter((key) => columns[key]);
    if (!keys.length) return;
    const values = keys.map((key) => fields[key]);
    await pool.query(
      `UPDATE archive_objects SET ${keys.map((key, index) => `${columns[key]}=$${index + 1}`).join(', ')} WHERE id=$${keys.length + 1}`,
      [...values, objectId],
    );
  }

  async function linkObject(objectId) {
    await pool.query('UPDATE archive_objects SET reference_count=reference_count+1 WHERE id=$1', [objectId]);
  }

  // 单文件处理：每一步失败都写回明确状态，不抛给上层循环。
  async function processFile({ batch, file }) {
    const userId = batch.user_id;
    const backend = batch.storage_backend;
    const storage = storageFor(backend);
    await setCurrentFile(batch.id, file.relative_path);

    const { realPath: sourceRoot } = await resolveSourcePath(batch.source_root, config.allowedRoots);
    const sourcePath = path.join(sourceRoot, file.relative_path);
    if (!sourcePath.startsWith(`${sourceRoot}${path.sep}`)) throw archiveError('source_changed', '源路径越界');

    const stats = await fsp.lstat(sourcePath).catch((error) => {
      throw error.code === 'ENOENT' ? archiveError('source_missing', '源文件已不存在') : error;
    });
    if (!stats.isFile()) throw archiveError('source_changed', '源路径已不是普通文件');
    if (!sameStat(file.size_bytes, stats.size)) throw archiveError('source_changed', `源文件大小已变化（清单 ${file.size_bytes} 字节，当前 ${stats.size} 字节）`);
    if (file.source_mtime && Math.abs(new Date(file.source_mtime).getTime() - stats.mtime.getTime()) > 1000) {
      throw archiveError('source_changed', '源文件修改时间已变化');
    }

    const sha256 = await hashStream(sourcePath);
    const existing = await findReusableObject(userId, backend, sha256, stats.size);
    let object = existing;
    let reused = Boolean(existing && existing.status === 'available');
    if (object) await markObject(object.id, { status: 'uploading', lastError: '' });
    else object = await ensureObject({ userId, backend, sha256, sizeBytes: stats.size, contentType: file.mime_type, key: storage.buildKey({ userId, sha256, sizeBytes: stats.size }) });

    if (!reused) {
      try {
        await withUploadLock(object.object_key, async () => {
          const existing = await storage.headObject(object.object_key).catch(() => null);
          if (existing && Number(existing.sizeBytes) === Number(stats.size) && (!existing.sha256Metadata || existing.sha256Metadata === sha256)) return;
          await storage.putObject({
            key: object.object_key,
            filePath: sourcePath,
            contentType: file.mime_type,
            sha256,
            sizeBytes: stats.size,
            multipartThresholdBytes: config.multipartThresholdBytes,
          });
        });
      } catch (error) {
        await markObject(object.id, { status: 'failed', lastError: String(error.message || '').slice(0, 500) });
        throw error;
      }
    }

    await pool.query(`UPDATE archive_files SET status='verifying', sha256=$2, updated_at=now() WHERE id=$1`, [file.id, sha256]);

    let head = null;
    try {
      head = await storage.headObject(object.object_key);
    } catch (_) {
      throw archiveError('object_missing', '目标对象校验失败：对象不存在');
    }
    if (head.sizeBytes && Number(head.sizeBytes) !== Number(stats.size)) {
      await markObject(object.id, { status: 'failed', lastError: `目标大小 ${head.sizeBytes} 与源文件 ${stats.size} 不一致` });
      throw archiveError('verification_failed', `目标对象大小不一致（${head.sizeBytes} ≠ ${stats.size}）`);
    }
    if (head.sha256Metadata && head.sha256Metadata !== sha256) {
      await markObject(object.id, { status: 'failed', lastError: '目标 SHA-256 元数据与源文件不一致' });
      throw archiveError('verification_failed', '目标对象 SHA-256 元数据不一致');
    }
    await markObject(object.id, {
      status: 'available',
      verifiedAt: new Date(),
      lastError: '',
      metadata: { verifiedSha256: sha256, verifiedSizeBytes: Number(stats.size), contentType: file.mime_type, reused },
    });
    if (!reused) await linkObject(object.id);

    let assetId = file.asset_id;
    if (batch.create_media_assets && MEDIA_KINDS.has(file.kind) && !assetId && createMediaAsset) {
      try {
        assetId = await createMediaAsset({ userId, file, objectKey: object.object_key, sha256, mimeType: file.mime_type, sizeBytes: stats.size, storage, folder: batch.asset_folder });
      } catch (error) {
        // 素材记录失败不影响原文件归档：文件仍为可用，只是没有进入素材库。
        log.warn(`archive media asset skipped for ${file.relative_path}: ${error.message}`);
      }
    }

    await pool.query(
      `UPDATE archive_files
          SET status='available', object_id=$2, asset_id=$3, sha256=$4, size_bytes=$5, error_code='', error_message='', updated_at=now()
        WHERE id=$1`,
      [file.id, object.id, assetId || null, sha256, stats.size],
    );
    return { reused, objectId: object.id };
  }

  async function runFileWithRetry(batch, file) {
    const maxAttempts = Math.max(1, config.maxRetries + 1);
    let attempt = 0;
    for (;;) {
      try {
        const result = await processFile({ batch, file });
        await pool.query('UPDATE archive_files SET retry_count=$2, updated_at=now() WHERE id=$1', [file.id, attempt]);
        return result;
      } catch (error) {
        const classified = classifyFileError(error);
        attempt += 1;
        const retryable = !['source_missing', 'source_changed', 'not_a_file'].includes(classified.code) && attempt < maxAttempts;
        await pool.query(
          `UPDATE archive_files
              SET status=$2, retry_count=$3, error_code=$4, error_message=$5, updated_at=now()
            WHERE id=$1`,
          [file.id, retryable ? 'queued' : 'failed', attempt, classified.code, classified.message],
        );
        await refreshBatchStats(batch.id);
        if (!retryable) {
          log.warn(`archive file failed ${batch.id}/${file.relative_path}: ${classified.code} ${classified.message}`);
          return null;
        }
        await sleep(RETRYABLE_BACKOFF_MS[Math.min(attempt - 1, RETRYABLE_BACKOFF_MS.length - 1)]);
        // 重试前重新读一遍清单，避免用过期的大小/时间判断源文件是否变化
        const { rows } = await pool.query('SELECT * FROM archive_files WHERE id=$1', [file.id]);
        if (!rows[0] || rows[0].status !== 'queued') return null;
        Object.assign(file, rows[0]);
      }
    }
  }

  async function finalizeBatch(batchId) {
    const stats = await refreshBatchStats(batchId);
    const { rows } = await pool.query(
      `SELECT COUNT(*)::int AS pending FROM archive_files WHERE batch_id=$1 AND status IN ('queued','discovered','hashing','uploading','verifying')`,
      [batchId],
    );
    const { rows: batchRows } = await pool.query('SELECT status, source_cleanup_status FROM archive_batches WHERE id=$1', [batchId]);
    const currentStatus = batchRows[0]?.status;
    if (currentStatus === 'paused' || currentStatus === 'cancelled') return;
    const nextStatus = stats.pending
      ? 'running'
      : stats.failed_files
        ? 'completed_with_errors'
        : 'completed';
    await pool.query(
      `UPDATE archive_batches
          SET status=$2, completed_at=CASE WHEN $2 IN ('completed','completed_with_errors') THEN now() ELSE completed_at END,
              current_relative_path='', updated_at=now()
        WHERE id=$1`,
      [batchId, nextStatus],
    );
  }

  async function runBatch(batchId, userId) {
    const batch = await loadBatch(batchId, userId);
    if (!batch) return;
    await pool.query(
      `UPDATE archive_batches SET status='running', started_at=COALESCE(started_at, now()), completed_at=NULL, last_error='', updated_at=now()
        WHERE id=$1 AND status IN ('queued','paused','cancelled','running')`,
      [batchId],
    );

    const workerCount = Math.max(1, Number(config.maxConcurrency) || 1);
    const worker = async () => {
      for (;;) {
        const { rows } = await pool.query('SELECT status FROM archive_batches WHERE id=$1', [batchId]);
        const status = rows[0]?.status;
        if (status !== 'running') return;
        const file = await claimNextFile(batchId);
        if (!file) return;
        try {
          await runFileWithRetry(batch, file);
        } catch (error) {
          // 兜底：任何未预期的异常都要落成明确的失败状态，否则文件会卡在 hashing 导致批次永远不结束
          await pool.query(
            `UPDATE archive_files SET status='failed', error_code='archive_failed', error_message=$2, updated_at=now() WHERE id=$1`,
            [file.id, String(error?.message || '归档失败').slice(0, 500)],
          ).catch(() => {});
        }
        await refreshBatchStats(batchId);
      }
    };

    await Promise.all(Array.from({ length: workerCount }, () => worker().catch((error) => log.error(`archive worker error ${batchId}:`, error.message))));
    await finalizeBatch(batchId);
  }

  function launch(batchId, userId) {
    if (running.has(batchId)) return running.get(batchId);
    const task = runBatch(batchId, userId)
      .catch((error) => {
        log.error(`archive batch ${batchId} failed:`, error.message);
        return pool.query(
          `UPDATE archive_batches SET status='failed', last_error=$2, completed_at=now(), updated_at=now() WHERE id=$1`,
          [batchId, String(error.message || '').slice(0, 500)],
        ).catch(() => {});
      })
      .finally(() => running.delete(batchId));
    running.set(batchId, task);
    return task;
  }

  async function startBatch(batchId, userId) {
    const batch = await loadBatch(batchId, userId);
    if (!batch) throw archiveError('not_found', '归档批次不存在');
    if (batch.status === 'running') return batch;
    await pool.query(`UPDATE archive_batches SET status='queued', last_error='', updated_at=now() WHERE id=$1`, [batchId]);
    launch(batchId, userId);
    return loadBatch(batchId, userId);
  }

  async function pauseBatch(batchId, userId) {
    const batch = await loadBatch(batchId, userId);
    if (!batch) throw archiveError('not_found', '归档批次不存在');
    if (batch.status === 'running') {
      await pool.query(`UPDATE archive_batches SET status='paused', updated_at=now() WHERE id=$1`, [batchId]);
      // 已派发的文件跑完当前这一个就不再领取新文件
      await pool.query(`UPDATE archive_files SET status='queued' WHERE batch_id=$1 AND status IN ('hashing','uploading','verifying')`, [batchId]);
    }
    return loadBatch(batchId, userId);
  }

  async function cancelBatch(batchId, userId) {
    const batch = await loadBatch(batchId, userId);
    if (!batch) throw archiveError('not_found', '归档批次不存在');
    if (batch.status === 'cancelled') return batch;
    await pool.query(`UPDATE archive_batches SET status='cancelled', updated_at=now() WHERE id=$1`, [batchId]);
    await pool.query(`UPDATE archive_files SET status='queued' WHERE batch_id=$1 AND status IN ('hashing','uploading','verifying')`, [batchId]);
    await refreshBatchStats(batchId);
    return loadBatch(batchId, userId);
  }

  async function resumeBatch(batchId, userId) {
    return startBatch(batchId, userId);
  }

  async function retryFailedFiles(batchId, userId) {
    const batch = await loadBatch(batchId, userId);
    if (!batch) throw archiveError('not_found', '归档批次不存在');
    const { rowCount } = await pool.query(
      `UPDATE archive_files SET status='queued', retry_count=0, error_code='', error_message='', updated_at=now()
        WHERE batch_id=$1 AND status='failed'`,
      [batchId],
    );
    await refreshBatchStats(batchId);
    if (rowCount) await startBatch(batchId, userId);
    return { requeued: rowCount, batch: await loadBatch(batchId, userId) };
  }

  async function queueAllFiles(batchId) {
    await pool.query(
      `UPDATE archive_files SET status='queued', error_code='', error_message='', updated_at=now()
        WHERE batch_id=$1 AND status IN ('discovered','failed','skipped')`,
      [batchId],
    );
  }

  // 重新校验：HEAD 比对大小和 SHA-256 元数据；任一不一致都会阻止后续清理源文件。
  async function verifyBatch(batchId, userId) {
    const batch = await loadBatch(batchId, userId);
    if (!batch) throw archiveError('not_found', '归档批次不存在');
    const storage = storageFor(batch.storage_backend);
    const { rows } = await pool.query(
      `SELECT f.*, o.object_key, o.status AS object_status, o.verified_at, o.sha256 AS object_sha256
         FROM archive_files f LEFT JOIN archive_objects o ON o.id=f.object_id
        WHERE f.batch_id=$1 AND f.status IN ('available','source_deleted')`,
      [batchId],
    );
    const results = { total: rows.length, ok: 0, mismatched: 0, missing: 0 };
    for (const file of rows) {
      try {
        const head = await storage.headObject(file.object_key);
        const sizeOk = !head.sizeBytes || Number(head.sizeBytes) === Number(file.size_bytes);
        const shaOk = !head.sha256Metadata || head.sha256Metadata === file.sha256;
        if (sizeOk && shaOk) {
          results.ok += 1;
          await markObject(file.object_id, { status: 'available', verifiedAt: new Date(), lastError: '' });
          await pool.query(`UPDATE archive_files SET error_code='', error_message='', updated_at=now() WHERE id=$1`, [file.id]);
        } else {
          results.mismatched += 1;
          await markObject(file.object_id, { status: 'failed', lastError: '重新校验发现目标对象与清单不一致' });
          await pool.query(`UPDATE archive_files SET error_code='verification_failed', error_message='重新校验发现目标对象与清单不一致', updated_at=now() WHERE id=$1`, [file.id]);
        }
      } catch (_) {
        results.missing += 1;
        await markObject(file.object_id, { status: 'failed', lastError: '目标对象不存在' });
        await pool.query(`UPDATE archive_files SET error_code='object_missing', error_message='目标对象不存在', updated_at=now() WHERE id=$1`, [file.id]);
      }
    }
    await refreshBatchStats(batchId);
    return { ...results, verifiedAt: new Date().toISOString() };
  }

  async function cleanupReadiness(batch) {
    const blockers = [];
    if (!['completed', 'completed_with_errors', 'cancelled'].includes(batch.status)) blockers.push('批次尚未完成');
    if (batch.storage_backend === 'local' && config.localRoot && await isSameDevice(config.localRoot, batch.source_root)) {
      blockers.push('local 后端与源目录在同一块磁盘，清理不会释放空间');
    }
    const { rows } = await pool.query(
      `SELECT COUNT(*)::int AS total,
              COUNT(*) FILTER (WHERE f.object_id IS NULL)::int AS no_object,
              COUNT(*) FILTER (WHERE f.sha256 IS NULL OR f.sha256 = '')::int AS no_hash,
              COUNT(*) FILTER (WHERE o.status IS DISTINCT FROM 'available' OR o.verified_at IS NULL)::int AS unverified,
              COUNT(*) FILTER (WHERE f.error_code <> '')::int AS errored,
              COUNT(*) FILTER (WHERE f.source_deleted_at IS NOT NULL)::int AS already_deleted
         FROM archive_files f LEFT JOIN archive_objects o ON o.id=f.object_id
        WHERE f.batch_id=$1 AND f.status='available'`,
      [batch.id],
    );
    const stats = rows[0];
    if (stats.total === 0) blockers.push('没有可清理的成功归档文件');
    if (stats.no_object) blockers.push(`${stats.no_object} 个文件没有目标对象`);
    if (stats.no_hash) blockers.push(`${stats.no_hash} 个文件缺少 SHA-256`);
    if (stats.unverified) blockers.push(`${stats.unverified} 个文件未通过目标端校验`);
    if (stats.errored) blockers.push(`${stats.errored} 个文件存在校验或处理错误`);
    return { blockers, stats, ready: blockers.length === 0 };
  }

  // 清理源文件是独立的显式操作：需要批次名确认、只删成功项、源文件变化一律不删。
  async function cleanupSource({ batchId, userId, confirmText, onlySuccessful = true, removeEmptyDirectories = false, allowSameDisk = false }) {
    const batch = await loadBatch(batchId, userId);
    if (!batch) throw archiveError('not_found', '归档批次不存在');
    if (!confirmText || String(confirmText).trim() !== batch.name) {
      throw Object.assign(new Error('确认文本与批次名称不一致，已拒绝清理本机源文件'), { statusCode: 400, archiveCode: 'confirmation_mismatch' });
    }
    if (onlySuccessful && batch.status !== 'completed' && batch.status !== 'completed_with_errors') {
      throw Object.assign(new Error('批次尚未完成，不能清理源文件'), { statusCode: 409, archiveCode: 'batch_not_completed' });
    }
    const readiness = await cleanupReadiness(batch);
    if (!readiness.ready && !(allowSameDisk && readiness.blockers.length === 1 && readiness.blockers[0].includes('同一块磁盘'))) {
      throw Object.assign(new Error(`不满足清理条件：${readiness.blockers.join('；')}`), { statusCode: 409, archiveCode: 'cleanup_blocked' });
    }
    const sameDisk = batch.storage_backend === 'local' && config.localRoot && await isSameDevice(config.localRoot, batch.source_root);

    await pool.query(
      `UPDATE archive_batches SET source_cleanup_status='requested', source_cleanup_requested_at=now(), updated_at=now() WHERE id=$1`,
      [batchId],
    );

    const { realPath: sourceRoot } = await resolveSourcePath(batch.source_root, config.allowedRoots);
    const { rows: candidates } = await pool.query(
      `SELECT f.id, f.relative_path, f.size_bytes, f.source_mtime, f.source_deleted_at
         FROM archive_files f JOIN archive_objects o ON o.id=f.object_id
        WHERE f.batch_id=$1 AND f.status='available' AND f.source_deleted_at IS NULL
          AND f.error_code='' AND f.sha256 IS NOT NULL
          AND o.status='available' AND o.verified_at IS NOT NULL`,
      [batchId],
    );

    const summary = { candidates: candidates.length, deleted: 0, changed: 0, failed: 0, removedDirectories: 0, errors: [] };
    const touchedDirectories = new Set();
    for (const candidate of candidates) {
      const sourcePath = path.join(sourceRoot, candidate.relative_path);
      try {
        const stats = await fsp.lstat(sourcePath).catch((error) => {
          if (error.code === 'ENOENT') return null;
          throw error;
        });
        if (!stats || !stats.isFile()) {
          summary.changed += 1;
          await pool.query(`UPDATE archive_files SET error_code='source_missing', error_message='清理时源文件已不存在', updated_at=now() WHERE id=$1`, [candidate.id]);
          continue;
        }
        if (Number(stats.size) !== Number(candidate.size_bytes) || (candidate.source_mtime && Math.abs(new Date(candidate.source_mtime).getTime() - stats.mtime.getTime()) > 1000)) {
          summary.changed += 1;
          await pool.query(`UPDATE archive_files SET error_code='source_changed', error_message='源文件在导入后发生变化，已保留', updated_at=now() WHERE id=$1`, [candidate.id]);
          continue;
        }
        await fsp.unlink(sourcePath);
        summary.deleted += 1;
        touchedDirectories.add(path.dirname(sourcePath));
        await pool.query(
          `UPDATE archive_files SET status='source_deleted', source_deleted_at=now(), updated_at=now() WHERE id=$1`,
          [candidate.id],
        );
      } catch (error) {
        summary.failed += 1;
        summary.errors.push({ relativePath: candidate.relative_path, message: error.message });
        await pool.query(`UPDATE archive_files SET error_code='cleanup_failed', error_message=$2, updated_at=now() WHERE id=$1`, [candidate.id, String(error.message).slice(0, 500)]);
      }
    }

    if (removeEmptyDirectories) {
      const directories = [...touchedDirectories]
        .filter((directory) => directory !== sourceRoot && directory.startsWith(`${sourceRoot}${path.sep}`))
        .sort((a, b) => b.split(path.sep).length - a.split(path.sep).length);
      for (const directory of directories) {
        try {
          const remaining = await fsp.readdir(directory);
          if (!remaining.length) {
            await fsp.rmdir(directory);
            summary.removedDirectories += 1;
          }
        } catch (error) {
          if (error.code !== 'ENOENT') log.warn(`archive empty directory cleanup skipped: ${error.message}`);
        }
      }
    }

    const status = summary.failed ? 'partial' : 'completed';
    await pool.query(
      `UPDATE archive_batches SET source_cleanup_status=$2, source_cleanup_completed_at=now(), updated_at=now() WHERE id=$1`,
      [batchId, status],
    );
    await refreshBatchStats(batchId);
    return { ...summary, status, sameDevice: Boolean(sameDisk) };
  }

  async function manifest(batchId, userId) {
    const { rows } = await pool.query(
      `SELECT f.batch_id, b.name AS batch_name, f.relative_path, f.size_bytes, f.sha256,
              f.kind, f.status, f.source_deleted_at, o.object_key, o.status AS object_status, o.verified_at
         FROM archive_files f
         JOIN archive_batches b ON b.id=f.batch_id
         LEFT JOIN archive_objects o ON o.id=f.object_id
        WHERE f.batch_id=$1 AND f.user_id=$2
        ORDER BY f.relative_path`,
      [batchId, userId],
    );
    return { batchId, files: rows };
  }

  // 进程重启恢复：未完成的活动状态回到可重试队列，批次不误报为完成。
  async function recoverInterruptedBatches() {
    const { rows } = await pool.query(`SELECT id, name FROM archive_batches WHERE status='running'`);
    if (rows.length) {
      await pool.query(
        `UPDATE archive_files SET status='queued', updated_at=now()
          WHERE status IN ('hashing','uploading','verifying') AND batch_id IN (SELECT id FROM archive_batches WHERE status='running')`,
      );
      await pool.query(
        `UPDATE archive_batches SET status='paused', last_error='服务重启，任务已暂停，点击继续即可恢复', updated_at=now() WHERE status='running'`,
      );
      log.warn(`archive recovery: ${rows.length} 个未完成批次已转为暂停`);
    }
    await pool.query(
      `UPDATE archive_objects SET status='pending', last_error='服务重启后需要重新确认上传' WHERE status IN ('uploading','verifying')`,
    );
    return rows.length;
  }

  return {
    cancelBatch,
    cleanupReadiness,
    cleanupSource,
    finalizeBatch,
    launch,
    manifest,
    pauseBatch,
    queueAllFiles,
    recoverInterruptedBatches,
    refreshBatchStats,
    resumeBatch,
    retryFailedFiles,
    startBatch,
    verifyBatch,
  };
}

module.exports = { classifyFileError, createArchiveQueue };
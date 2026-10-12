import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import NetdiskBrowser from "./netdisk-browser.jsx";
import {
  Archive,
  ChevronDown,
  CircleCheck,
  Cloud,
  Download,
  ExternalLink,
  FileText,
  FolderOpen,
  Info,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";

const API_BASE = "/api";

const ARCHIVE_KIND_LABELS = {
  image: "图片",
  video: "视频",
  audio: "音频",
  document: "文档",
  archive: "压缩包",
  other: "其他",
};

const BATCH_STATUS_LABELS = {
  scanned: "已扫描",
  queued: "排队中",
  running: "归档中",
  paused: "已暂停",
  completed: "已完成",
  completed_with_errors: "部分失败",
  cancelled: "已取消",
  failed: "批次失败",
};

const FILE_STATUS_LABELS = {
  discovered: "待处理",
  hashing: "计算哈希",
  queued: "排队中",
  uploading: "上传中",
  verifying: "校验中",
  available: "已归档",
  skipped: "已跳过",
  failed: "失败",
  source_deleted: "源文件已清理",
};

const SORT_OPTIONS = [
  { value: "created_desc", label: "导入时间（新→旧）" },
  { value: "created_asc", label: "导入时间（旧→新）" },
  { value: "size_desc", label: "大小（大→小）" },
  { value: "size_asc", label: "大小（小→大）" },
  { value: "mtime_desc", label: "修改时间（新→旧）" },
  { value: "name_asc", label: "文件名" },
];

async function archiveFetch(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error || "请求失败，请稍后重试");
  return payload;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function shortSha(value) {
  const text = String(value || "");
  return text ? `${text.slice(0, 12)}…` : "—";
}

function percent(done, total) {
  if (!total) return 0;
  return Math.min(100, Math.round((Number(done) / Number(total)) * 100));
}

// 扫描导入：只读扫描服务端白名单目录，确认后再创建异步批次。
function ArchiveScanPanel({ options, onBatchCreated, defaultPath }) {
  const [sourcePath, setSourcePath] = useState(defaultPath || "");
  const [scan, setScan] = useState(null);
  const [selectedDirs, setSelectedDirs] = useState([]);
  const [storageBackend, setStorageBackend] = useState(options?.storageBackendDefault || "cos");
  const [createMediaAssets, setCreateMediaAssets] = useState(false);
  const [batchName, setBatchName] = useState("");
  const [scanning, setScanning] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const runScan = async () => {
    setScanning(true);
    setError("");
    setScan(null);
    try {
      const payload = await archiveFetch("/archive/scan", { method: "POST", body: JSON.stringify({ sourcePath }) });
      setScan(payload.scan);
      setSelectedDirs(payload.scan.directories.map((item) => item.name));
      setBatchName(payload.scan.displayName);
    } catch (scanError) {
      setError(scanError.message);
    } finally {
      setScanning(false);
    }
  };

  const createBatch = async () => {
    setCreating(true);
    setError("");
    try {
      const payload = await archiveFetch("/archive/batches", {
        method: "POST",
        body: JSON.stringify({
          sourcePath,
          name: batchName || scan?.displayName || "",
          storageBackend,
          createMediaAssets,
          includeRelativePaths: selectedDirs.length ? selectedDirs : ["."],
        }),
      });
      setScan(null);
      setSourcePath("");
      setBatchName("");
      onBatchCreated(payload.batch);
    } catch (createError) {
      setError(createError.message);
    } finally {
      setCreating(false);
    }
  };

  const selectedStats = useMemo(() => {
    if (!scan) return null;
    const picked = scan.directories.filter((item) => selectedDirs.includes(item.name));
    return {
      fileCount: picked.reduce((sum, item) => sum + item.fileCount, 0),
      totalBytes: picked.reduce((sum, item) => sum + item.totalBytes, 0),
    };
  }, [scan, selectedDirs]);

  const backendAvailable = (id) => options?.backends?.find((item) => item.id === id)?.available;
  const sameDevice = storageBackend === "local" && options?.sameDeviceAsSource;

  return (
    <div className="archive-scan">
      <div className="archive-scan-row">
        <input
          className="archive-path-input"
          value={sourcePath}
          placeholder="/Users/…/AIGC/1007视频"
          onChange={(event) => setSourcePath(event.target.value)}
        />
        <button className="primary-button" onClick={runScan} disabled={scanning || !sourcePath.trim()}>
          <Search size={16} />
          {scanning ? "扫描中…" : "扫描目录"}
        </button>
      </div>
      <p className="archive-hint">
        只允许扫描服务端白名单目录（{options?.allowedRoots?.join("、") || "未配置"}）；扫描只读取文件信息，不会上传，也不会删除本地文件。
      </p>
      {error && <div className="inline-error">{error}</div>}
      {scan && (
        <div className="archive-scan-result">
          <div className="archive-scan-summary">
            <div><span>目录</span><strong>{scan.displayName}</strong></div>
            <div><span>文件数</span><strong>{scan.fileCount}</strong></div>
            <div><span>总大小</span><strong>{formatBytes(scan.totalBytes)}</strong></div>
            <div><span>跳过</span><strong>{scan.skippedCount}</strong></div>
            <div><span>风险文件</span><strong>{scan.riskFileCount}</strong></div>
          </div>
          <div className="archive-kind-row">
            {Object.entries(scan.byKind).map(([kind, count]) => (
              <span key={kind} className="archive-kind-chip">{ARCHIVE_KIND_LABELS[kind] || kind} {count}</span>
            ))}
          </div>
          {scan.riskFileCount > 0 && (
            <p className="archive-warning">
              <Info size={14} />已跳过 {scan.riskFileCount} 个疑似敏感文件（.env、密钥或文件名含 secret/token/credential），默认不会上传。
            </p>
          )}
          {scan.skippedCount > 0 && (
            <details className="archive-skipped">
              <summary>查看被跳过的条目（{scan.skippedCount}）</summary>
              <ul>
                {scan.skipped.map((item) => (
                  <li key={`${item.path}-${item.reason}`}>
                    <code>{item.path}</code> — {item.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <div className="archive-dir-picker">
            <p>导入范围</p>
            <div className="archive-dir-list">
              {scan.directories.map((item) => (
                <label key={item.name} className="archive-dir-item">
                  <input
                    type="checkbox"
                    checked={selectedDirs.includes(item.name)}
                    onChange={(event) =>
                      setSelectedDirs((current) =>
                        event.target.checked ? [...current, item.name] : current.filter((name) => name !== item.name),
                      )
                    }
                  />
                  <span>{item.name === "." ? "（根目录文件）" : item.name}</span>
                  <em>{item.fileCount} 个 · {formatBytes(item.totalBytes)}</em>
                </label>
              ))}
            </div>
          </div>
          <div className="archive-form-row">
            <label className="archive-field">
              <span>批次名称</span>
              <input value={batchName} onChange={(event) => setBatchName(event.target.value)} placeholder="1007视频" />
            </label>
            <label className="archive-field">
              <span>目标后端</span>
              <select value={storageBackend} onChange={(event) => setStorageBackend(event.target.value)}>
                {(options?.backends || []).map((item) => (
                  <option key={item.id} value={item.id} disabled={!item.available}>
                    {item.label}{item.available ? "" : "（不可用）"}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="archive-checkbox">
            <input type="checkbox" checked={createMediaAssets} onChange={(event) => setCreateMediaAssets(event.target.checked)} disabled={storageBackend !== "cos"} />
            <span>为图片和视频创建素材库记录（仅 COS 后端可用，可直接复用现有预览与下载）</span>
          </label>
          {sameDevice && (
            <p className="archive-warning"><Info size={14} />local 后端与源目录在同一块磁盘，复制后不会释放本地空间。</p>
          )}
          {storageBackend === "cos" && (
            <p className="archive-hint">COS 模式：完成校验后可单独确认清理本地源文件，真正释放磁盘空间。</p>
          )}
          <p className="archive-hint">首次导入不会删除本地文件。</p>
          <div className="archive-scan-actions">
            <span className="archive-selection">
              已选 {selectedStats ? selectedStats.fileCount : 0} 个文件 · {formatBytes(selectedStats?.totalBytes || 0)}
            </span>
            <button className="primary-button" onClick={createBatch} disabled={creating || !selectedStats?.fileCount}>
              <Plus size={16} />
              {creating ? "创建中…" : "创建归档批次"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ArchiveBatchCard({ batch, active, onSelect, onAction, busy }) {
  const progress = percent(batch.processedBytes, batch.totalBytes);
  return (
    <article className={`archive-batch-card ${active ? "active" : ""}`} onClick={() => onSelect(batch.id)}>
      <div className="archive-batch-head">
        <h3>{batch.name}</h3>
        <span className={`archive-status archive-status-${batch.status}`}>{BATCH_STATUS_LABELS[batch.status] || batch.status}</span>
      </div>
      <p className="archive-batch-path" title={batch.sourceRoot}>{batch.sourceRoot}</p>
      <div className="archive-progress"><span style={{ width: `${progress}%` }} /></div>
      <div className="archive-batch-meta">
        <span>{batch.storageBackend === "cos" ? "腾讯云 COS" : "本地归档目录"}</span>
        <span>{formatBytes(batch.processedBytes)} / {formatBytes(batch.totalBytes)}</span>
      </div>
      <div className="archive-batch-counts">
        <span>成功 {batch.successFiles}</span>
        <span>失败 {batch.failedFiles}</span>
        <span>跳过 {batch.skippedFiles}</span>
        <span>{progress}%</span>
      </div>
      {batch.currentRelativePath && <p className="archive-batch-current">正在处理：{batch.currentRelativePath}</p>}
      <div className="archive-batch-actions" onClick={(event) => event.stopPropagation()}>
        {batch.status === "queued" || batch.status === "paused" || batch.status === "cancelled" ? (
          <button onClick={() => onAction(batch, batch.status === "paused" ? "resume" : "start")} disabled={busy}>
            <Play size={14} />{batch.status === "paused" ? "继续" : "开始"}
          </button>
        ) : null}
        {batch.status === "running" ? <button onClick={() => onAction(batch, "pause")} disabled={busy}><Pause size={14} />暂停</button> : null}
        {batch.status !== "running" && batch.status !== "completed" ? (
          <button onClick={() => onAction(batch, "cancel")} disabled={busy}><X size={14} />取消</button>
        ) : null}
        {batch.failedFiles > 0 ? <button onClick={() => onAction(batch, "retry-failed")} disabled={busy}><RefreshCw size={14} />重试失败</button> : null}
        <button onClick={() => onAction(batch, "verify")} disabled={busy}><CircleCheck size={14} />重新校验</button>
      </div>
    </article>
  );
}

function ArchiveBatchDetail({ batch, onRefresh }) {
  const [confirmText, setConfirmText] = useState("");
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setConfirmText("");
    setCleanupOpen(false);
    setMessage("");
    setError("");
  }, [batch?.id]);

  if (!batch) return null;

  const runCleanup = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const payload = await archiveFetch(`/archive/batches/${batch.id}/cleanup-source`, {
        method: "POST",
        body: JSON.stringify({
          batchId: batch.id,
          confirmText,
          removeEmptyDirectories: true,
          allowSameDisk: batch.storageBackend === "local",
        }),
      });
      setMessage(`已删除本地源文件 ${payload.cleanup.deleted} 个；源文件已变化保留 ${payload.cleanup.changed} 个；删除失败 ${payload.cleanup.failed} 个；清空空目录 ${payload.cleanup.removedDirectories} 个。`);
      setCleanupOpen(false);
      onRefresh();
    } catch (cleanupError) {
      setError(cleanupError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="archive-batch-detail">
      <div className="archive-detail-head">
        <div>
          <h3>{batch.name}</h3>
          <p>{batch.sourceRoot}</p>
        </div>
        <span className={`archive-status archive-status-${batch.status}`}>{BATCH_STATUS_LABELS[batch.status] || batch.status}</span>
      </div>
      <div className="archive-detail-grid">
        <div><span>目标后端</span><strong>{batch.storageBackend === "cos" ? "腾讯云 COS" : "本地归档目录"}</strong></div>
        <div><span>总文件</span><strong>{batch.totalFiles}</strong></div>
        <div><span>总大小</span><strong>{formatBytes(batch.totalBytes)}</strong></div>
        <div><span>已处理</span><strong>{batch.processedFiles}</strong></div>
        <div><span>成功</span><strong>{batch.successFiles}</strong></div>
        <div><span>失败</span><strong>{batch.failedFiles}</strong></div>
        <div><span>跳过</span><strong>{batch.skippedFiles}</strong></div>
        <div><span>源文件清理</span><strong>{batch.sourceCleanupStatus === "completed" ? "已完成" : batch.sourceCleanupStatus === "requested" ? "已请求" : "未执行"}</strong></div>
      </div>
      <p className="archive-hint">按字节进度 {percent(batch.processedBytes, batch.totalBytes)}%{batch.currentRelativePath ? ` · 正在处理 ${batch.currentRelativePath}` : ""}</p>
      {batch.lastError && <div className="inline-error">{batch.lastError}</div>}
      {batch.failures?.length > 0 && (
        <details className="archive-failures">
          <summary>失败文件（{batch.failedFiles}）</summary>
          <ul>
            {batch.failures.map((item) => (
              <li key={item.id}>
                <code>{item.relativePath}</code> — {item.errorCode} {item.errorMessage}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="archive-cleanup">
        {batch.cleanupReady ? (
          <p className="archive-hint">满足清理条件：有 {batch.cleanupCandidates} 个文件已完成目标端校验，可以选择删除本机源文件。</p>
        ) : (
          <p className="archive-hint">暂不可清理：{batch.cleanupBlockers?.join("；") || "批次未完成或校验未通过"}</p>
        )}
        {batch.cleanupReady && !cleanupOpen && (
          <button className="secondary-button" onClick={() => setCleanupOpen(true)} disabled={busy}>
            <Trash2 size={15} />清理已备份源文件
          </button>
        )}
        {cleanupOpen && (
          <div className="archive-cleanup-confirm">
            <p>
              这会删除本机 <strong>{batch.successFiles}</strong> 个源文件。归档对象保存在{batch.storageBackend === "cos" ? "腾讯云 COS" : "本地归档目录"}，
              失败的条目和已变化的源文件会被保留。
            </p>
            <label className="archive-field">
              <span>请输入批次名称「{batch.name}」以确认</span>
              <input value={confirmText} onChange={(event) => setConfirmText(event.target.value)} placeholder={batch.name} />
            </label>
            <div className="archive-cleanup-actions">
              <button className="secondary-button" onClick={() => setCleanupOpen(false)} disabled={busy}>取消</button>
              <button className="primary-button danger" onClick={runCleanup} disabled={busy || confirmText.trim() !== batch.name}>
                {busy ? "清理中…" : "确认删除本机源文件"}
              </button>
            </div>
          </div>
        )}
        {error && <div className="inline-error">{error}</div>}
        {message && <div className="inline-success">{message}</div>}
      </div>
    </div>
  );
}

function ArchiveFilePreview({ file, onClose, onOpenAsset }) {
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!file) return undefined;
    let active = true;
    setPreview(null);
    setError("");
    archiveFetch(`/archive/files/${file.id}/preview`)
      .then((payload) => active && setPreview(payload.preview))
      .catch((previewError) => active && setError(previewError.message));
    return () => {
      active = false;
    };
  }, [file?.id]);

  if (!file) return null;
  return (
    <div className="modal-layer archive-modal-layer" onClick={onClose}>
      <div className="archive-preview-modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div>
            <h3>{file.name}</h3>
            <p>{file.relativePath}</p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="关闭预览"><X size={18} /></button>
        </div>
        <div className="archive-preview-body">
          {error && <div className="inline-error">{error}</div>}
          {!error && !preview && <p className="archive-hint">正在读取预览…</p>}
          {preview?.type === "text" && <pre className="archive-preview-text">{preview.content}</pre>}
          {preview?.type === "media" && file.kind === "image" && <img className="archive-preview-image" src={preview.url} alt={file.name} />}
          {preview?.type === "media" && file.kind === "video" && <video className="archive-preview-video" src={preview.url} controls playsInline />}
          {preview?.type === "media" && file.kind === "audio" && <audio className="archive-preview-audio" src={preview.url} controls />}
          {["too_large", "unsupported", "unavailable"].includes(preview?.type) && <p className="archive-hint">{preview?.message}</p>}
        </div>
        <div className="archive-preview-meta">
          <div><span>类型</span><strong>{ARCHIVE_KIND_LABELS[file.kind] || file.kind}</strong></div>
          <div><span>大小</span><strong>{file.size}</strong></div>
          <div><span>修改时间</span><strong>{formatDate(file.sourceMtime)}</strong></div>
          <div><span>导入时间</span><strong>{formatDate(file.createdAt)}</strong></div>
          <div className="wide"><span>SHA-256</span><strong className="mono">{file.sha256 || "—"}</strong></div>
          <div className="wide"><span>原始路径</span><strong className="mono">{file.sourcePath}</strong></div>
          <div><span>目标状态</span><strong>{file.objectStatus || "—"}</strong></div>
          <div><span>源文件</span><strong>{file.sourceDeletedAt ? "已清理" : "仍在本机"}</strong></div>
        </div>
        <div className="modal-foot">
          {file.assetId && (
            <button className="secondary-button" onClick={() => onOpenAsset(file.assetId)}>
              <ExternalLink size={15} />打开素材详情
            </button>
          )}
          <a className="primary-button" href={`/api/archive/files/${file.id}/download`}>
            <Download size={15} />下载原文件
          </a>
        </div>
      </div>
    </div>
  );
}

function ArchiveFileList({ batches, onOpenAsset }) {
  const [files, setFiles] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [batchId, setBatchId] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("created_desc");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const pageSize = 30;

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize), sort });
      if (batchId) params.set("batchId", batchId);
      if (kind) params.set("kind", kind);
      if (status) params.set("status", status);
      if (query.trim()) params.set("q", query.trim());
      const payload = await archiveFetch(`/archive/files?${params.toString()}`);
      setFiles(payload.files || []);
      setTotal(payload.total || 0);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, [batchId, kind, page, query, sort, status]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="archive-files">
      <div className="archive-file-toolbar">
        <label className="archive-search-box">
          <Search size={15} />
          <input value={query} placeholder="搜索文件名或相对路径" onChange={(event) => { setQuery(event.target.value); setPage(1); }} />
          {query && <button onClick={() => setQuery("")} aria-label="清除搜索"><X size={13} /></button>}
        </label>
        <select value={batchId} onChange={(event) => { setBatchId(event.target.value); setPage(1); }}>
          <option value="">全部批次</option>
          {batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}
        </select>
        <select value={kind} onChange={(event) => { setKind(event.target.value); setPage(1); }}>
          <option value="">全部类型</option>
          {Object.entries(ARCHIVE_KIND_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
          <option value="">全部状态</option>
          {Object.entries(FILE_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select value={sort} onChange={(event) => setSort(event.target.value)}>
          {SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </div>
      {error && <div className="inline-error">{error}</div>}
      {loading ? (
        <div className="empty-state"><div className="empty-icon"><FolderOpen size={22} /></div><h3>正在加载工作文档</h3></div>
      ) : files.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon"><FileText size={22} /></div>
          <h3>还没有归档文件</h3>
          <p>先在上方扫描本地目录并创建归档批次。</p>
        </div>
      ) : (
        <div className="archive-file-list">
          {files.map((file) => (
            <article className="archive-file-row" key={file.id}>
              <div className="archive-file-main">
                <span className={`archive-file-kind archive-kind-${file.kind}`}>{ARCHIVE_KIND_LABELS[file.kind] || file.kind}</span>
                <div className="archive-file-copy">
                  <button className="archive-file-name" onClick={() => setSelected(file)} title={file.relativePath}>{file.name}</button>
                  <span className="archive-file-path">{file.batchName} / {file.relativePath}</span>
                </div>
              </div>
              <div className="archive-file-meta">
                <span>{file.size}</span>
                <span title={file.sha256}>SHA {shortSha(file.sha256)}</span>
                <span>{FILE_STATUS_LABELS[file.status] || file.status}</span>
                <span>{file.sourceDeletedAt ? "源文件已清理" : "源文件保留"}</span>
              </div>
              <div className="archive-file-actions">
                {file.assetId && (
                  <button onClick={() => onOpenAsset(file.assetId)} title="打开素材详情"><ExternalLink size={15} /></button>
                )}
                <a href={`/api/archive/files/${file.id}/download`} title="下载原文件"><Download size={15} /></a>
                <button onClick={() => setSelected(file)} title="预览与详情"><Info size={15} /></button>
              </div>
            </article>
          ))}
        </div>
      )}
      {total > pageSize && (
        <div className="archive-pager">
          <button className="secondary-button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
          <span>第 {page} / {Math.ceil(total / pageSize)} 页，共 {total} 个文件</span>
          <button className="secondary-button" disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage((value) => value + 1)}>下一页</button>
        </div>
      )}
      {selected && <ArchiveFilePreview file={selected} onClose={() => setSelected(null)} onOpenAsset={onOpenAsset} />}
    </div>
  );
}

// 旧版对象存储归档：只适用于必须放 COS 的小文件，视频备份请走百度网盘。
function CosArchiveSection({ onOpenAsset }) {
  const [options, setOptions] = useState(null);
  const [batches, setBatches] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);

  const loadBatches = useCallback(async (quiet = false) => {
    if (!quiet) setBusy(true);
    try {
      const [optionsPayload, batchesPayload] = await Promise.all([
        archiveFetch("/archive/options"),
        archiveFetch("/archive/batches"),
      ]);
      setOptions(optionsPayload.options);
      setBatches(batchesPayload.batches || []);
      setError("");
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setBusy(false);
    }
  }, []);

  const loadDetail = useCallback(async (batchId, quiet = false) => {
    if (!batchId) {
      setDetail(null);
      return;
    }
    try {
      const payload = await archiveFetch(`/archive/batches/${batchId}`);
      setDetail(payload.batch);
      if (!quiet) setError("");
    } catch (loadError) {
      if (!quiet) setError(loadError.message);
    }
  }, []);

  useEffect(() => {
    loadBatches();
  }, [loadBatches]);

  // 有进行中的批次时轮询进度，避免长时间占用一个 HTTP 请求。
  useEffect(() => {
    const hasActive = batches.some((batch) => ["running", "queued"].includes(batch.status));
    if (!hasActive) return undefined;
    pollRef.current = window.setInterval(() => {
      loadBatches(true);
      if (selectedId) loadDetail(selectedId, true);
    }, 3000);
    return () => window.clearInterval(pollRef.current);
  }, [batches, loadBatches, loadDetail, selectedId]);

  const selectBatch = async (batchId) => {
    setSelectedId(batchId);
    await loadDetail(batchId);
  };

  const handleAction = async (batch, action) => {
    setBusy(true);
    setError("");
    try {
      await archiveFetch(`/archive/batches/${batch.id}/${action}`, { method: "POST", body: JSON.stringify({}) });
      await loadBatches(true);
      await loadDetail(batch.id, true);
    } catch (actionError) {
      setError(actionError.message);
    } finally {
      setBusy(false);
    }
  };

  const handleBatchCreated = async (batch) => {
    await loadBatches(true);
    await selectBatch(batch.id);
  };

  return (
    <div className="archive-panel">
      {error && <div className="inline-error">{error}</div>}
      {!options?.allowedRoots?.length && (
        <div className="inline-error">
          服务端未配置 ARCHIVE_ALLOWED_SOURCE_ROOTS，本地目录归档功能处于关闭状态。请在服务端 .env 中配置后重启 API。
        </div>
      )}
      <section className="archive-card">
        <div className="archive-card-head">
          <div>
            <h2><Archive size={17} />扫描本地目录</h2>
            <p>只读取文件信息；创建批次后由服务端后台异步上传，可随时暂停继续。</p>
          </div>
          <button className="secondary-button" onClick={() => loadBatches()} disabled={busy}>
            <RefreshCw size={15} />刷新
          </button>
        </div>
        <ArchiveScanPanel options={options} onBatchCreated={handleBatchCreated} defaultPath={options?.allowedRoots?.[0] || ""} />
      </section>
      <section className="archive-card">
        <div className="archive-card-head">
          <div>
            <h2><FolderOpen size={17} />归档批次</h2>
            <p>查看进度、暂停继续、重试失败项、重新校验，全部通过后才能清理本机源文件。</p>
          </div>
        </div>
        {batches.length === 0 ? (
          <div className="archive-empty"><Archive size={20} /><p>还没有归档批次。先扫描一个本地目录开始。</p></div>
        ) : (
          <div className="archive-batch-list">
            {batches.map((batch) => (
              <ArchiveBatchCard
                key={batch.id}
                batch={batch.id === selectedId && detail ? { ...batch, ...detail } : batch}
                active={batch.id === selectedId}
                onSelect={selectBatch}
                onAction={handleAction}
                busy={busy}
              />
            ))}
          </div>
        )}
        {detail && (
          <ArchiveBatchDetail
            batch={detail}
            onRefresh={async () => {
              await loadBatches(true);
              await loadDetail(detail.id, true);
            }}
          />
        )}
      </section>
      <section className="archive-card">
        <div className="archive-card-head">
          <div>
            <h2><FileText size={17} />工作文档</h2>
            <p>图片和视频同时出现在素材库；Markdown、JSON、脚本等保留原始目录结构，可在线预览和下载。</p>
          </div>
        </div>
        <ArchiveFileList batches={batches} onOpenAsset={onOpenAsset} />
      </section>
    </div>
  );
}
// /documents 的默认视图：网盘目录浏览器。COS/local 归档降级为折叠的旧版入口。
export default function DocumentsPanel({ onOpenAsset }) {
  const [showCos, setShowCos] = useState(false);
  return (
    <>
      <section className="archive-card netdisk-card">
        <div className="archive-card-head">
          <div>
            <h2><Cloud size={17} />百度网盘目录</h2>
            <p>
              创作目录里的视频和素材由本机 MCP/CLI 直接同步到百度网盘，工作台只渲染网盘目录。
              上传和下载都发生在本机与网盘之间，文件字节不会经过工作台服务器。
            </p>
          </div>
        </div>
        <NetdiskBrowser />
      </section>
      <section className="archive-card netdisk-legacy">
        <button className="netdisk-legacy-toggle" onClick={() => setShowCos((value) => !value)} aria-expanded={showCos}>
          <Archive size={16} />
          <span>
            <strong>旧版对象存储归档（COS / 本地目录）</strong>
            <small>仅适合必须放对象存储的小文件。视频备份请用百度网盘，不要走这条链路。</small>
          </span>
          <ChevronDown size={16} className={showCos ? "rotated" : ""} />
        </button>
        {showCos ? <CosArchiveSection onOpenAsset={onOpenAsset} /> : null}
      </section>
    </>
  );
}

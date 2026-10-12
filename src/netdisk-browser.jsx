import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  FileText,
  Folder,
  FolderOpen,
  Info,
  KeyRound,
  RefreshCw,
  Search,
  ShieldCheck,
  Terminal,
  X,
} from "lucide-react";

const API_BASE = "/api";

const KIND_LABELS = {
  directory: "目录",
  image: "图片",
  video: "视频",
  audio: "音频",
  document: "文档",
  archive: "压缩包",
  other: "其他",
};

async function netdiskFetch(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(payload?.error || "请求失败，请稍后重试");
    error.errorCode = payload?.errorCode;
    error.payload = payload;
    throw error;
  }
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

function remainingLabel(expiresInSec) {
  if (expiresInSec === null || expiresInSec === undefined) return "有效期未知";
  if (expiresInSec <= 0) return "已过期";
  const days = Math.floor(expiresInSec / 86400);
  const hours = Math.floor((expiresInSec % 86400) / 3600);
  if (days > 0) return `剩余约 ${days} 天`;
  if (hours > 0) return `剩余约 ${hours} 小时`;
  return "剩余不足 1 小时";
}

function copyText(value) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  const input = document.createElement("textarea");
  input.value = value;
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  document.execCommand("copy");
  input.remove();
  return Promise.resolve(true);
}

function CommandBlock({ label, command }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await copyText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch (_) {
      setCopied(false);
    }
  };
  return (
    <div className="netdisk-command">
      <div className="netdisk-command-head">
        <span>{label}</span>
        <button onClick={handleCopy}>
          <Copy size={13} />
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <code>{command}</code>
    </div>
  );
}

// 百度网盘授权配置区。始终展示：已配置时也要能看到状态和「更新授权」，
// 否则过期后用户找不到入口。Token 只存在组件 state 里，保存后立即清空。
function CredentialPanel({ options, credential, onSaved, onChanged }) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const configured = Boolean(credential?.configured);
  const status = credential?.status || "none";
  const expired = status === "expired";
  const invalid = status === "invalid";
  const active = status === "active";
  const encryptionMissing = options?.encryptionConfigured === false;

  const save = async () => {
    if (!value.trim()) {
      setError("请先粘贴 access_token 或授权成功后的整段地址");
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload = await netdiskFetch("/netdisk/credential", { method: "PUT", body: JSON.stringify({ credential: value }) });
      setValue("");            // 保存后立刻清空，不在内存里多留
      setMessage("授权已保存，正在读取网盘目录…");
      onChanged(payload.credential);
      await onSaved();
    } catch (saveError) {
      setError(saveError.message);
      if (saveError.payload?.credential) onChanged(saveError.payload.credential);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setRemoving(true);
    setError("");
    try {
      const payload = await netdiskFetch("/netdisk/credential", { method: "DELETE" });
      setValue("");
      setMessage("已移除网盘授权，已缓存的目录仍可浏览。");
      onChanged(payload.credential);
      await onSaved();
    } catch (removeError) {
      setError(removeError.message);
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div className={`netdisk-credential ${active ? "is-active" : expired || invalid ? "is-broken" : ""}`}>
      <div className="netdisk-credential-head">
        <div className="netdisk-credential-title">
          <KeyRound size={15} />
          <strong>百度网盘授权</strong>
          {active && <span className="netdisk-badge badge-ok">已连接</span>}
          {expired && <span className="netdisk-badge badge-warn">已过期</span>}
          {invalid && <span className="netdisk-badge badge-bad">已失效</span>}
          {!configured && <span className="netdisk-badge">未配置</span>}
        </div>
        {options?.authorizeUrl && (
          <a className="secondary-button" href={options.authorizeUrl} target="_blank" rel="noreferrer">
            <ExternalLink size={14} />打开百度授权页
          </a>
        )}
      </div>

      <p className="netdisk-purpose">
        <ShieldCheck size={14} />
        {credential?.purpose || options?.purpose || "仅用于读取百度网盘 /AIGC 目录列表，不会用此 Token 上传、下载或删除文件。"}
      </p>

      {configured && (
        <div className="netdisk-credential-status">
          <div><span>Token 后四位</span><strong>····{credential.tokenLast4 || "----"}</strong></div>
          <div><span>有效期至</span><strong>{formatDate(credential.expiresAt)}</strong></div>
          <div><span>剩余</span><strong>{remainingLabel(credential.expiresInSec)}</strong></div>
          {credential.scope && <div><span>授权范围</span><strong>{credential.scope}</strong></div>}
          {credential.lastVerifiedAt && <div><span>最近验证</span><strong>{formatDate(credential.lastVerifiedAt)}</strong></div>}
        </div>
      )}

      {active && credential?.expiringSoon && (
        <p className="netdisk-note warn">授权即将过期（{remainingLabel(credential.expiresInSec)}），建议尽快重新授权。</p>
      )}
      {(expired || invalid) && (
        <div className="netdisk-reauth">
          <p>
            {expired ? "百度网盘授权已过期。" : "百度返回了授权失效（errno=111）。"}
            百度当前是隐式授权，没有可用的 refresh_token，无法自动续期，请按下面三步重新授权：
          </p>
          <ol>
            <li>点「打开百度授权页」并登录</li>
            <li>跳到 <code>login_success</code> 后复制地址栏的<strong>整段链接</strong></li>
            <li>粘贴到下面输入框保存，会覆盖旧 Token</li>
          </ol>
        </div>
      )}
      {encryptionMissing && (
        <p className="netdisk-note bad">服务器未配置 NETDISK_TOKEN_ENCRYPTION_KEY，无法保存授权。请在服务端 .env 里设置 32 字节 Base64 密钥后重启 API。</p>
      )}

      <div className="netdisk-credential-form">
        <input
          type="password"
          value={value}
          autoComplete="off"
          spellCheck="false"
          placeholder="粘贴授权成功后地址栏的整段链接，或只粘贴 access_token"
          onChange={(event) => setValue(event.target.value)}
          disabled={saving || encryptionMissing}
        />
        <button className="primary-button" onClick={save} disabled={saving || !value.trim() || encryptionMissing}>
          <Check size={15} />
          {saving ? "验证中…" : configured ? "更新授权" : "保存授权"}
        </button>
        {configured && (
          <button className="secondary-button" onClick={remove} disabled={removing}>
            {removing ? "移除中…" : "移除"}
          </button>
        )}
      </div>
      {error && <div className="inline-error">{error}</div>}
      {message && !error && <div className="inline-success">{message}</div>}
    </div>
  );
}

function NetdiskNodeDetail({ node, commands, onClose }) {
  const [copied, setCopied] = useState(false);
  if (!node) return null;
  const uploadHint = commands.find((item) => item.id === "upload");
  return (
    <div className="modal-layer netdisk-modal-layer" onClick={onClose}>
      <div className="netdisk-detail-modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div>
            <h3>{node.name}</h3>
            <p>{node.path}</p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </div>
        <div className="netdisk-detail-grid">
          <div><span>类型</span><strong>{KIND_LABELS[node.kind] || node.kind}</strong></div>
          <div><span>大小</span><strong>{formatBytes(node.sizeBytes)}</strong></div>
          <div><span>网盘修改时间</span><strong>{formatDate(node.serverMtime)}</strong></div>
          <div><span>所在目录</span><strong>{node.parentPath || "/"}</strong></div>
          <div className="wide"><span>网盘路径</span><strong className="mono">{node.path}</strong></div>
        </div>
        <p className="netdisk-note">
          原片只存在百度网盘，工作台不保存也不中转文件字节，网页里不提供播放和下载。
          本机 CLI 目前还没有下载命令，需要取回时请先在百度网盘网页端下载。
        </p>
        {uploadHint && (
          <div className="netdisk-download-hint">
            <div className="netdisk-command-head">
              <span>把本机目录同步到这个网盘位置</span>
              <button
                onClick={async () => {
                  try {
                    await copyText(uploadHint.command);
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1800);
                  } catch (_) {}
                }}
              >
                <Copy size={13} />
                {copied ? "已复制" : "复制命令"}
              </button>
            </div>
            <code>{uploadHint.command}</code>
          </div>
        )}
      </div>
    </div>
  );
}

export default function NetdiskBrowser() {
  const [options, setOptions] = useState(null);
  const [credential, setCredential] = useState(null);
  const [currentPath, setCurrentPath] = useState("/AIGC");
  const [layer, setLayer] = useState(null);
  const [stats, setStats] = useState(null);
  const [commands, setCommands] = useState([]);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadOptions = useCallback(async () => {
    try {
      const payload = await netdiskFetch("/netdisk/options");
      setOptions(payload.options);
      setCredential(payload.options.credential || null);
      setCurrentPath((value) => (value === "/AIGC" ? payload.options.rootPath : value));
    } catch (optionError) {
      setError(optionError.message);
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      setStats(await netdiskFetch("/netdisk/stats"));
    } catch (_) {
      setStats(null);
    }
  }, []);

  const loadCommands = useCallback(async () => {
    try {
      setCommands((await netdiskFetch("/netdisk/sync-commands")).commands || []);
    } catch (_) {
      setCommands([]);
    }
  }, []);

  // 每次进入/刷新都只向百度请求当前这一层
  const loadLayer = useCallback(async (targetPath, { quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    setError("");
    try {
      const payload = await netdiskFetch(`/netdisk/children?path=${encodeURIComponent(targetPath)}`);
      setLayer(payload);
      if (payload.credential) setCredential(payload.credential);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOptions();
    loadStats();
    loadCommands();
  }, [loadCommands, loadOptions, loadStats]);

  useEffect(() => {
    loadLayer(currentPath);
  }, [currentPath, loadLayer]);

  useEffect(() => {
    if (!query.trim()) {
      setSearchResults(null);
      return undefined;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ q: query.trim() });
      if (kind) params.set("kind", kind);
      // 搜索只查本地缓存，不打百度
      netdiskFetch(`/netdisk/search?${params.toString()}`)
        .then((payload) => active && setSearchResults(payload.nodes || []))
        .catch(() => active && setSearchResults([]));
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [kind, query]);

  const rootPath = options?.rootPath || "/AIGC";

  const crumbs = useMemo(() => {
    const parts = currentPath.split("/").filter(Boolean);
    return [{ name: rootPath, path: rootPath }, ...parts.slice(1).map((part, index) => ({ name: part, path: `/${parts.slice(0, index + 2).join("/")}` }))];
  }, [currentPath, rootPath]);

  const nodes = layer?.nodes || [];
  // 类型筛选对当前层和搜索结果同样生效
  const visibleNodes = useMemo(() => {
    const source = searchResults || nodes;
    return source.filter((node) => !kind || node.kind === kind);
  }, [kind, nodes, searchResults]);

  const kindOptions = useMemo(() => {
    const counts = new Set();
    nodes.forEach((node) => counts.add(node.kind));
    return [...counts].filter(Boolean);
  }, [nodes]);

  const needsCredential = layer?.errorCode === "need_credential" || (!credential?.configured && nodes.length === 0);
  const credentialBroken = !needsCredential && (layer?.errorCode === "token_invalid" || layer?.errorCode === "credential_expired" || credential?.status === "expired" || credential?.status === "invalid");
  const sourceLabel = layer?.source === "live" ? "实时" : layer?.source === "cache" ? "缓存（网盘暂不可用）" : "";

  return (
    <div className="netdisk-browser">
      <CredentialPanel
        options={options}
        credential={credential}
        onChanged={setCredential}
        onSaved={async () => {
          await loadLayer(currentPath, { quiet: true });
          await loadStats();
        }}
      />

      {error && <div className="inline-error">{error}</div>}
      {layer?.warning && <div className="netdisk-inline-warning">{layer.warning}</div>}

      <div className="netdisk-toolbar">
        <nav className="netdisk-breadcrumb" aria-label="网盘目录">
          {crumbs.map((crumb, index) => (
            <span key={crumb.path}>
              {index > 0 && <ChevronRight size={13} />}
              <button
                className={index === crumbs.length - 1 ? "current" : ""}
                onClick={() => {
                  setQuery("");
                  setCurrentPath(crumb.path);
                }}
              >
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>
        <div className="netdisk-toolbar-actions">
          {sourceLabel && (
            <span className={`netdisk-source ${layer.source}`}>
              <i />
              {sourceLabel}
              {layer.fetchedAt ? ` · ${new Date(layer.fetchedAt).toLocaleTimeString("zh-CN", { hour12: false })}` : ""}
            </span>
          )}
          <label className="netdisk-search">
            <Search size={15} />
            <input value={query} placeholder="搜索已缓存的文件名" onChange={(event) => setQuery(event.target.value)} />
            {query && <button onClick={() => setQuery("")} aria-label="清除搜索"><X size={13} /></button>}
          </label>
          {kindOptions.length > 0 && (
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="">全部类型</option>
              {kindOptions.map((option) => (
                <option key={option} value={option}>{KIND_LABELS[option] || option}</option>
              ))}
            </select>
          )}
          <button
            className="secondary-button"
            onClick={() => {
              loadLayer(currentPath);
              loadStats();
            }}
          >
            <RefreshCw size={15} />刷新
          </button>
        </div>
      </div>

      {stats && (stats.byKind?.length > 0 || stats.cachedNodeCount > 0) && (
        <div className="netdisk-summary">
          <div><span>缓存节点</span><strong>{stats.cachedNodeCount}</strong></div>
          <div><span>缓存更新</span><strong>{formatDate(stats.cacheUpdatedAt)}</strong></div>
          <div>
            <span>文件类型</span>
            <strong>{stats.byKind?.length ? stats.byKind.map((item) => `${KIND_LABELS[item.kind] || item.kind} ${item.count}`).join(" · ") : "—"}</strong>
          </div>
          <div><span>说明</span><strong>统计基于缓存</strong></div>
        </div>
      )}

      {stats?.byKind?.length > 0 && !query && (
        <div className="netdisk-kind-row">
          {stats.byKind.map((item) => (
            <button
              key={item.kind}
              className={`netdisk-kind-chip ${kind === item.kind ? "active" : ""}`}
              onClick={() => setKind(kind === item.kind ? "" : item.kind)}
            >
              {KIND_LABELS[item.kind] || item.kind} {item.count} · {formatBytes(item.bytes)}
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="empty-state">
          <div className="empty-icon"><FolderOpen size={22} /></div>
          <h3>正在读取网盘目录</h3>
        </div>
      ) : visibleNodes.length === 0 ? (
        <div className="netdisk-empty">
          <div className="empty-icon"><Folder size={22} /></div>
          <h3>
            {query
              ? "没有匹配的文件"
              : needsCredential
                ? "还没有配置百度网盘授权"
                : credentialBroken
                  ? "授权已过期，无法读取网盘"
                  : "这个目录是空的"}
          </h3>
          <p>
            {query
              ? "搜索只查已缓存的目录节点，换个关键词或清除类型筛选。"
              : needsCredential
                ? "在上方保存百度网盘授权后，这个页面会实时显示网盘 /AIGC 的目录结构。"
                : credentialBroken
                  ? "重新授权后即可继续实时浏览；下方若有列表则来自上次缓存。"
                  : "网盘上这个目录暂时没有文件。"}
          </p>
        </div>
      ) : (
        <div className="netdisk-file-list">
          {visibleNodes.map((node) => (
            <article className="netdisk-file-row" key={node.path}>
              <button
                className="netdisk-file-name"
                onClick={() => (node.isDir ? (setQuery(""), setCurrentPath(node.path)) : setSelected(node))}
                title={node.path}
              >
                {node.isDir ? <Folder size={16} /> : <FileText size={16} />}
                <span>{node.name}</span>
              </button>
              <span className={`netdisk-kind archive-kind-${node.kind}`}>{KIND_LABELS[node.kind] || node.kind}</span>
              <span className="netdisk-size">{node.isDir ? "—" : formatBytes(node.sizeBytes)}</span>
              <span className="netdisk-mtime">{formatDate(node.serverMtime)}</span>
              {!node.isDir && (
                <button className="netdisk-row-action" onClick={() => setSelected(node)} aria-label="查看详情" title="查看详情">
                  <Info size={15} />
                </button>
              )}
            </article>
          ))}
        </div>
      )}

      {commands.length > 0 && (
        <details className="netdisk-commands">
          <summary><Terminal size={15} />本机上传 / 下载命令（辅助，不影响上面的浏览）</summary>
          <p>上传和下载只在本机与百度网盘之间进行。把视频从网盘取回本机也用同一条命令。</p>
          {commands.filter((item) => item.id !== "publish-api").map((command) => (
            <CommandBlock key={command.id} label={command.label} command={command.command} />
          ))}
        </details>
      )}

      {selected && <NetdiskNodeDetail node={selected} commands={commands} onClose={() => setSelected(null)} />}
    </div>
  );
}

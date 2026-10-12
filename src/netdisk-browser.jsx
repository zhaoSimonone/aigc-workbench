import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronRight,
  Copy,
  Download,
  FileText,
  Folder,
  FolderOpen,
  Info,
  RefreshCw,
  Search,
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

function CommandBlock({ label, command, onCopied }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await copyText(command);
      setCopied(true);
      onCopied?.();
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
          <Copy size={13} />{copied ? "已复制" : "复制命令"}
        </button>
      </div>
      <code>{command}</code>
    </div>
  );
}

function NetdiskNodeDetail({ node, commands, onClose }) {
  const [copied, setCopied] = useState(false);
  if (!node) return null;
  const downloadHint = commands?.length
    ? commands.find((item) => item.id === "download")
    : null;
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
          工作台只保存网盘目录元数据，不保存也不中转文件字节。视频原片留在百度网盘，需要下载时在本机用 MCP 取回。
        </p>
        {downloadHint ? (
          <div className="netdisk-download-hint">
            <div className="netdisk-download-hint-head"><Download size={14} />在本机下载</div>
            <code>{downloadHint.command}</code>
            <button
              className="secondary-button"
              onClick={async () => {
                try {
                  await copyText(downloadHint.command);
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1800);
                } catch (_) {}
              }}
            >
              <Copy size={14} />{copied ? "已复制命令" : "复制下载命令"}
            </button>
          </div>
        ) : (
          <p className="netdisk-note">下载能力尚未在本机 Skill 中启用，第一期只提供目录浏览。</p>
        )}
      </div>
    </div>
  );
}

// 网盘目录浏览器：只读渲染本机 MCP 同步上来的目录快照。
export default function NetdiskBrowser() {
  const [rootPath, setRootPath] = useState("/AIGC");
  const [currentPath, setCurrentPath] = useState("/AIGC");
  const [nodes, setNodes] = useState([]);
  const [snapshot, setSnapshot] = useState(null);
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
      setRootPath(payload.options.rootPath);
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
      const payload = await netdiskFetch("/netdisk/sync-commands");
      setCommands(payload.commands || []);
    } catch (_) {
      setCommands([]);
    }
  }, []);

  const loadChildren = useCallback(async (targetPath) => {
    setLoading(true);
    setError("");
    try {
      const payload = await netdiskFetch(`/netdisk/children?path=${encodeURIComponent(targetPath)}`);
      setNodes(payload.nodes || []);
      setSnapshot(payload.snapshot || null);
    } catch (loadError) {
      setError(loadError.message);
      setNodes([]);
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
    loadChildren(currentPath);
  }, [currentPath, loadChildren]);

  useEffect(() => {
    if (!query.trim()) {
      setSearchResults(null);
      return undefined;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ q: query.trim() });
      if (kind) params.set("kind", kind);
      netdiskFetch(`/netdisk/search?${params.toString()}`)
        .then((payload) => active && setSearchResults(payload.nodes || []))
        .catch(() => active && setSearchResults([]));
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [kind, query]);

  const crumbs = useMemo(() => {
    const parts = currentPath.split("/").filter(Boolean);
    return [{ name: rootPath, path: rootPath }, ...parts.slice(1).map((part, index) => ({ name: part, path: `/${parts.slice(0, index + 2).join("/")}` }))];
  }, [currentPath, rootPath]);

  const visibleNodes = useMemo(() => {
    const source = searchResults || nodes;
    return source.filter((node) => (searchResults ? !kind || node.kind === kind : true));
  }, [kind, nodes, searchResults]);

  const kindOptions = useMemo(() => {
    const counts = new Map();
    nodes.forEach((node) => counts.set(node.kind, (counts.get(node.kind) || 0) + 1));
    return [...counts.keys()].filter(Boolean);
  }, [nodes]);

  const needsSync = !snapshot;

  return (
    <div className="netdisk-browser">
      {error && <div className="inline-error">{error}</div>}

      <div className="netdisk-toolbar">
        <nav className="netdisk-breadcrumb" aria-label="网盘目录">
          {crumbs.map((crumb, index) => (
            <span key={crumb.path}>
              {index > 0 && <ChevronRight size={13} />}
              <button className={index === crumbs.length - 1 ? "current" : ""} onClick={() => { setQuery(""); setCurrentPath(crumb.path); }}>
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>
        <div className="netdisk-toolbar-actions">
          <label className="netdisk-search">
            <Search size={15} />
            <input
              value={query}
              placeholder="搜索网盘文件名"
              onChange={(event) => setQuery(event.target.value)}
            />
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
          <button className="secondary-button" onClick={() => { loadChildren(currentPath); loadStats(); }}>
            <RefreshCw size={15} />刷新
          </button>
        </div>
      </div>

      {snapshot && (
        <div className="netdisk-summary">
          <div><span>最近同步</span><strong>{formatDate(snapshot.createdAt)}</strong></div>
          <div><span>来源</span><strong>{snapshot.source === "mcp" ? "本机 MCP" : "本机 CLI"}</strong></div>
          <div><span>目录数</span><strong>{snapshot.dirCount}</strong></div>
          <div><span>文件数</span><strong>{snapshot.fileCount}</strong></div>
          <div><span>总大小</span><strong>{formatBytes(snapshot.totalBytes)}</strong></div>
        </div>
      )}

      {stats && stats.byKind?.length > 0 && !query && (
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
          <h3>{query ? "没有匹配的文件" : needsSync ? "还没有网盘目录快照" : "这个目录是空的"}</h3>
          {needsSync ? (
            <>
              <p>工作台只渲染网盘目录，不会替你上传文件。请在本机用下面的命令同步目录，文件字节不会经过工作台服务器。</p>
            </>
          ) : (
            <p>{query ? "换个关键词试试，或清除类型筛选。" : "网盘上这个目录暂时没有文件。"}</p>
          )}
        </div>
      ) : (
        <div className="netdisk-file-list">
          {visibleNodes.map((node) => (
            <article className="netdisk-file-row" key={node.path}>
              <button
                className="netdisk-file-name"
                onClick={() => { if (node.isDir) { setQuery(""); setCurrentPath(node.path); } else { setSelected(node); } }}
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

      {needsSync && commands.length > 0 && (
        <div className="netdisk-commands">
          <h3><Terminal size={15} />在本机执行（不上传到工作台）</h3>
          <p>先 dry-run 核对清单，再去掉 --dry-run 真正同步。Token 只存在本机，不进入服务器和数据库。</p>
          {commands.map((command) => (
            <CommandBlock key={command.id} label={command.label} command={command.command} />
          ))}
        </div>
      )}

      {selected && <NetdiskNodeDetail node={selected} commands={commands} onClose={() => setSelected(null)} />}
    </div>
  );
}